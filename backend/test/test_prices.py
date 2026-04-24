"""Tests for backend.environment.prices — hits the real Massive API."""

import os

import pytest

from backend.environment.prices import Prices

pytestmark = pytest.mark.skipif(
    not os.getenv("MASSIVE_API_KEY"),
    reason="MASSIVE_API_KEY not set",
)


def test_get_price_returns_positive_float():
    p = Prices()
    price = p.get_price("AAPL")
    assert isinstance(price, float)
    assert price > 0


def test_get_price_uppercases_ticker():
    p = Prices()
    upper = p.get_price("AAPL")
    lower = p.get_price("aapl")
    assert upper > 0
    assert lower > 0


def test_get_prices_batch():
    p = Prices()
    result = p.get_prices(["AAPL", "MSFT"])
    assert set(result.keys()) == {"AAPL", "MSFT"}
    assert all(v > 0 for v in result.values())


async def test_aget_price():
    p = Prices()
    price = await p.aget_price("AAPL")
    assert price > 0


async def test_aget_prices_concurrent():
    p = Prices()
    result = await p.aget_prices(["AAPL", "MSFT", "GOOGL"])
    assert set(result.keys()) == {"AAPL", "MSFT", "GOOGL"}
    assert all(v > 0 for v in result.values())


def test_missing_api_key_raises():
    original = os.environ.pop("MASSIVE_API_KEY", None)
    try:
        with pytest.raises(RuntimeError, match="MASSIVE_API_KEY"):
            Prices(api_key=None)
    finally:
        if original is not None:
            os.environ["MASSIVE_API_KEY"] = original
