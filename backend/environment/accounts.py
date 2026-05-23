"""Trader account state backed by SQLite.

One row per trader in `accounts`, one row per held position in `holdings`,
append-only `trades` and `games` tables. All quantities are fractional (REAL).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

INITIAL_BALANCE = 1_000_000.0
EPSILON = 1e-9  # float quantity tolerance after SQLite round-trips

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    trader_id TEXT PRIMARY KEY,
    cash REAL NOT NULL,
    initial_balance REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS holdings (
    trader_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    quantity REAL NOT NULL,
    avg_cost REAL NOT NULL,
    PRIMARY KEY (trader_id, ticker)
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trader_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    final_results TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Accounts:
    """Thin SQLite-backed store for trader accounts, holdings, trades and game history."""

    def __init__(self, db_path: str | Path = ":memory:"):
        self.db_path = str(db_path)
        # check_same_thread=False: FastAPI's async routes may be dispatched from
        # a worker thread under TestClient; we never write concurrently, so this
        # is safe.
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def reset_working_state(self) -> None:
        """Wipe accounts, holdings, trades. Leaves `games` history intact."""
        with self.conn:
            self.conn.execute("DELETE FROM accounts")
            self.conn.execute("DELETE FROM holdings")
            self.conn.execute("DELETE FROM trades")

    def create_trader(self, trader_id: str, initial: float = INITIAL_BALANCE) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO accounts (trader_id, cash, initial_balance) VALUES (?, ?, ?)",
                (trader_id, initial, initial),
            )

    def cash(self, trader_id: str) -> float:
        row = self.conn.execute(
            "SELECT cash FROM accounts WHERE trader_id = ?", (trader_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown trader: {trader_id}")
        return float(row["cash"])

    def initial_balance(self, trader_id: str) -> float:
        row = self.conn.execute(
            "SELECT initial_balance FROM accounts WHERE trader_id = ?", (trader_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown trader: {trader_id}")
        return float(row["initial_balance"])

    def holdings(self, trader_id: str) -> dict[str, dict[str, float]]:
        """Returns {ticker: {"quantity": q, "avg_cost": c}}."""
        rows = self.conn.execute(
            "SELECT ticker, quantity, avg_cost FROM holdings WHERE trader_id = ?",
            (trader_id,),
        ).fetchall()
        return {
            r["ticker"]: {"quantity": float(r["quantity"]), "avg_cost": float(r["avg_cost"])}
            for r in rows
        }

    def trades(self, trader_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT ticker, quantity, price, ts FROM trades WHERE trader_id = ? ORDER BY id",
            (trader_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def execute_trade(
        self, trader_id: str, ticker: str, quantity: float, price: float
    ) -> None:
        """Buy if quantity > 0, sell if quantity < 0. Fractional allowed. No shorting."""
        if quantity == 0:
            raise ValueError("Quantity must be non-zero")
        if price <= 0:
            raise ValueError("Price must be positive")
        ticker = ticker.upper()

        cash = self.cash(trader_id)
        holdings = self.holdings(trader_id)
        current = holdings.get(ticker, {"quantity": 0.0, "avg_cost": 0.0})
        cur_qty = current["quantity"]
        cur_avg = current["avg_cost"]

        if quantity > 0:
            cost = quantity * price
            if cost > cash:
                raise ValueError(
                    f"Insufficient cash: need {cost:.2f}, have {cash:.2f}"
                )
            new_qty = cur_qty + quantity
            new_avg = (cur_qty * cur_avg + quantity * price) / new_qty
            new_cash = cash - cost
        else:
            sell_qty = -quantity
            if sell_qty > cur_qty + EPSILON:
                raise ValueError(
                    f"Cannot sell {sell_qty} of {ticker}: only hold {cur_qty}"
                )
            new_qty = cur_qty - sell_qty
            new_avg = cur_avg  # avg cost basis unchanged on partial sell
            new_cash = cash + sell_qty * price

        with self.conn:
            self.conn.execute(
                "UPDATE accounts SET cash = ? WHERE trader_id = ?",
                (new_cash, trader_id),
            )
            if new_qty <= EPSILON:
                self.conn.execute(
                    "DELETE FROM holdings WHERE trader_id = ? AND ticker = ?",
                    (trader_id, ticker),
                )
            else:
                self.conn.execute(
                    """
                    INSERT INTO holdings (trader_id, ticker, quantity, avg_cost)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(trader_id, ticker) DO UPDATE
                    SET quantity = excluded.quantity, avg_cost = excluded.avg_cost
                    """,
                    (trader_id, ticker, new_qty, new_avg),
                )
            self.conn.execute(
                "INSERT INTO trades (trader_id, ticker, quantity, price, ts) VALUES (?, ?, ?, ?, ?)",
                (trader_id, ticker, quantity, price, _now()),
            )

    def portfolio_value(
        self, trader_id: str, prices: dict[str, float]
    ) -> float:
        """Cash + sum(quantity * current_price). `prices` must cover every held ticker."""
        value = self.cash(trader_id)
        for ticker, pos in self.holdings(trader_id).items():
            if ticker in prices:
                value += pos["quantity"] * prices[ticker]
        return value

    def pnl(self, trader_id: str, portfolio_value: float) -> float:
        return portfolio_value - self.initial_balance(trader_id)

    def record_game(
        self,
        started_at: str,
        ended_at: str,
        duration_seconds: float,
        final_results: dict[str, float],
    ) -> int:
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO games (started_at, ended_at, duration_seconds, final_results)
                VALUES (?, ?, ?, ?)
                """,
                (started_at, ended_at, duration_seconds, json.dumps(final_results)),
            )
            return int(cur.lastrowid)

    def list_games(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, started_at, ended_at, duration_seconds, final_results FROM games ORDER BY id DESC"
        ).fetchall()
        return [
            {**dict(r), "final_results": json.loads(r["final_results"])}
            for r in rows
        ]
