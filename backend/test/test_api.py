"""Tests for the FastAPI /arena endpoints.

No real MCPs, no LLM calls, no live Massive: Trader.run_until_stopped is
neutralized, `Prices` is swapped for a static dict, and the ArenaHolder is
built with those fakes. We exercise start / tick / stop / stream through
FastAPI's TestClient.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from backend.api.app import ArenaHolder, create_app
from backend.arena import arena as arena_mod
from backend.arena.arena import ArenaConfig, DEFAULT_CONFIG_PATH
from backend.environment.accounts import Accounts
from backend.traders.models import TraderConfig


class _FakePrices:
    def __init__(self, prices: dict[str, float]):
        self._prices = {k.upper(): v for k, v in prices.items()}

    async def aget_price(self, ticker: str) -> float:
        return self._prices[ticker.upper()]

    async def aget_prices(self, tickers: list[str]) -> dict[str, float]:
        return {t: self._prices[t.upper()] for t in tickers}


@pytest.fixture(autouse=True)
def neutralize_trader_loop(monkeypatch):
    async def _noop(self, stop_event: asyncio.Event):
        await stop_event.wait()

    monkeypatch.setattr(arena_mod.Trader, "run_until_stopped", _noop)
    monkeypatch.setattr(arena_mod, "wipe_memory_files", lambda _tids: None)


@pytest.fixture
def holder(tmp_path):
    """ArenaHolder backed by in-memory accounts + fake prices + test config."""
    config = ArenaConfig(
        duration_seconds=600.0,
        traders=[
            TraderConfig(
                id=f"t{i}", display_name=f"T{i}",
                provider="openai", model="gpt-5.4",
                reasoning={"effort": "low"}, max_tokens=1000,
            )
            for i in range(4)
        ],
    )
    h = ArenaHolder.__new__(ArenaHolder)
    h.config_path = tmp_path / "unused.json"
    h.db_path = tmp_path / "test.sqlite"
    h.accounts = Accounts(":memory:")
    h.prices = _FakePrices({})
    h.arena = None
    # Override new_arena to use our injected config without reading JSON.
    def _new_arena(*, duration_override=None, max_mode=False):
        from backend.arena.arena import Arena, ArenaConfig
        _ = max_mode  # fake holder always serves the same test config
        cfg = ArenaConfig(
            duration_seconds=duration_override if duration_override is not None else config.duration_seconds,
            traders=config.traders,
        )
        h.arena = Arena(config=cfg, accounts=h.accounts, prices=h.prices)
        return h.arena
    h.new_arena = _new_arena
    h.config_path = DEFAULT_CONFIG_PATH  # /arena/config reads the real JSON
    yield h
    h.accounts.close()


@pytest.fixture
def client(holder):
    app = create_app(holder=holder)
    with TestClient(app) as c:
        yield c


def test_tick_before_start_returns_409(client):
    r = client.post("/arena/tick")
    assert r.status_code == 409


def test_start_returns_initial_snapshot(client):
    r = client.post("/arena/start")
    assert r.status_code == 200
    snap = r.json()
    assert snap["running"] is True
    assert len(snap["traders"]) == 4
    assert all(t["cash"] == 1_000_000.0 for t in snap["traders"])


def test_start_twice_returns_409(client):
    assert client.post("/arena/start").status_code == 200
    r = client.post("/arena/start")
    assert r.status_code == 409


def test_start_with_duration_override(client, holder):
    r = client.post("/arena/start", json={"duration_seconds": 600})
    assert r.status_code == 200
    snap = r.json()
    assert snap["time_elapsed_seconds"] + snap["time_remaining_seconds"] == 600
    assert holder.arena.config.duration_seconds == 600


def test_start_rejects_non_positive_duration(client):
    r = client.post("/arena/start", json={"duration_seconds": 0})
    assert r.status_code == 422
    r = client.post("/arena/start", json={"duration_seconds": -5})
    assert r.status_code == 422


def test_arena_config_returns_both_variants(client):
    r = client.get("/arena/config")
    assert r.status_code == 200
    body = r.json()
    assert len(body["traders"]) == 4
    claude = next(t for t in body["traders"] if t["id"] == "claude")
    assert claude["max"]["display_name"] == "Claude Opus 4.7"
    assert claude["max"]["reasoning_label"] == "max"
    assert claude["eco"]["display_name"] == "Claude Haiku 4.5"
    assert claude["eco"]["reasoning_label"] == "low"
    gemini = next(t for t in body["traders"] if t["id"] == "gemini")
    assert gemini["max"]["reasoning_label"] == "32k"


def test_start_snapshot_carries_reasoning_label(client):
    r = client.post("/arena/start")
    assert r.status_code == 200
    snap = r.json()
    for t in snap["traders"]:
        assert "reasoning_label" in t and t["reasoning_label"]


def test_tick_returns_running_snapshot(client):
    client.post("/arena/start")
    r = client.post("/arena/tick")
    assert r.status_code == 200
    snap = r.json()
    assert snap["running"] is True
    assert len(snap["traders"]) == 4


def test_stop_returns_final_snapshot_and_records_game(client, holder):
    client.post("/arena/start")
    r = client.post("/arena/stop")
    assert r.status_code == 200
    snap = r.json()
    assert snap["running"] is False
    assert len(holder.accounts.list_games()) == 1


def test_stop_is_idempotent(client):
    client.post("/arena/start")
    s1 = client.post("/arena/stop").json()
    s2 = client.post("/arena/stop").json()
    assert s1 == s2


def test_start_after_end_starts_a_new_game(client, holder):
    client.post("/arena/start")
    client.post("/arena/stop")
    # A completed arena should allow a fresh start.
    r = client.post("/arena/start")
    assert r.status_code == 200
    # Two games recorded now.
    client.post("/arena/stop")
    assert len(holder.accounts.list_games()) == 2


async def test_stream_yields_sse_frame_for_queued_event(holder):
    """Call the /arena/stream endpoint handler directly and pull one frame.

    Using the FastAPI TestClient for SSE is brittle (iter_lines has no timeout
    against a never-ending generator); exercising the generator is a cleaner
    and faster unit test of the framing.
    """
    from backend.api.app import create_app
    from backend.traders.trader import TraderEvent

    create_app(holder=holder)  # builds routes; we reach into the arena directly
    holder.new_arena()
    await holder.arena.start()
    holder.arena.events.put_nowait(TraderEvent(
        trader_id="t0", type="tool_called",
        timestamp="2026-04-24T00:00:00+00:00",
        payload={"tool": "trade"},
    ))

    async for event in holder.arena.stream():
        frame = {"event": event.type, "data": json.loads(json.dumps(event_to_dict(event)))}
        assert frame["event"] == "tool_called"
        assert frame["data"]["trader_id"] == "t0"
        assert frame["data"]["payload"]["tool"] == "trade"
        break

    await holder.arena.end()


def event_to_dict(event):
    from dataclasses import asdict
    return asdict(event)
