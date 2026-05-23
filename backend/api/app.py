"""FastAPI app fronting the Arena.

Endpoints:
  GET  /arena/config   — model catalog + presets + duration / max_tokens
  POST /arena/start    — reset + launch traders, return started snapshot
  POST /arena/stop     — manual end, return final snapshot
  POST /arena/tick     — UI heartbeat, return current snapshot
  GET  /arena/stream   — SSE stream of TraderEvents
  POST /api/auth/login — JWT login
  GET  /api/auth/me    — validate token
  POST /api/auth/change-password
  GET  /api/admin/users — list users (admin)
  POST /api/admin/users — create user (admin)
  DELETE /api/admin/users/{username} — delete user (admin)

Single arena per process. Running state lives in the ArenaHolder (so the
instance can be swapped across games while routes stay bound to the holder).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sse_starlette.sse import EventSourceResponse

from backend.arena.arena import Arena, ArenaConfig, DEFAULT_CONFIG_PATH
import backend.auth as _auth_mod
from backend.environment.accounts import Accounts
from backend.environment.prices import Prices
from backend.routers.auth_routes import limiter, router as auth_router
from backend.routers.admin_routes import router as admin_router

# In production (Docker) the built frontend is copied alongside the backend.
# In local dev the frontend lives at frontend/dist after `npm run build`.
_FRONTEND_DIST_CANDIDATES = [
    Path(__file__).resolve().parents[2] / "frontend_dist",
    Path(__file__).resolve().parents[2] / "frontend" / "dist",
]

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "backend" / "environment" / "tradewars.sqlite"


class TraderSelection(BaseModel):
    model_id: str = Field(..., description="Key in config.json's `models` map.")
    reasoning_label: str = Field(..., description="Must match one of the model's reasoning_options.label.")


class StartRequest(BaseModel):
    duration_seconds: float | None = Field(
        default=None,
        gt=0,
        description="Optional override for game length. Falls back to config.",
    )
    selections: list[TraderSelection] | None = Field(
        default=None,
        description="One per slot, in order. If omitted, the `max` preset is used.",
    )


class ArenaHolder:
    """Holds the current Arena (if any). Swapped out at each start()."""

    def __init__(
        self,
        config_path: Path = DEFAULT_CONFIG_PATH,
        db_path: Path = DEFAULT_DB_PATH,
    ):
        self.config_path = config_path
        self.db_path = db_path
        self.accounts = Accounts(db_path)
        self.prices = Prices()
        self.arena: Arena | None = None

    def new_arena(
        self,
        *,
        duration_override: float | None = None,
        selections: list[dict] | None = None,
    ) -> Arena:
        config = ArenaConfig.load(self.config_path)
        sel = selections if selections is not None else config.preset_selections("max")
        traders = config.from_selections(sel)
        config = config.with_traders(traders)
        if duration_override is not None:
            config.duration_seconds = duration_override
        self.arena = Arena(config=config, accounts=self.accounts, prices=self.prices)
        return self.arena

    def require(self) -> Arena:
        if self.arena is None:
            raise HTTPException(status_code=409, detail="Arena has not been started")
        return self.arena


_logger = logging.getLogger(__name__)


def create_app(holder: ArenaHolder | None = None) -> FastAPI:
    _auth_mod.check_auth_config()

    app = FastAPI(title="Tradewars")
    holder = holder or ArenaHolder()
    app.state.arena_holder = holder

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "https://tradewars.kentbenson.net",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["Content-Type", "Authorization"],
    )

    # Rate limiter (shared with auth_routes)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Auth + admin routers
    app.include_router(auth_router)
    app.include_router(admin_router)

    # JWT middleware — protects /arena/* routes
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if _auth_mod.DEV_MODE and not _auth_mod.AUTH_SECRET_KEY:
            return await call_next(request)
        path = request.url.path
        if not path.startswith("/arena/"):
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)
        auth_header = request.headers.get("Authorization", "")
        token = None
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        else:
            # SSE EventSource can't send headers — accept token as query param
            token = request.query_params.get("token")
        if not token:
            return JSONResponse(
                status_code=401, content={"detail": "Not authenticated"}
            )
        username = _auth_mod.decode_token(token)
        if not username:
            return JSONResponse(
                status_code=401, content={"detail": "Invalid or expired token"}
            )
        return await call_next(request)

    @app.get("/arena/config")
    def get_config() -> dict:
        """Catalog + presets so the sidebar can populate dropdowns."""
        return json.loads(Path(holder.config_path).read_text())

    @app.post("/arena/start")
    async def start(body: StartRequest | None = None) -> dict:
        if holder.arena is not None and holder.arena._final_snapshot is None:
            await holder.arena.end()
        body = body or StartRequest()
        selections = (
            [s.model_dump() for s in body.selections] if body.selections is not None else None
        )
        try:
            arena = holder.new_arena(
                duration_override=body.duration_seconds,
                selections=selections,
            )
        except KeyError as e:
            raise HTTPException(status_code=400, detail=f"Unknown selection: {e}") from e
        except StopIteration as e:
            raise HTTPException(status_code=400, detail="Unknown reasoning_label for model") from e
        await arena.start()
        snap = await arena.tick()
        return asdict(snap)

    @app.post("/arena/stop")
    async def stop() -> dict:
        arena = holder.require()
        snap = await arena.end()
        return asdict(snap)

    @app.post("/arena/tick")
    async def tick() -> dict:
        arena = holder.require()
        snap = await arena.tick()
        return asdict(snap)

    @app.get("/arena/stream")
    async def stream() -> EventSourceResponse:
        arena = holder.require()

        async def gen():
            async for event in arena.stream():
                yield {
                    "event": event.type,
                    "data": json.dumps(asdict(event)),
                    "retry": 2000,
                }

        return EventSourceResponse(gen())

    # Serve the built frontend at / when present. Mount LAST so all /arena/*
    # routes above take precedence; html=True maps GET / → index.html.
    for dist in _FRONTEND_DIST_CANDIDATES:
        if dist.exists():
            app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")
            break

    return app
