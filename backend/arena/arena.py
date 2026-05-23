"""Arena lifecycle: start → run → end (liquidate + record).

Start wipes the working accounts tables + memory files, creates fresh traders,
and launches 4 trader tasks concurrently via `asyncio.gather`. Each tick
refreshes prices for all held tickers and returns a snapshot for the UI.
End signals the stop event, waits briefly for cycles to unwind, liquidates
any remaining positions at the then-current Massive quotes (manual Stop and
auto 60:00 end behave identically), then records the game to history.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from backend.environment.accounts import Accounts
from backend.environment.prices import Prices
from backend.traders.mcp_servers import wipe_memory_files
from backend.traders.models import TraderConfig
from backend.traders.tools import TraderContext, holding_detail
from backend.traders.trader import Trader, TraderEvent

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "backend" / "arena" / "config.json"

SHUTDOWN_TIMEOUT_SECONDS = 30.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ArenaConfig:
    """Loaded `config.json` — a model catalog + presets. Knows nothing about
    the four runtime traders until `from_selections()` resolves them."""

    duration_seconds: float
    traders: list[TraderConfig] = field(default_factory=list)
    max_tokens: int = 64_000
    models: dict[str, dict] = field(default_factory=dict)   # model_id → spec
    presets: dict[str, list] = field(default_factory=dict)  # preset name → list of selections

    @classmethod
    def load(cls, path: Path | str = DEFAULT_CONFIG_PATH) -> "ArenaConfig":
        data = json.loads(Path(path).read_text())
        return cls(
            duration_seconds=float(data["duration_seconds"]),
            max_tokens=int(data["max_tokens"]),
            models=data["models"],
            presets=data["presets"],
        )

    def with_traders(self, traders: list[TraderConfig]) -> "ArenaConfig":
        """Return a new config carrying a resolved set of traders for a game."""
        return ArenaConfig(
            duration_seconds=self.duration_seconds,
            max_tokens=self.max_tokens,
            models=self.models,
            presets=self.presets,
            traders=traders,
        )

    def from_selections(self, selections: list[dict]) -> list[TraderConfig]:
        """Resolve a list of `{model_id, reasoning_label}` into TraderConfigs.

        - id = "<display_name> (<reasoning_label>)" with " #N" suffix added
          when later slots collide with an earlier one.
        - display_name + reasoning are pulled from the catalog entry.
        """
        used: dict[str, int] = {}
        traders: list[TraderConfig] = []
        for sel in selections:
            spec = self.models[sel["model_id"]]
            label = sel["reasoning_label"]
            reasoning = next(
                opt["reasoning"] for opt in spec["reasoning_options"] if opt["label"] == label
            )
            base_id = f"{spec['display_name']} ({label})"
            count = used.get(base_id, 0) + 1
            used[base_id] = count
            trader_id = base_id if count == 1 else f"{base_id} #{count}"
            traders.append(TraderConfig(
                id=trader_id,
                display_name=spec["display_name"],
                provider=spec["provider"],
                model=spec["model"],
                reasoning=reasoning,
                max_tokens=self.max_tokens,
            ))
        return traders

    def preset_selections(self, name: str) -> list[dict]:
        if name not in self.presets:
            raise KeyError(f"Unknown preset: {name}")
        return list(self.presets[name])


def reasoning_label(reasoning: dict) -> str:
    """Short human-readable summary of a reasoning config, e.g. 'max', '32k', 'off'."""
    if "effort" in reasoning:
        return str(reasoning["effort"])
    if "budget_tokens" in reasoning:
        n = int(reasoning["budget_tokens"])
        return f"{n // 1000}k" if n >= 1000 else str(n)
    thinking = reasoning.get("thinking")
    if isinstance(thinking, dict):
        t = thinking.get("type")
        if t == "disabled":
            return "off"
        if t == "enabled":
            return "on"
    return ""


@dataclass
class TraderSnapshot:
    trader_id: str
    display_name: str
    reasoning_label: str
    cash: float
    holdings: dict[str, dict[str, float]]  # ticker → {quantity, avg_cost, current_price, market_value, unrealized_pnl}
    total_portfolio_value: float
    total_pnl: float
    total_trades: int


@dataclass
class ArenaSnapshot:
    started_at: str
    time_elapsed_seconds: float
    time_remaining_seconds: float
    running: bool
    traders: list[TraderSnapshot]


@dataclass
class Arena:
    """Owns state for a single game.

    Instantiate once per game; call `start()` then `tick()` on each UI heartbeat
    and `end()` when the timer expires or the UI presses Stop. `stream()`
    yields TraderEvents for the SSE endpoint.
    """

    config: ArenaConfig
    accounts: Accounts
    prices: Prices
    events: asyncio.Queue[TraderEvent] = field(default_factory=asyncio.Queue)
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    _started_at: datetime | None = None
    _ended_at: datetime | None = None
    _tasks: list[asyncio.Task] = field(default_factory=list)
    _last_prices: dict[str, float] = field(default_factory=dict)
    _final_snapshot: ArenaSnapshot | None = None
    _auto_end_task: asyncio.Task | None = None
    _end_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # ---- lifecycle ----

    async def start(self) -> None:
        if self._started_at is not None:
            raise RuntimeError("Arena already started")
        self.accounts.reset_working_state()
        trader_ids = [t.id for t in self.config.traders]
        wipe_memory_files(trader_ids)
        for tid in trader_ids:
            self.accounts.create_trader(tid)

        self._started_at = _now()
        self.stop_event.clear()

        for cfg in self.config.traders:
            ctx = TraderContext(
                trader_id=cfg.id,
                accounts=self.accounts,
                prices=self.prices,
                started_at=self._started_at,
                duration_seconds=self.config.duration_seconds,
                rival_ids=[t.id for t in self.config.traders if t.id != cfg.id],
            )
            trader = Trader(config=cfg, context=ctx, events=self.events)
            self._tasks.append(asyncio.create_task(
                trader.run_until_stopped(self.stop_event),
                name=f"trader-{cfg.id}",
            ))

        self._auto_end_task = asyncio.create_task(self._auto_end(), name="arena-auto-end")

    async def _auto_end(self) -> None:
        """Fires end() when the clock runs out. Idempotent with manual Stop."""
        try:
            await asyncio.sleep(self.config.duration_seconds)
        except asyncio.CancelledError:
            return
        if self._ended_at is None:
            await self.end()

    async def end(self) -> ArenaSnapshot:
        if self._started_at is None:
            raise RuntimeError("Arena not started")
        async with self._end_lock:
            if self._final_snapshot is not None:
                return self._final_snapshot

            # Cancel the auto-end timer if we're the manual caller.
            current = asyncio.current_task()
            if self._auto_end_task and self._auto_end_task is not current:
                self._auto_end_task.cancel()

            self.stop_event.set()

            # Give in-flight cycles a chance to finish; cancel stragglers.
            _done, pending = await asyncio.wait(
                self._tasks, timeout=SHUTDOWN_TIMEOUT_SECONDS
            )
            for task in pending:
                task.cancel()
            for task in self._tasks:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("trader task raised on shutdown")

            await self._liquidate_all()

            self._ended_at = _now()
            snapshot = await self._snapshot(running=False)
            self._record_game(snapshot)
            self._final_snapshot = snapshot
            return snapshot

    # ---- tick / snapshot ----

    async def tick(self) -> ArenaSnapshot:
        if self._started_at is None:
            raise RuntimeError("Arena not started")
        if self._final_snapshot is not None:
            return self._final_snapshot
        return await self._snapshot(running=True)

    def _all_held_tickers(self) -> set[str]:
        tickers: set[str] = set()
        for cfg in self.config.traders:
            tickers.update(self.accounts.holdings(cfg.id))
        return tickers

    async def _snapshot(self, *, running: bool) -> ArenaSnapshot:
        tickers = self._all_held_tickers()
        current = await self.prices.aget_prices(sorted(tickers)) if tickers else {}
        self._last_prices.update(current)
        all_prices = {**self._last_prices, **current}

        traders_snap = []
        for cfg in self.config.traders:
            holdings = self.accounts.holdings(cfg.id)
            detail = {}
            for t, p in holdings.items():
                price = all_prices.get(t)
                if price is not None:
                    detail[t] = holding_detail(p, price)
            value = self.accounts.portfolio_value(cfg.id, all_prices)
            traders_snap.append(TraderSnapshot(
                trader_id=cfg.id,
                display_name=cfg.display_name,
                reasoning_label=reasoning_label(cfg.reasoning),
                cash=self.accounts.cash(cfg.id),
                holdings=detail,
                total_portfolio_value=value,
                total_pnl=self.accounts.pnl(cfg.id, value),
                total_trades=self.accounts.trade_count(cfg.id),
            ))

        assert self._started_at is not None
        elapsed = (_now() - self._started_at).total_seconds()
        return ArenaSnapshot(
            started_at=self._started_at.isoformat(),
            time_elapsed_seconds=round(elapsed, 1),
            time_remaining_seconds=round(max(0.0, self.config.duration_seconds - elapsed), 1),
            running=running,
            traders=traders_snap,
        )

    # ---- liquidation / history ----

    async def _liquidate_all(self) -> None:
        """Sell every open position at current price, falling back to the last
        tick price cache if the live lookup fails (per PLAN)."""
        tickers = self._all_held_tickers()
        if not tickers:
            return

        fresh: dict[str, float] = {}
        try:
            fresh = await self.prices.aget_prices(sorted(tickers))
        except Exception:
            logger.exception("batch price lookup for liquidation failed; using last-tick cache")

        for cfg in self.config.traders:
            for ticker, pos in list(self.accounts.holdings(cfg.id).items()):
                price = fresh.get(ticker)
                if price is None:
                    price = self._last_prices.get(ticker)
                if price is None:
                    logger.error("no price for %s during liquidation; skipping", ticker)
                    continue
                try:
                    self.accounts.execute_trade(cfg.id, ticker, -pos["quantity"], price)
                    await self.events.put(TraderEvent(
                        trader_id=cfg.id,
                        type="liquidation",
                        timestamp=_now().isoformat(),
                        payload={"ticker": ticker, "quantity": pos["quantity"], "price": price},
                    ))
                except Exception:
                    logger.exception("liquidation sell failed for %s / %s", cfg.id, ticker)

    def _record_game(self, snapshot: ArenaSnapshot) -> None:
        assert self._started_at is not None and self._ended_at is not None
        duration = (self._ended_at - self._started_at).total_seconds()
        final_results = {t.trader_id: t.total_pnl for t in snapshot.traders}
        self.accounts.record_game(
            started_at=self._started_at.isoformat(),
            ended_at=self._ended_at.isoformat(),
            duration_seconds=duration,
            final_results=final_results,
        )

    # ---- streaming ----

    async def stream(self) -> AsyncIterator[TraderEvent]:
        """Yield TraderEvents as they arrive. Intended for the SSE endpoint."""
        while True:
            yield await self.events.get()
