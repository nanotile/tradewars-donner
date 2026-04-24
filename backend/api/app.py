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
from sse_starlette.sse import EventSourceResponse

from backend.arena.arena import Arena, ArenaConfig, ArenaSnapshot, DEFAULT_CONFIG_PATH
from backend.environment.accounts import Accounts
from backend.environment.prices import Prices

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

    def new_arena(self) -> Arena:
        self.arena = Arena(
            config=ArenaConfig.load(self.config_path),
            accounts=self.accounts,
            prices=self.prices,
        )
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
    async def start() -> dict:
        if holder.arena is not None and holder.arena._final_snapshot is None:
            raise HTTPException(status_code=409, detail="Arena already running")
        arena = holder.new_arena()
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
                }

        return EventSourceResponse(gen())

    return app

