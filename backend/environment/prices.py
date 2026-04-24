"""Massive (formerly Polygon.io) price lookups.

Thin wrapper over the official `massive` Python REST client. Reads
MASSIVE_API_KEY from the environment.
"""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from massive import RESTClient

load_dotenv(override=True)


class Prices:
    """Synchronous and async helpers for looking up the last trade price."""

    def __init__(self, api_key: str | None = None):
        key = api_key or os.getenv("MASSIVE_API_KEY")
        if not key:
            raise RuntimeError("MASSIVE_API_KEY is not set")
        self.client = RESTClient(api_key=key)

    def get_price(self, ticker: str) -> float:
        """Return the last trade price for a ticker (synchronous)."""
        trade = self.client.get_last_trade(ticker=ticker.upper())
        return float(trade.price)

    def get_prices(self, tickers: list[str]) -> dict[str, float]:
        """Return {ticker: price} for a list. Sequential, simple."""
        return {t: self.get_price(t) for t in tickers}

    async def aget_price(self, ticker: str) -> float:
        """Async wrapper via a worker thread."""
        return await asyncio.to_thread(self.get_price, ticker)

    async def aget_prices(self, tickers: list[str]) -> dict[str, float]:
        """Async batch lookup, parallelised across threads."""
        tasks = [self.aget_price(t) for t in tickers]
        results = await asyncio.gather(*tasks)
        return dict(zip(tickers, results))
