"""Tests for Arena lifecycle without running real agents.

We monkey-patch `Trader.run_until_stopped` to a no-op that just awaits the
stop event, then drive start → tick → end and verify that: the DB is wiped
on start, traders exist with fresh $1M, holdings are liquidated at end, and
a row is written to the games history table.
"""

import asyncio

import pytest

from backend.arena.arena import Arena, ArenaConfig
from backend.environment.accounts import INITIAL_BALANCE, Accounts
from backend.traders.models import TraderConfig
from backend.test.conftest import FakePrices

pytestmark = pytest.mark.usefixtures("neutralize_trader_loop")


@pytest.fixture
def mini_config():
    return ArenaConfig(
        duration_seconds=3600.0,
        traders=[
            TraderConfig(
                id=f"t{i}", display_name=f"T{i}",
                provider="openai", model="gpt-5.4",
                reasoning={"effort": "low"}, max_tokens=1000,
            )
            for i in range(4)
        ],
    )


async def test_start_wipes_state_and_creates_fresh_traders(mini_config, accounts):
    # seed some prior state to prove start() wipes it
    accounts.create_trader("stale")
    accounts.execute_trade("stale", "OLD", 1, 10)

    arena = Arena(config=mini_config, accounts=accounts, prices=FakePrices({}))
    await arena.start()

    with pytest.raises(KeyError):
        accounts.cash("stale")
    for t in mini_config.traders:
        assert accounts.cash(t.id) == INITIAL_BALANCE
        assert accounts.holdings(t.id) == {}

    arena.stop_event.set()
    await asyncio.gather(*arena._tasks, return_exceptions=True)


async def test_tick_returns_snapshot_with_all_traders(mini_config, accounts):
    arena = Arena(config=mini_config, accounts=accounts, prices=FakePrices({"AAPL": 100.0}))
    await arena.start()

    snap = await arena.tick()
    assert snap.running is True
    assert len(snap.traders) == 4
    assert all(t.cash == INITIAL_BALANCE for t in snap.traders)
    assert all(t.total_portfolio_value == INITIAL_BALANCE for t in snap.traders)
    assert snap.time_remaining_seconds <= 3600.0

    arena.stop_event.set()
    await asyncio.gather(*arena._tasks, return_exceptions=True)


async def test_end_liquidates_holdings_and_records_game(mini_config, accounts):
    arena = Arena(
        config=mini_config,
        accounts=accounts,
        prices=FakePrices({"AAPL": 110.0, "MSFT": 220.0}),
    )
    await arena.start()

    # Simulate some trades mid-game by mutating the accounts directly.
    accounts.execute_trade("t0", "AAPL", 10, 100.0)   # +100 on AAPL at 110
    accounts.execute_trade("t1", "MSFT", 5, 200.0)    # +100 on MSFT at 220

    snap = await arena.end()

    # All holdings liquidated, cash-only state.
    for t in mini_config.traders:
        assert accounts.holdings(t.id) == {}
    # t0 ended with +100 P&L, t1 +100, t2 / t3 flat.
    final = {t.trader_id: t.total_pnl for t in snap.traders}
    assert final["t0"] == pytest.approx(100.0)
    assert final["t1"] == pytest.approx(100.0)
    assert final["t2"] == 0.0
    assert final["t3"] == 0.0

    games = accounts.list_games()
    assert len(games) == 1
    assert games[0]["final_results"]["t0"] == pytest.approx(100.0)


async def test_end_is_idempotent(mini_config, accounts):
    arena = Arena(config=mini_config, accounts=accounts, prices=FakePrices({}))
    await arena.start()
    snap1 = await arena.end()
    snap2 = await arena.end()
    assert snap1 is snap2
    # Only one row in games history.
    assert len(accounts.list_games()) == 1


async def test_start_twice_raises(mini_config, accounts):
    arena = Arena(config=mini_config, accounts=accounts, prices=FakePrices({}))
    await arena.start()
    with pytest.raises(RuntimeError, match="already started"):
        await arena.start()
    arena.stop_event.set()
    await asyncio.gather(*arena._tasks, return_exceptions=True)


async def test_tick_before_start_raises(mini_config, accounts):
    arena = Arena(config=mini_config, accounts=accounts, prices=FakePrices({}))
    with pytest.raises(RuntimeError, match="not started"):
        await arena.tick()


async def test_auto_end_fires_when_duration_elapses(accounts):
    """With a tiny duration, the arena should end itself without a manual call."""
    config = ArenaConfig(
        duration_seconds=0.2,
        traders=[TraderConfig(
            id="t0", display_name="T0",
            provider="openai", model="gpt-5.4",
            reasoning={"effort": "low"}, max_tokens=1000,
        )],
    )
    arena = Arena(config=config, accounts=accounts, prices=FakePrices({}))
    await arena.start()

    await asyncio.sleep(0.5)  # give auto-end room to fire + shut down
    assert arena._ended_at is not None
    assert arena._final_snapshot is not None
    assert len(accounts.list_games()) == 1


async def test_liquidation_falls_back_to_last_tick_price_if_live_lookup_fails(
    mini_config, accounts,
):
    class FlakeyPrices:
        def __init__(self):
            self.call_count = 0

        async def aget_price(self, ticker: str) -> float:
            return 100.0

        async def aget_prices(self, tickers):
            self.call_count += 1
            # First call (tick) succeeds, second call (liquidation) blows up.
            if self.call_count == 1:
                return {t: 100.0 for t in tickers}
            raise RuntimeError("Massive went dark")

    prices = FlakeyPrices()
    arena = Arena(config=mini_config, accounts=accounts, prices=prices)
    await arena.start()
    accounts.execute_trade("t0", "AAPL", 1, 100.0)

    # Populate tick cache with a known price.
    await arena.tick()

    snap = await arena.end()
    # Liquidation fell back to cached $100, so t0 ends flat.
    assert accounts.holdings("t0") == {}
    final = {t.trader_id: t.total_pnl for t in snap.traders}
    assert final["t0"] == 0.0
