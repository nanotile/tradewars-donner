"""Tests for backend.traders.tools — get_state and trade logic."""

from datetime import datetime, timedelta, timezone

import pytest

from backend.environment.accounts import INITIAL_BALANCE, Accounts
from backend.traders.tools import TraderContext, get_state_impl, trade_impl


class FakePrices:
    """Stand-in for backend.environment.prices.Prices using a static dict."""

    def __init__(self, prices: dict[str, float]):
        self._prices = {k.upper(): v for k, v in prices.items()}

    async def aget_price(self, ticker: str) -> float:
        return self._prices[ticker.upper()]

    async def aget_prices(self, tickers: list[str]) -> dict[str, float]:
        return {t: self._prices[t.upper()] for t in tickers}


@pytest.fixture
def accounts():
    a = Accounts(":memory:")
    for tid in ("claude", "gpt", "kimi"):
        a.create_trader(tid)
    yield a
    a.close()


def make_ctx(accounts: Accounts, prices: dict[str, float], *, trader_id: str = "claude", rivals: list[str] | None = None) -> TraderContext:
    return TraderContext(
        trader_id=trader_id,
        accounts=accounts,
        prices=FakePrices(prices),
        started_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        duration_seconds=3600.0,
        rival_ids=rivals if rivals is not None else ["gpt", "kimi"],
    )


async def test_initial_state_is_all_cash(accounts):
    ctx = make_ctx(accounts, {})
    state = await get_state_impl(ctx)

    assert state["trader_id"] == "claude"
    assert state["cash"] == INITIAL_BALANCE
    assert state["holdings"] == {}
    assert state["total_portfolio_value"] == INITIAL_BALANCE
    assert state["total_pnl"] == 0.0


async def test_state_timing_fields(accounts):
    ctx = make_ctx(accounts, {})
    state = await get_state_impl(ctx)

    assert 100 <= state["time_elapsed_seconds"] <= 140
    assert 3460 <= state["time_remaining_seconds"] <= 3500


async def test_state_includes_rivals_portfolio_values(accounts):
    accounts.execute_trade("gpt", "MSFT", 100, 400.0)
    ctx = make_ctx(accounts, {"MSFT": 420.0})
    state = await get_state_impl(ctx)

    assert "gpt" in state["rivals_total_portfolio_value"]
    assert "kimi" in state["rivals_total_portfolio_value"]
    expected_gpt = INITIAL_BALANCE - 100 * 400.0 + 100 * 420.0
    assert state["rivals_total_portfolio_value"]["gpt"] == expected_gpt
    assert state["rivals_total_portfolio_value"]["kimi"] == INITIAL_BALANCE


async def test_state_holdings_include_per_position_pnl(accounts):
    accounts.execute_trade("claude", "AAPL", 10, 100.0)
    ctx = make_ctx(accounts, {"AAPL": 110.0})
    state = await get_state_impl(ctx)

    aapl = state["holdings"]["AAPL"]
    assert aapl["quantity"] == 10.0
    assert aapl["avg_cost"] == 100.0
    assert aapl["current_price"] == 110.0
    assert aapl["market_value"] == 1100.0
    assert aapl["unrealized_pnl"] == 100.0


async def test_state_total_pnl_matches_holdings(accounts):
    accounts.execute_trade("claude", "AAPL", 10, 100.0)
    accounts.execute_trade("claude", "MSFT", 5, 200.0)
    ctx = make_ctx(accounts, {"AAPL": 120.0, "MSFT": 180.0})

    state = await get_state_impl(ctx)
    # 200 gain on AAPL, 100 loss on MSFT → +100 net
    assert state["total_pnl"] == 100.0
    expected_value = (INITIAL_BALANCE - 1000.0 - 1000.0) + 10 * 120.0 + 5 * 180.0
    assert state["total_portfolio_value"] == expected_value


async def test_trade_buy_fills_at_current_price(accounts):
    ctx = make_ctx(accounts, {"AAPL": 123.45})
    result = await trade_impl(ctx, "AAPL", 10)

    assert result["success"] is True
    assert result["ticker"] == "AAPL"
    assert result["price"] == 123.45
    assert result["side"] == "buy"
    assert accounts.cash("claude") == INITIAL_BALANCE - 1234.5


async def test_trade_sell_uses_negative_quantity(accounts):
    accounts.execute_trade("claude", "AAPL", 10, 100.0)
    ctx = make_ctx(accounts, {"AAPL": 150.0})
    result = await trade_impl(ctx, "AAPL", -4)

    assert result["success"] is True
    assert result["side"] == "sell"
    assert result["price"] == 150.0
    assert accounts.holdings("claude")["AAPL"]["quantity"] == 6.0


async def test_trade_fractional(accounts):
    ctx = make_ctx(accounts, {"AAPL": 200.0})
    result = await trade_impl(ctx, "AAPL", 0.25)

    assert result["success"] is True
    assert accounts.holdings("claude")["AAPL"]["quantity"] == 0.25
    assert accounts.cash("claude") == INITIAL_BALANCE - 50.0


async def test_trade_ticker_is_normalized(accounts):
    ctx = make_ctx(accounts, {"AAPL": 100.0})
    result = await trade_impl(ctx, "aapl", 1)

    assert result["ticker"] == "AAPL"
    assert "AAPL" in accounts.holdings("claude")


async def test_trade_insufficient_cash_returns_error(accounts):
    ctx = make_ctx(accounts, {"AAPL": 2_000_000.0})
    result = await trade_impl(ctx, "AAPL", 1)

    assert result["success"] is False
    assert "Insufficient cash" in result["error"]
    # No trade recorded, cash unchanged
    assert accounts.cash("claude") == INITIAL_BALANCE
    assert accounts.holdings("claude") == {}


async def test_trade_cannot_short(accounts):
    ctx = make_ctx(accounts, {"AAPL": 100.0})
    result = await trade_impl(ctx, "AAPL", -1)

    assert result["success"] is False
    assert "Cannot sell" in result["error"]


async def test_trade_rejects_zero_quantity(accounts):
    ctx = make_ctx(accounts, {"AAPL": 100.0})
    result = await trade_impl(ctx, "AAPL", 0)

    assert result["success"] is False
    assert "non-zero" in result["error"]
