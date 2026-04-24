"""Phase 2 single-trader prototype.

Spins up one Agent with:
  - Massive MCP stdio server (price/news/fundamentals/technicals)
  - Memory MCP stdio server (per-trader knowledge graph)

Runs one decision-cycle, streams events, prints tool calls and final output.

Uses a cheap OpenRouter model (`openai/gpt-oss-120b`) so we can iterate on the
plumbing without burning budget on reasoning models. Production model swap is
Phase 2 step 2 (verify reasoning-effort passthrough).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from agents import (
    Agent,
    AsyncOpenAI,
    OpenAIChatCompletionsModel,
    Runner,
    set_tracing_disabled,
)
from agents.mcp import MCPServerStdio
from dotenv import load_dotenv

load_dotenv(override=True)
set_tracing_disabled(True)

REPO_ROOT = Path(__file__).resolve().parents[2]
MEMORY_DIR = REPO_ROOT / "backend" / "environment" / "memory"
MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def make_massive_mcp() -> MCPServerStdio:
    api_key = os.environ["MASSIVE_API_KEY"]
    return MCPServerStdio(
        name="Massive",
        params={
            "command": "mcp_massive",
            "args": [],
            "env": {**os.environ, "MASSIVE_API_KEY": api_key},
        },
        cache_tools_list=True,
        client_session_timeout_seconds=60,
    )


def make_memory_mcp(trader_id: str) -> MCPServerStdio:
    memory_file = MEMORY_DIR / f"trader_{trader_id}.jsonl"
    return MCPServerStdio(
        name=f"Memory[{trader_id}]",
        params={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-memory"],
            "env": {**os.environ, "MEMORY_FILE_PATH": str(memory_file)},
        },
        cache_tools_list=True,
        client_session_timeout_seconds=60,
    )


def make_openrouter_model(model_id: str) -> OpenAIChatCompletionsModel:
    """Route a model through OpenRouter using an OpenAI-compatible client."""
    client = AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
    )
    return OpenAIChatCompletionsModel(model=model_id, openai_client=client)


INSTRUCTIONS = """You are a test trader running inside a prototype harness.
You have access to two MCP servers:
  - Massive: realtime + historic equity data, news, fundamentals, technicals.
  - Memory: a knowledge graph for persistent notes across decision cycles.

For this prototype, do the following in order:
  1. Use Massive to get the latest price for AAPL.
  2. Use Massive to get a recent technical indicator for AAPL (e.g. SMA 20 or RSI 14).
  3. Use Massive to get a fundamental financial metric for AAPL (e.g. latest
     quarterly financials or market cap).
  4. Use Massive to get any recent news for NVDA.
  5. Save a short observation about NVDA to your memory. First `create_entities`
     for NVDA (entityType "stock") if it does not exist, then `add_observations`.
  6. Read memory back with `read_graph` to confirm the observation was saved.
  7. Reply with a final message summarising what you found in bullets:
     AAPL price, AAPL technical indicator value, AAPL fundamental value,
     NVDA headline, memory confirmation.
"""


async def main() -> None:
    massive = make_massive_mcp()
    memory = make_memory_mcp("proto")

    async with massive, memory:
        agent = Agent(
            name="Prototype Trader",
            instructions=INSTRUCTIONS,
            model=make_openrouter_model("openai/gpt-oss-120b"),
            mcp_servers=[massive, memory],
        )

        result = Runner.run_streamed(
            agent,
            "Run the prototype sequence now.",
            max_turns=30,
        )

        async for event in result.stream_events():
            if event.type == "run_item_stream_event":
                name = event.name
                if name == "tool_called":
                    tool_name = getattr(event.item.raw_item, "name", "<unknown>")
                    print(f"[tool_called] {tool_name}")
                elif name == "tool_output":
                    raw = event.item.raw_item
                    out = raw.get("output") if isinstance(raw, dict) else getattr(raw, "output", "")
                    preview = str(out)[:200].replace("\n", " ")
                    print(f"[tool_output] {preview}")
                elif name == "message_output_created":
                    raw = event.item.raw_item
                    content = getattr(raw, "content", None) or []
                    for part in content:
                        text = getattr(part, "text", None)
                        if text:
                            print(f"[message] {text[:400]}")

        print("\n=== FINAL OUTPUT ===")
        print(result.final_output)


if __name__ == "__main__":
    asyncio.run(main())
