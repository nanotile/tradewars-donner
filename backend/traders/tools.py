"""Game tools exposed to each trader agent.

`get_state` — time, portfolio, holdings with per-position P&L, rivals' totals.
`trade`     — buy (positive quantity) or sell (negative quantity), fractional,
              fills synchronously at the current Massive quote.

The per-trader state (trader_id, accounts store, price client, clock) is
carried through the OpenAI Agents SDK `RunContextWrapper.context`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agents import RunContextWrapper, function_tool

from backend.environment.accounts import Accounts
from backend.environment.prices import Prices


@dataclass
class TraderContext:
    trader_id: str
    accounts: Accounts
    prices: Prices
    started_at: datetime
    duration_seconds: float
    rival_ids: list[str] = field(default_factory=list)


async def get_state_impl(tc: TraderContext) -> dict[str, Any]:
    """Compute a full state snapshot for the given trader context."""
    now = datetime.now(timezone.utc)
    elapsed = (now - tc.started_at).total_seconds()
    remaining = max(0.0, tc.duration_seconds - elapsed)

    own_holdings = tc.accounts.holdings(tc.trader_id)
    rivals_holdings = {rid: tc.accounts.holdings(rid) for rid in tc.rival_ids}

    tickers = set(own_holdings)
    for h in rivals_holdings.values():
        tickers.update(h)
    current_prices = await tc.prices.aget_prices(sorted(tickers)) if tickers else {}

    holdings_detail: dict[str, dict[str, float]] = {}
    for ticker, pos in own_holdings.items():
        price = current_prices[ticker]
        market_value = pos["quantity"] * price
        unrealized_pnl = market_value - pos["quantity"] * pos["avg_cost"]
        holdings_detail[ticker] = {
            "quantity": pos["quantity"],
            "avg_cost": pos["avg_cost"],
            "current_price": price,
            "market_value": market_value,
            "unrealized_pnl": unrealized_pnl,
        }

    own_value = tc.accounts.portfolio_value(tc.trader_id, current_prices)
    own_pnl = tc.accounts.pnl(tc.trader_id, own_value)

    rivals = {
        rid: tc.accounts.portfolio_value(rid, current_prices)
        for rid in tc.rival_ids
    }

    return {
        "trader_id": tc.trader_id,
        "time_elapsed_seconds": round(elapsed, 1),
        "time_remaining_seconds": round(remaining, 1),
        "cash": tc.accounts.cash(tc.trader_id),
        "holdings": holdings_detail,
        "total_portfolio_value": own_value,
        "total_pnl": own_pnl,
        "rivals_total_portfolio_value": rivals,
    }


async def trade_impl(
    tc: TraderContext, ticker: str, quantity: float
) -> dict[str, Any]:
    """Execute a single trade at the current Massive quote."""
    ticker = ticker.upper()
    price = await tc.prices.aget_price(ticker)
    try:
        tc.accounts.execute_trade(tc.trader_id, ticker, quantity, price)
    except ValueError as e:
        return {
            "success": False,
            "error": str(e),
            "ticker": ticker,
            "requested_quantity": quantity,
            "price": price,
        }
    return {
        "success": True,
        "ticker": ticker,
        "quantity": quantity,
        "price": price,
        "side": "buy" if quantity > 0 else "sell",
        "cash_after": tc.accounts.cash(tc.trader_id),
    }


@function_tool
async def get_state(ctx: RunContextWrapper[TraderContext]) -> dict[str, Any]:
    """Return the trader's current game state.

    Includes time elapsed and remaining, cash, holdings (with per-position
    market value and unrealized P&L), total portfolio value, total P&L, and
    the total portfolio value of each rival trader.
    """
    return await get_state_impl(ctx.context)


@function_tool
async def trade(
    ctx: RunContextWrapper[TraderContext], ticker: str, quantity: float
) -> dict[str, Any]:
    """Buy or sell a ticker, filling at the current Massive quote.

    Args:
        ticker: Stock ticker symbol, e.g. "AAPL".
        quantity: Number of shares. Fractional allowed. Positive buys, negative sells. No short selling.
    """
    return await trade_impl(ctx.context, ticker, quantity)
