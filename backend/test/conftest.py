"""Shared test fixtures for the Tradewars backend test suite."""

from __future__ import annotations

import asyncio

import pytest

from backend.arena import arena as arena_mod
from backend.environment.accounts import Accounts


class FakePrices:
    """Stand-in for backend.environment.prices.Prices using a static dict."""

    def __init__(self, prices: dict[str, float] | None = None):
        self._prices = {k.upper(): v for k, v in (prices or {}).items()}

    async def aget_price(self, ticker: str) -> float:
        return self._prices[ticker.upper()]

    async def aget_prices(self, tickers: list[str]) -> dict[str, float]:
        return {t: self._prices[t.upper()] for t in tickers}


@pytest.fixture
def disable_auth(monkeypatch):
    import backend.auth
    monkeypatch.setattr(backend.auth, "AUTH_SECRET_KEY", "")
    monkeypatch.setattr(backend.auth, "DEV_MODE", True)
    monkeypatch.setattr(backend.auth, "DEV_ADMIN", True)


@pytest.fixture
def neutralize_trader_loop(monkeypatch):
    async def _noop(self, stop_event: asyncio.Event):
        await stop_event.wait()

    monkeypatch.setattr(arena_mod.Trader, "run_until_stopped", _noop)
    monkeypatch.setattr(arena_mod, "wipe_memory_files", lambda _tids: None)


@pytest.fixture
def accounts():
    a = Accounts(":memory:")
    yield a
    a.close()
