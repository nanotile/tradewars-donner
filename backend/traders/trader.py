"""Single-trader decision-cycle loop.

One Trader owns:
  - a TraderConfig (model + reasoning settings)
  - a TraderContext (accounts, prices, clock, rival ids)
  - an async event queue shared with the arena (events fan out to SSE)

`run_until_stopped()` holds the Massive + Memory MCPs open for the full game,
runs decision cycles back-to-back, and emits structured events for each tool
call, tool output, assistant message, cycle boundary, and error.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from agents import Agent, Runner

from backend.traders.mcp_servers import make_massive_mcp, make_memory_mcp
from backend.traders.models import TraderConfig, build_model, build_model_settings
from backend.traders.templates import SYSTEM_PROMPT, render_cycle_input
from backend.traders.tools import TraderContext, get_state, trade

logger = logging.getLogger(__name__)

MAX_TURNS_PER_CYCLE = 40


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TraderEvent:
    trader_id: str
    type: str  # cycle_start | cycle_end | tool_called | tool_output | message | error
    timestamp: str
    payload: dict[str, Any]


class Trader:
    def __init__(
        self,
        config: TraderConfig,
        context: TraderContext,
        events: asyncio.Queue[TraderEvent],
    ):
        self.config = config
        self.context = context
        self.events = events
        self.previous_rationale = ""
        self.cycle_count = 0

    async def _emit(self, type_: str, payload: dict[str, Any]) -> None:
        await self.events.put(
            TraderEvent(
                trader_id=self.config.id,
                type=type_,
                timestamp=_now(),
                payload=payload,
            )
        )

    async def _run_one_cycle(self, agent: Agent[TraderContext]) -> None:
        self.cycle_count += 1
        await self._emit("cycle_start", {"cycle": self.cycle_count})

        prompt = render_cycle_input(self.cycle_count, self.previous_rationale)

        try:
            result = Runner.run_streamed(
                agent,
                prompt,
                context=self.context,
                max_turns=MAX_TURNS_PER_CYCLE,
            )
            async for event in result.stream_events():
                await self._forward_sdk_event(event)

            rationale = result.final_output or ""
            self.previous_rationale = rationale.strip()[:800]
            await self._emit("cycle_end", {"cycle": self.cycle_count, "rationale": self.previous_rationale})

        except Exception as e:
            logger.exception("cycle %s failed for %s", self.cycle_count, self.config.id)
            await self._emit("error", {"cycle": self.cycle_count, "error": f"{type(e).__name__}: {e}"})
            await asyncio.sleep(2)

    async def _forward_sdk_event(self, event: Any) -> None:
        if event.type != "run_item_stream_event":
            return
        name = event.name
        raw = event.item.raw_item
        if name == "tool_called":
            await self._emit("tool_called", {
                "tool": getattr(raw, "name", None),
                "arguments": getattr(raw, "arguments", None),
            })
        elif name == "tool_output":
            out = raw.get("output") if isinstance(raw, dict) else getattr(raw, "output", None)
            await self._emit("tool_output", {"output": str(out)[:2000]})
        elif name == "message_output_created":
            content = getattr(raw, "content", None) or []
            texts = [getattr(p, "text", None) for p in content]
            joined = "\n".join(t for t in texts if t)
            if joined:
                await self._emit("message", {"text": joined})

    async def run_until_stopped(self, stop_event: asyncio.Event) -> None:
        massive = make_massive_mcp()
        memory = make_memory_mcp(self.config.id)
        async with massive, memory:
            agent = Agent[TraderContext](
                name=self.config.display_name,
                instructions=SYSTEM_PROMPT,
                model=build_model(self.config),
                model_settings=build_model_settings(self.config),
                mcp_servers=[massive, memory],
                tools=[get_state, trade],
            )
            while not stop_event.is_set():
                await self._run_one_cycle(agent)
                if stop_event.is_set():
                    break
                await asyncio.sleep(0.05)
