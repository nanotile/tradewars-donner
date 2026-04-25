"""FastAPI app fronting the Arena.

Endpoints:
  POST /arena/start    — reset + launch traders, return started snapshot
  POST /arena/stop     — manual end, return final snapshot
  POST /arena/tick     — UI heartbeat, return current snapshot
  GET  /arena/stream   — SSE stream of TraderEvents

Single arena per process. Running state lives in the ArenaHolder (so the
instance can be swapped across games while routes stay bound to the holder).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from backend.arena.arena import Arena, ArenaConfig, DEFAULT_CONFIG_PATH, reasoning_label
from backend.environment.accounts import Accounts
from backend.environment.prices import Prices

# In production (Docker) the built frontend is copied alongside the backend.
# In local dev the frontend lives at frontend/dist after `npm run build`.
_FRONTEND_DIST_CANDIDATES = [
    Path(__file__).resolve().parents[2] / "frontend_dist",
    Path(__file__).resolve().parents[2] / "frontend" / "dist",
]


class StartRequest(BaseModel):
    duration_seconds: float | None = Field(
        default=None,
        gt=0,
        description="Optional override for game length. Falls back to config.",
    )
    max_mode: bool = Field(
        default=False,
        description="True → max-reasoning models; False → eco (cheap) models.",
    )

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "backend" / "environment" / "tradewars.sqlite"


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
        max_mode: bool = False,
    ) -> Arena:
        config = ArenaConfig.load(self.config_path, max_mode=max_mode)
        if duration_override is not None:
            config.duration_seconds = duration_override
        self.arena = Arena(config=config, accounts=self.accounts, prices=self.prices)
        return self.arena

    def require(self) -> Arena:
        if self.arena is None:
            raise HTTPException(status_code=409, detail="Arena has not been started")
        return self.arena


def create_app(holder: ArenaHolder | None = None) -> FastAPI:
    app = FastAPI(title="Tradewars")
    holder = holder or ArenaHolder()
    app.state.arena_holder = holder

    @app.post("/arena/start")
    async def start(body: StartRequest | None = None) -> dict:
        if holder.arena is not None and holder.arena._final_snapshot is None:
            raise HTTPException(status_code=409, detail="Arena already running")
        body = body or StartRequest()
        arena = holder.new_arena(
            duration_override=body.duration_seconds,
            max_mode=body.max_mode,
        )
        await arena.start()
        snap = await arena.tick()
        return asdict(snap)

    @app.get("/arena/config")
    def get_config() -> dict:
        """Return both max and eco variants so the UI can preview the line-up."""
        data = json.loads(Path(holder.config_path).read_text())
        return {
            "duration_seconds": data["duration_seconds"],
            "traders": [
                {
                    "id": t["id"],
                    "max": {
                        "display_name": t["max"]["display_name"],
                        "reasoning_label": reasoning_label(t["max"]["reasoning"]),
                    },
                    "eco": {
                        "display_name": t["eco"]["display_name"],
                        "reasoning_label": reasoning_label(t["eco"]["reasoning"]),
                    },
                }
                for t in data["traders"]
            ],
        }

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
                }

        return EventSourceResponse(gen())

    # Serve the built frontend at / when present. Mount LAST so all /arena/*
    # routes above take precedence; html=True maps GET / → index.html.
    for dist in _FRONTEND_DIST_CANDIDATES:
        if dist.exists():
            app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")
            break

    return app

