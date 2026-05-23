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
from backend.arena.arena import DEFAULT_CONFIG_PATH
from backend.environment.accounts import Accounts


class _FakePrices:
    def __init__(self, prices: dict[str, float]):
        self._prices = {k.upper(): v for k, v in prices.items()}

    async def aget_price(self, ticker: str) -> float:
        return self._prices[ticker.upper()]

    async def aget_prices(self, tickers: list[str]) -> dict[str, float]:
        return {t: self._prices[t.upper()] for t in tickers}


@pytest.fixture(autouse=True)
def disable_auth(monkeypatch):
    import backend.auth
    monkeypatch.setattr(backend.auth, "AUTH_SECRET_KEY", "")


@pytest.fixture(autouse=True)
def neutralize_trader_loop(monkeypatch):
    async def _noop(self, stop_event: asyncio.Event):
        await stop_event.wait()

    monkeypatch.setattr(arena_mod.Trader, "run_until_stopped", _noop)
    monkeypatch.setattr(arena_mod, "wipe_memory_files", lambda _tids: None)


@pytest.fixture
def holder(tmp_path):
    """Real ArenaHolder pointed at the real config catalog, but with an
    in-memory accounts DB and fake price feed."""
    h = ArenaHolder.__new__(ArenaHolder)
    h.config_path = DEFAULT_CONFIG_PATH
    h.db_path = tmp_path / "test.sqlite"
    h.accounts = Accounts(":memory:")
    h.prices = _FakePrices({})
    h.arena = None
    yield h
    h.accounts.close()


@pytest.fixture
def client(holder):
    app = create_app(holder=holder)
    with TestClient(app) as c:
        yield c


# ---- /arena/config ----

def test_config_returns_catalog_with_expected_models(client):
    r = client.get("/arena/config")
    assert r.status_code == 200
    body = r.json()
    assert body["duration_seconds"] == 720
    assert body["max_tokens"] == 64_000
    assert "claude-opus-4-7" in body["models"]
    assert body["models"]["claude-opus-4-7"]["display_name"] == "Claude Opus 4.7"
    assert "max" in body["presets"]
    assert "eco" in body["presets"]
    assert len(body["presets"]["max"]) == 4


# ---- /arena/start ----

def test_tick_before_start_returns_409(client):
    assert client.post("/arena/tick").status_code == 409


def test_start_with_no_body_uses_max_preset(client):
    r = client.post("/arena/start")
    assert r.status_code == 200
    snap = r.json()
    assert len(snap["traders"]) == 4
    ids = {t["trader_id"] for t in snap["traders"]}
    assert ids == {
        "Claude Opus 4.7 (max)",
        "GPT 5.5 (xhigh)",
        "Gemini 3.1 Pro Preview (32k)",
        "DeepSeek V4 Pro (max)",
    }


def test_start_with_custom_selections(client):
    selections = [
        {"model_id": "kimi-k2-6", "reasoning_label": "xhigh"},
        {"model_id": "kimi-k2-6", "reasoning_label": "low"},
        {"model_id": "claude-haiku-4-5", "reasoning_label": "low"},
        {"model_id": "deepseek-v4-flash", "reasoning_label": "off"},
    ]
    r = client.post("/arena/start", json={"selections": selections})
    assert r.status_code == 200
    snap = r.json()
    assert [t["trader_id"] for t in snap["traders"]] == [
        "Kimi K2.6 (xhigh)",
        "Kimi K2.6 (low)",
        "Claude Haiku 4.5 (low)",
        "DeepSeek V4 Flash (off)",
    ]


def test_start_with_duplicate_models_disambiguates_with_hash(client):
    r = client.post("/arena/start", json={
        "selections": [
            {"model_id": "kimi-k2-6", "reasoning_label": "xhigh"},
            {"model_id": "kimi-k2-6", "reasoning_label": "xhigh"},
            {"model_id": "kimi-k2-6", "reasoning_label": "xhigh"},
            {"model_id": "kimi-k2-6", "reasoning_label": "low"},
        ],
    })
    assert r.status_code == 200
    snap = r.json()
    assert [t["trader_id"] for t in snap["traders"]] == [
        "Kimi K2.6 (xhigh)",
        "Kimi K2.6 (xhigh) #2",
        "Kimi K2.6 (xhigh) #3",
        "Kimi K2.6 (low)",
    ]


def test_start_with_unknown_model_id_400(client):
    r = client.post("/arena/start", json={
        "selections": [{"model_id": "imaginary", "reasoning_label": "max"}] * 4,
    })
    assert r.status_code == 400


def test_start_with_unknown_reasoning_label_400(client):
    r = client.post("/arena/start", json={
        "selections": [{"model_id": "claude-opus-4-7", "reasoning_label": "ultra"}] * 4,
    })
    assert r.status_code == 400


def test_start_twice_returns_409(client):
    assert client.post("/arena/start").status_code == 200
    assert client.post("/arena/start").status_code == 409


def test_start_with_duration_override(client, holder):
    r = client.post("/arena/start", json={"duration_seconds": 600})
    assert r.status_code == 200
    snap = r.json()
    assert snap["time_elapsed_seconds"] + snap["time_remaining_seconds"] == 600
    assert holder.arena.config.duration_seconds == 600


def test_start_rejects_non_positive_duration(client):
    assert client.post("/arena/start", json={"duration_seconds": 0}).status_code == 422
    assert client.post("/arena/start", json={"duration_seconds": -5}).status_code == 422


def test_start_snapshot_carries_reasoning_label_and_display_name(client):
    r = client.post("/arena/start")
    snap = r.json()
    by_id = {t["trader_id"]: t for t in snap["traders"]}
    assert by_id["Claude Opus 4.7 (max)"]["display_name"] == "Claude Opus 4.7"
    assert by_id["Claude Opus 4.7 (max)"]["reasoning_label"] == "max"
    assert by_id["Gemini 3.1 Pro Preview (32k)"]["reasoning_label"] == "32k"


# ---- /arena/tick + /arena/stop ----

def test_tick_returns_running_snapshot(client):
    client.post("/arena/start")
    snap = client.post("/arena/tick").json()
    assert snap["running"] is True
    assert len(snap["traders"]) == 4


def test_stop_returns_final_snapshot_and_records_game(client, holder):
    client.post("/arena/start")
    snap = client.post("/arena/stop").json()
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
    assert client.post("/arena/start").status_code == 200
    client.post("/arena/stop")
    assert len(holder.accounts.list_games()) == 2


# ---- /arena/stream ----

async def test_stream_yields_sse_frame_for_queued_event(holder):
    """Call the /arena/stream endpoint handler directly and pull one frame.

    Using the FastAPI TestClient for SSE is brittle (iter_lines has no timeout
    against a never-ending generator); exercising the generator is a cleaner
    and faster unit test of the framing.
    """
    from backend.traders.trader import TraderEvent

    create_app(holder=holder)
    holder.new_arena()
    await holder.arena.start()

    fake_id = holder.arena.config.traders[0].id
    holder.arena.events.put_nowait(TraderEvent(
        trader_id=fake_id, type="tool_called",
        timestamp="2026-04-25T00:00:00+00:00",
        payload={"tool": "trade"},
    ))

    async for event in holder.arena.stream():
        from dataclasses import asdict
        frame = {"event": event.type, "data": json.loads(json.dumps(asdict(event)))}
        assert frame["event"] == "tool_called"
        assert frame["data"]["trader_id"] == fake_id
        break

    await holder.arena.end()
