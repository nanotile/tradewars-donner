"""Massive (formerly Polygon.io) price lookups with Kraken fallback for crypto.

Equities go through the Massive REST client. Crypto tickers (X:BTCUSD etc.)
route to Kraken's free public API — no key required, works 24/7.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.request

from massive import RESTClient

_KRAKEN_TICKER_URL = "https://api.kraken.com/0/public/Ticker"
_CACHE_TTL = 2.0


def _is_crypto(ticker: str) -> bool:
    return ticker.upper().startswith("X:")


def _kraken_price(ticker: str) -> float:
    pair = ticker.upper().removeprefix("X:")
    resp = urllib.request.urlopen(f"{_KRAKEN_TICKER_URL}?pair={pair}", timeout=10)
    data = json.loads(resp.read())
    if data["error"]:
        raise RuntimeError(f"Kraken error for {pair}: {data['error']}")
    result_key = next(iter(data["result"]))
    return float(data["result"][result_key]["c"][0])


class Prices:
    """Synchronous and async helpers for looking up the last trade price."""

    def __init__(self, api_key: str | None = None):
        key = api_key or os.getenv("MASSIVE_API_KEY")
        if not key:
            raise RuntimeError("MASSIVE_API_KEY is not set")
        self.client = RESTClient(api_key=key)
        self._cache: dict[str, tuple[float, float]] = {}

    def get_price(self, ticker: str) -> float:
        """Return the last trade price for a ticker (synchronous). Cached for 2s."""
        ticker = ticker.upper()
        now = time.monotonic()
        cached = self._cache.get(ticker)
        if cached and now - cached[1] < _CACHE_TTL:
            return cached[0]
        if _is_crypto(ticker):
            price = _kraken_price(ticker)
        else:
            trade = self.client.get_last_trade(ticker=ticker)
            price = float(trade.price)
        self._cache[ticker] = (price, now)
        return price

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
