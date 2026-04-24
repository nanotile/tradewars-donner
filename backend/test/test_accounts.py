"""Tests for backend.environment.accounts."""

import pytest

from backend.environment.accounts import INITIAL_BALANCE, Accounts


@pytest.fixture
def accounts():
    a = Accounts(":memory:")
    yield a
    a.close()


def test_create_trader_starts_with_initial_balance(accounts):
    accounts.create_trader("claude")
    assert accounts.cash("claude") == INITIAL_BALANCE
    assert accounts.initial_balance("claude") == INITIAL_BALANCE
    assert accounts.holdings("claude") == {}


def test_unknown_trader_raises(accounts):
    with pytest.raises(KeyError):
        accounts.cash("nobody")


def test_buy_reduces_cash_and_records_holding(accounts):
    accounts.create_trader("claude")
    accounts.execute_trade("claude", "AAPL", 10, 150.0)

    assert accounts.cash("claude") == INITIAL_BALANCE - 1500.0
    holdings = accounts.holdings("claude")
    assert holdings == {"AAPL": {"quantity": 10.0, "avg_cost": 150.0}}


def test_fractional_buy(accounts):
    accounts.create_trader("claude")
    accounts.execute_trade("claude", "AAPL", 0.5, 200.0)

    assert accounts.cash("claude") == INITIAL_BALANCE - 100.0
    assert accounts.holdings("claude")["AAPL"]["quantity"] == 0.5


def test_avg_cost_blends_across_buys(accounts):
    accounts.create_trader("claude")
    accounts.execute_trade("claude", "AAPL", 10, 100.0)
    accounts.execute_trade("claude", "AAPL", 10, 200.0)

    h = accounts.holdings("claude")["AAPL"]
    assert h["quantity"] == 20.0
    assert h["avg_cost"] == 150.0


def test_sell_increases_cash_and_decreases_holding(accounts):
    accounts.create_trader("claude")
    accounts.execute_trade("claude", "AAPL", 10, 100.0)
    accounts.execute_trade("claude", "AAPL", -4, 120.0)

    assert accounts.cash("claude") == INITIAL_BALANCE - 1000.0 + 480.0
    assert accounts.holdings("claude")["AAPL"]["quantity"] == 6.0
    assert accounts.holdings("claude")["AAPL"]["avg_cost"] == 100.0


def test_selling_all_removes_holding(accounts):
    accounts.create_trader("claude")
    accounts.execute_trade("claude", "AAPL", 10, 100.0)
    accounts.execute_trade("claude", "AAPL", -10, 110.0)

    assert accounts.holdings("claude") == {}


def test_insufficient_cash_raises(accounts):
    accounts.create_trader("claude")
    with pytest.raises(ValueError, match="Insufficient cash"):
        accounts.execute_trade("claude", "AAPL", 100, 20_000.0)


def test_no_shorting(accounts):
    accounts.create_trader("claude")
    with pytest.raises(ValueError, match="Cannot sell"):
        accounts.execute_trade("claude", "AAPL", -1, 100.0)


def test_cannot_oversell(accounts):
    accounts.create_trader("claude")
    accounts.execute_trade("claude", "AAPL", 5, 100.0)
    with pytest.raises(ValueError, match="Cannot sell"):
        accounts.execute_trade("claude", "AAPL", -6, 110.0)


def test_zero_quantity_rejected(accounts):
    accounts.create_trader("claude")
    with pytest.raises(ValueError, match="non-zero"):
        accounts.execute_trade("claude", "AAPL", 0, 100.0)


def test_non_positive_price_rejected(accounts):
    accounts.create_trader("claude")
    with pytest.raises(ValueError, match="positive"):
        accounts.execute_trade("claude", "AAPL", 1, 0.0)


def test_ticker_normalised_to_uppercase(accounts):
    accounts.create_trader("claude")
    accounts.execute_trade("claude", "aapl", 1, 100.0)
    assert "AAPL" in accounts.holdings("claude")


def test_trades_are_logged(accounts):
    accounts.create_trader("claude")
    accounts.execute_trade("claude", "AAPL", 1, 100.0)
    accounts.execute_trade("claude", "AAPL", -1, 110.0)

    trades = accounts.trades("claude")
    assert len(trades) == 2
    assert trades[0]["quantity"] == 1.0
    assert trades[1]["quantity"] == -1.0
    assert trades[0]["price"] == 100.0
    assert trades[1]["price"] == 110.0


def test_portfolio_value(accounts):
    accounts.create_trader("claude")
    accounts.execute_trade("claude", "AAPL", 10, 100.0)
    accounts.execute_trade("claude", "MSFT", 5, 200.0)

    prices = {"AAPL": 110.0, "MSFT": 210.0}
    value = accounts.portfolio_value("claude", prices)
    expected_cash = INITIAL_BALANCE - 1000.0 - 1000.0
    expected_value = expected_cash + 10 * 110.0 + 5 * 210.0
    assert value == expected_value


def test_portfolio_value_missing_price_raises(accounts):
    accounts.create_trader("claude")
    accounts.execute_trade("claude", "AAPL", 1, 100.0)
    with pytest.raises(KeyError):
        accounts.portfolio_value("claude", {})


def test_pnl_positive_and_negative(accounts):
    accounts.create_trader("claude")
    accounts.execute_trade("claude", "AAPL", 10, 100.0)

    value_up = accounts.portfolio_value("claude", {"AAPL": 150.0})
    assert accounts.pnl("claude", value_up) == 500.0

    value_down = accounts.portfolio_value("claude", {"AAPL": 50.0})
    assert accounts.pnl("claude", value_down) == -500.0


def test_reset_working_state_wipes_traders_but_keeps_games(accounts):
    accounts.create_trader("claude")
    accounts.execute_trade("claude", "AAPL", 1, 100.0)
    accounts.record_game("2026-04-24T12:00:00+00:00", "2026-04-24T13:00:00+00:00", 3600, {"claude": 1500.0})

    accounts.reset_working_state()

    with pytest.raises(KeyError):
        accounts.cash("claude")
    assert len(accounts.list_games()) == 1


def test_record_game_and_list(accounts):
    accounts.record_game("s1", "e1", 60.0, {"claude": 100.0, "gpt": -50.0})
    accounts.record_game("s2", "e2", 3600.0, {"claude": -200.0})

    games = accounts.list_games()
    assert len(games) == 2
    assert games[0]["final_results"] == {"claude": -200.0}  # most recent first
    assert games[1]["final_results"] == {"claude": 100.0, "gpt": -50.0}


def test_multiple_traders_isolated(accounts):
    accounts.create_trader("claude")
    accounts.create_trader("gpt")
    accounts.execute_trade("claude", "AAPL", 10, 100.0)

    assert accounts.cash("gpt") == INITIAL_BALANCE
    assert accounts.holdings("gpt") == {}
    assert accounts.holdings("claude") == {"AAPL": {"quantity": 10.0, "avg_cost": 100.0}}
