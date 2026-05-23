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
import json
import logging
from dataclasses import dataclass
from typing import Any

from agents import Agent, Runner

from backend.traders.mcp_servers import make_massive_mcp, make_memory_mcp
from backend.utils import utcnow
from backend.traders.models import TraderConfig, build_model, build_model_settings
from backend.traders.templates import render_cycle_input, render_system_prompt
from backend.traders.tools import TraderContext, get_state, trade

logger = logging.getLogger(__name__)

MAX_TURNS_PER_CYCLE = 200
RATIONALE_MAX_CHARS = 800
TOOL_OUTPUT_PREVIEW_CHARS = 2000
INTER_CYCLE_SLEEP_SECONDS = 10.0
ERROR_BACKOFF_SECONDS = 2.0
MCP_MAX_RETRIES = 3


def _format_output(out: Any) -> str:
    """Flatten an SDK tool output to a human-readable string.

    MCP tools return `[{"type": "input_text", "text": "..."}]` content parts;
    plain function tools return dicts or strings. Strip the wrapper so the
    frontend sees real text, not `str(list_of_dicts)` Python repr noise.
    """
    if out is None:
        return ""
    if isinstance(out, list):
        return "\n".join(_format_output(item) for item in out)
    if isinstance(out, dict):
        if out.get("type") == "input_text" and "text" in out:
            return str(out["text"])
        try:
            return json.dumps(out, default=str)
        except (TypeError, ValueError):
            return str(out)
    return str(out)


def _extract_usage(result: Any) -> dict[str, int] | None:
    """Sum token usage across all raw model responses in a completed streaming run."""
    try:
        responses = getattr(result, "raw_responses", None)
        if not responses:
            return None
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0
        reasoning_tokens = 0
        for resp in responses:
            u = getattr(resp, "usage", None)
            if u is None:
                continue
            input_tokens += getattr(u, "input_tokens", 0) or 0
            output_tokens += getattr(u, "output_tokens", 0) or 0
            details_in = getattr(u, "input_tokens_details", None)
            if details_in:
                cached_tokens += getattr(details_in, "cached_tokens", 0) or 0
            details_out = getattr(u, "output_tokens_details", None)
            if details_out:
                reasoning_tokens += getattr(details_out, "reasoning_tokens", 0) or 0
        if input_tokens == 0 and output_tokens == 0:
            return None
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_tokens": cached_tokens,
            "reasoning_tokens": reasoning_tokens,
        }
    except Exception:
        return None


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
                timestamp=utcnow().isoformat(),
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

            rationale = (result.final_output or "").strip()
            self.previous_rationale = rationale[:RATIONALE_MAX_CHARS]
            usage = _extract_usage(result)
            payload: dict[str, Any] = {"cycle": self.cycle_count, "rationale": self.previous_rationale}
            if usage:
                payload["usage"] = usage
            await self._emit("cycle_end", payload)

        except Exception as e:
            logger.exception("cycle %s failed for %s", self.cycle_count, self.config.id)
            await self._emit("error", {"cycle": self.cycle_count, "error": f"{type(e).__name__}: {e}"})
            await asyncio.sleep(ERROR_BACKOFF_SECONDS)

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
            await self._emit("tool_output", {"output": _format_output(out)[:TOOL_OUTPUT_PREVIEW_CHARS]})
        elif name == "message_output_created":
            content = getattr(raw, "content", None) or []
            texts = [getattr(p, "text", None) for p in content]
            joined = "\n".join(t for t in texts if t)
            if joined:
                await self._emit("message", {"text": joined})

    async def run_until_stopped(self, stop_event: asyncio.Event) -> None:
        for attempt in range(1, MCP_MAX_RETRIES + 1):
            if stop_event.is_set():
                return
            try:
                await self._run_with_mcps(stop_event)
                return
            except Exception:
                if attempt >= MCP_MAX_RETRIES or stop_event.is_set():
                    logger.error(
                        "MCP subprocess failed %d/%d times for %s — giving up",
                        attempt, MCP_MAX_RETRIES, self.config.id,
                    )
                    await self._emit("error", {
                        "cycle": self.cycle_count,
                        "error": f"MCP crashed {attempt} times — trader stopped",
                    })
                    return
                logger.warning(
                    "MCP subprocess crashed for %s (attempt %d/%d) — restarting",
                    self.config.id, attempt, MCP_MAX_RETRIES,
                )
                await self._emit("error", {
                    "cycle": self.cycle_count,
                    "error": f"MCP crashed (attempt {attempt}/{MCP_MAX_RETRIES}) — restarting",
                })
                await asyncio.sleep(ERROR_BACKOFF_SECONDS)

    async def _run_with_mcps(self, stop_event: asyncio.Event) -> None:
        massive = make_massive_mcp()
        memory = make_memory_mcp(self.config.id)
        async with massive, memory:
            agent = Agent[TraderContext](
                name=self.config.display_name,
                instructions=render_system_prompt(self.context.duration_seconds),
                model=build_model(self.config),
                model_settings=build_model_settings(self.config),
                mcp_servers=[massive, memory],
                tools=[get_state, trade],
            )
            while not stop_event.is_set():
                await self._run_one_cycle(agent)
                if stop_event.is_set():
                    break
                await asyncio.sleep(INTER_CYCLE_SLEEP_SECONDS)
