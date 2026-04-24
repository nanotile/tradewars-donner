"""Phase 2 step 2: verify reasoning-effort passthrough per production model.

For each of the 4 production models, send a trivial request via OpenRouter
with an explicit reasoning configuration and confirm:
  (a) the request succeeds
  (b) the response reports reasoning token usage, proving the knob took effect

We route all 4 through OpenRouter for uniformity (matches the user's "OpenAI
Agents SDK abstractions" stance — OpenRouter speaks OpenAI chat-completions).
"""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv(override=True)

# OpenRouter effort ladder: minimal / low / medium / high / xhigh. xhigh ≈ 95%
# of max_tokens as reasoning budget — our target "max reasoning per provider".
MODELS = [
    ("claude",   "anthropic/claude-opus-4-7",  {"effort": "xhigh"}),
    ("gpt",      "openai/gpt-5.4",             {"effort": "xhigh"}),
    ("kimi",     "moonshotai/kimi-k2.6",       {"effort": "xhigh"}),
    ("deepseek", "deepseek/deepseek-v4-pro",   {"effort": "xhigh"}),
]


# A problem thorny enough to force reasoning, not just recall.
PROMPT = (
    "Prove step by step whether 1000003 is prime by trial division up to "
    "sqrt(1000003). Conclude with a single line: ANSWER: YES or ANSWER: NO."
)


async def probe(client: AsyncOpenAI, name: str, model_id: str, reasoning: dict) -> None:
    try:
        r = await client.chat.completions.create(
            model=model_id,
            messages=[{"role": "user", "content": PROMPT}],
            max_tokens=16000,
            extra_body={"reasoning": reasoning, "include_reasoning": True},
        )
        content = (r.choices[0].message.content or "").strip()
        usage = r.usage
        reasoning_tokens = getattr(
            getattr(usage, "completion_tokens_details", None),
            "reasoning_tokens",
            None,
        )
        answer = ""
        for line in content.splitlines()[::-1]:
            if line.strip().upper().startswith("ANSWER:"):
                answer = line.strip()
                break
        print(
            f"[OK] {name:>8} ({model_id})\n"
            f"     reasoning_tokens={reasoning_tokens} completion_tokens={usage.completion_tokens}\n"
            f"     {answer or '(no ANSWER line found)'}"
        )
    except Exception as e:
        print(f"[FAIL] {name:>8} ({model_id}): {type(e).__name__}: {str(e)[:200]}")


async def main() -> None:
    client = AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
    )
    for name, model_id, reasoning in MODELS:
        await probe(client, name, model_id, reasoning)


if __name__ == "__main__":
    asyncio.run(main())
