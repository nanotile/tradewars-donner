"""End-to-end arena integration test.

Runs a short (~90s) real arena with 4 OpenRouter traders on the cheap
`openai/gpt-oss-120b` model, real Massive prices, real MCPs. Validates the
full Phase 4 backend: 4 concurrent trader loops, tick snapshots, end-of-game
liquidation, game history persisted, structured events flowed through the
arena event queue.

Opt-in only (marked `integration`): `uv run pytest -m integration`.
Needs MASSIVE_API_KEY and OPENROUTER_API_KEY in env. Needs `mcp_massive`
and `npx` on PATH.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from backend.arena.arena import Arena, ArenaConfig
from backend.environment.accounts import Accounts
from backend.environment.prices import Prices
from backend.traders.models import TraderConfig

ARENA_DURATION_SECONDS = 90.0

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (os.getenv("MASSIVE_API_KEY") and os.getenv("OPENROUTER_API_KEY")),
        reason="MASSIVE_API_KEY or OPENROUTER_API_KEY not set",
    ),
    pytest.mark.skipif(
        shutil.which("mcp_massive") is None or shutil.which("npx") is None,
        reason="mcp_massive or npx not on PATH",
    ),
]


def _cheap_trader(i: int) -> TraderConfig:
    """All 4 traders run the same cheap model via OpenRouter — we're testing
    plumbing, not trading quality. gpt-oss-120b is cheap and responsive."""
    return TraderConfig(
        id=f"trader{i}",
        display_name=f"Trader{i}",
        provider="openrouter",
        model="openai/gpt-oss-120b",
        reasoning={"effort": "low"},
        max_tokens=4000,
    )


async def test_short_arena_end_to_end(tmp_path: Path):
    config = ArenaConfig(
        duration_seconds=ARENA_DURATION_SECONDS,
        traders=[_cheap_trader(i) for i in range(4)],
    )
    db_path = tmp_path / "arena.sqlite"
    accounts = Accounts(db_path)
    prices = Prices()

    try:
        arena = Arena(config=config, accounts=accounts, prices=prices)
        await arena.start()

        # All four trader tasks should be alive.
        assert len(arena._tasks) == 4
        for t in arena._tasks:
            assert not t.done(), f"{t.get_name()} exited prematurely"

        # Let traders run, ticking periodically the way the UI will.
        mid_snapshots = []
        ticks = int(ARENA_DURATION_SECONDS // 20)
        for _ in range(ticks):
            await asyncio.sleep(20)
            snap = await arena.tick()
            mid_snapshots.append(snap)
            assert snap.running is True
            assert len(snap.traders) == 4

        assert mid_snapshots, "no mid-arena ticks collected"

        # Wind down.
        final = await arena.end()
        assert final.running is False
        assert len(final.traders) == 4

        # Liquidation wiped all holdings — everyone ends in cash.
        for t in final.traders:
            assert t.holdings == {}, f"{t.trader_id} still holds {list(t.holdings)}"

        # Game history persisted.
        games = accounts.list_games()
        assert len(games) == 1
        assert set(games[0]["final_results"]) == {"trader0", "trader1", "trader2", "trader3"}

        # Event queue drained — should contain events from multiple traders
        # (at minimum, cycle_start events from every trader).
        traders_seen: set[str] = set()
        event_types: set[str] = set()
        while not arena.events.empty():
            e = arena.events.get_nowait()
            traders_seen.add(e.trader_id)
            event_types.add(e.type)
        assert traders_seen == {"trader0", "trader1", "trader2", "trader3"}, (
            f"only saw events from {traders_seen}"
        )
        assert "cycle_start" in event_types
    finally:
        accounts.close()
