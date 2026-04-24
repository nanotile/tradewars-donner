"""Verify GPT-5.4 + Claude Opus 4.7 reasoning via their NATIVE SDKs through
the OpenAI Agents SDK (not via OpenRouter).

  - GPT-5.4: native OpenAI path, ModelSettings(reasoning=Reasoning(effort="xhigh")).
  - Claude Opus 4.7: native Anthropic via the LitellmModel extension,
    passing the Anthropic-native `thinking` block through extra_body.
"""

from __future__ import annotations

import asyncio
import os

from agents import (
    Agent,
    ModelSettings,
    Runner,
    set_tracing_disabled,
)
from agents.extensions.models.litellm_model import LitellmModel
from dotenv import load_dotenv
from litellm.llms.anthropic.chat.transformation import AnthropicConfig
from openai.types.shared import Reasoning

load_dotenv(override=True)
set_tracing_disabled(True)


def _is_opus_4_6_or_4_7(model: str) -> bool:
    """Monkey-patch: widen LiteLLM 1.83's Opus-4.6-only `effort='max'` gate
    to include Opus 4.7 too. Let Anthropic be the source of truth.
    """
    m = model.lower()
    return any(v in m for v in (
        "opus-4-6", "opus_4_6", "opus-4.6", "opus_4.6",
        "opus-4-7", "opus_4_7", "opus-4.7", "opus_4.7",
    ))


AnthropicConfig._is_opus_4_6_model = staticmethod(_is_opus_4_6_or_4_7)

PROMPT = (
    "Prove step by step whether 1000003 is prime by trial division up to "
    "sqrt(1000003). Conclude with a single line: ANSWER: YES or ANSWER: NO."
)


async def probe_gpt() -> None:
    agent = Agent(
        name="GPT-5.4 Native",
        instructions="Solve the task carefully.",
        model="gpt-5.4",
        model_settings=ModelSettings(
            reasoning=Reasoning(effort="xhigh"),
            max_tokens=64000,
        ),
    )
    result = await Runner.run(agent, PROMPT, max_turns=3)
    answer = ""
    for line in (result.final_output or "").splitlines()[::-1]:
        if line.strip().upper().startswith("ANSWER:"):
            answer = line.strip()
            break
    print(f"[GPT-5.4 native xhigh]   {answer or '(no ANSWER line)'}\n"
          f"  output_len={len(result.final_output or '')}")


async def probe_claude() -> None:
    model = LitellmModel(
        model="anthropic/claude-opus-4-7",
        api_key=os.environ["ANTHROPIC_API_KEY"],
    )
    agent = Agent(
        name="Claude Opus 4.7 Native",
        instructions="Solve the task carefully.",
        model=model,
        model_settings=ModelSettings(
            max_tokens=64000,
            extra_args={
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "max"},
            },
        ),
    )
    result = await Runner.run(agent, PROMPT, max_turns=3)
    answer = ""
    for line in (result.final_output or "").splitlines()[::-1]:
        if line.strip().upper().startswith("ANSWER:"):
            answer = line.strip()
            break
    print(f"[Claude Opus native max]  {answer or '(no ANSWER line)'}\n"
          f"  output_len={len(result.final_output or '')}")


async def main() -> None:
    await probe_gpt()
    await probe_claude()


if __name__ == "__main__":
    asyncio.run(main())
