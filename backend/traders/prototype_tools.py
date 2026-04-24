"""Phase 3 integration prototype.

A real Agent sees the `get_state` + `trade` function tools, carries a
TraderContext through the Runner, and exercises both tools end-to-end
against a live Massive price feed and an in-memory SQLite accounts DB.

We assert after the run that the DB reflects the trades — so this doubles
as proof that the tool surface really mutates state (not just claimed to).

Uses a cheap OpenRouter model to keep cost trivial.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from agents import (
    Agent,
    AsyncOpenAI,
    OpenAIChatCompletionsModel,
    Runner,
    set_tracing_disabled,
)
from dotenv import load_dotenv

from backend.environment.accounts import INITIAL_BALANCE, Accounts
from backend.environment.prices import Prices
from backend.traders.tools import TraderContext, get_state, trade

load_dotenv(override=True)
set_tracing_disabled(True)


INSTRUCTIONS = """You are a test trader in a prototype harness.

You have two tools:
  - get_state: returns your current time, cash, holdings, total value,
    P&L, and your rivals' portfolio values.
  - trade(ticker, quantity): positive quantity buys, negative sells.
    Fractional shares are allowed. No short selling.

Do this sequence exactly:
  1. Call get_state to see your starting position.
  2. Buy 2 shares of AAPL.
  3. Buy 1 share of MSFT.
  4. Sell 1 share of AAPL.
  5. Call get_state again.
  6. Reply with a one-sentence summary of what changed.
"""


def make_openrouter_model(model_id: str) -> OpenAIChatCompletionsModel:
    client = AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
    )
    return OpenAIChatCompletionsModel(model=model_id, openai_client=client)


async def main() -> None:
    accounts = Accounts(":memory:")
    for tid in ("claude", "gpt", "kimi"):
        accounts.create_trader(tid)

    ctx = TraderContext(
        trader_id="claude",
        accounts=accounts,
        prices=Prices(),
        started_at=datetime.now(timezone.utc),
        duration_seconds=3600.0,
        rival_ids=["gpt", "kimi"],
    )

    agent = Agent[TraderContext](
        name="Prototype Trader (Tools)",
        instructions=INSTRUCTIONS,
        model=make_openrouter_model("openai/gpt-oss-120b"),
        tools=[get_state, trade],
    )

    result = Runner.run_streamed(
        agent,
        "Run the test sequence now.",
        context=ctx,
        max_turns=20,
    )

    async for event in result.stream_events():
        if event.type == "run_item_stream_event":
            name = event.name
            if name == "tool_called":
                tool_name = getattr(event.item.raw_item, "name", "<?>")
                args = getattr(event.item.raw_item, "arguments", "")
                print(f"[tool_called] {tool_name}({args})")
            elif name == "tool_output":
                raw = event.item.raw_item
                out = raw.get("output") if isinstance(raw, dict) else getattr(raw, "output", "")
                preview = str(out)[:200].replace("\n", " ")
                print(f"[tool_output] {preview}")
            elif name == "message_output_created":
                for part in (getattr(event.item.raw_item, "content", None) or []):
                    text = getattr(part, "text", None)
                    if text:
                        print(f"[message] {text[:300]}")

    print("\n=== FINAL OUTPUT ===")
    print(result.final_output)

    print("\n=== DB STATE (post-run) ===")
    print(f"cash = {accounts.cash('claude'):.2f}")
    print(f"holdings = {accounts.holdings('claude')}")
    print(f"trades = {len(accounts.trades('claude'))} total")

    holdings = accounts.holdings("claude")
    assert "AAPL" in holdings, "AAPL not held after run"
    assert "MSFT" in holdings, "MSFT not held after run"
    assert holdings["AAPL"]["quantity"] == 1.0, f"expected 1 AAPL, got {holdings['AAPL']['quantity']}"
    assert holdings["MSFT"]["quantity"] == 1.0, f"expected 1 MSFT, got {holdings['MSFT']['quantity']}"
    assert accounts.cash("claude") < INITIAL_BALANCE, "cash should have decreased"
    assert len(accounts.trades("claude")) == 3, "expected exactly 3 trades"
    print("\nASSERTIONS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
