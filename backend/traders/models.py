"""Per-trader model + ModelSettings factory.

Three routes, selected by `TraderConfig.provider`:
  - "openai"     — native OpenAI path. Pass model id as a string.
  - "anthropic"  — native Anthropic via LitellmModel, with the LiteLLM 1.83
                   monkey-patch applied so Opus 4.7 can use effort="max".
  - "openrouter" — OpenAI-compat Chat Completions client routed at OpenRouter.
                   Reasoning knob goes into extra_body.reasoning.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from agents import AsyncOpenAI, ModelSettings, OpenAIChatCompletionsModel
from agents.extensions.models.litellm_model import LitellmModel
from litellm.llms.anthropic.chat.transformation import AnthropicConfig
from openai.types.shared import Reasoning

_ANTHROPIC_PATCHED = False


def _install_anthropic_monkey_patch() -> None:
    """Widen LiteLLM's Opus-4.6-only gate on `output_config.effort="max"`.

    LiteLLM 1.83 whitelists only Opus 4.6 — Opus 4.7 accepts max too.
    Idempotent; safe to call multiple times. Drop when LiteLLM 1.84+ ships.
    """
    global _ANTHROPIC_PATCHED
    if _ANTHROPIC_PATCHED:
        return

    def _is_opus_4_6_or_4_7(model: str) -> bool:
        m = model.lower()
        return any(v in m for v in (
            "opus-4-6", "opus_4_6", "opus-4.6", "opus_4.6",
            "opus-4-7", "opus_4_7", "opus-4.7", "opus_4.7",
        ))

    AnthropicConfig._is_opus_4_6_model = staticmethod(_is_opus_4_6_or_4_7)
    _ANTHROPIC_PATCHED = True


@dataclass(frozen=True)
class TraderConfig:
    id: str
    display_name: str
    provider: str          # "openai" | "anthropic" | "openrouter"
    model: str             # provider-specific model id
    reasoning: dict[str, Any]  # {"effort": "max" | "xhigh" | "high" | ...}
    max_tokens: int


def build_model(config: TraderConfig) -> Any:
    """Return the right Model instance (or model-id string) for this provider."""
    if config.provider == "openai":
        return config.model
    if config.provider == "anthropic":
        _install_anthropic_monkey_patch()
        return LitellmModel(
            model=config.model,
            api_key=os.environ["ANTHROPIC_API_KEY"],
        )
    if config.provider == "openrouter":
        client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        )
        return OpenAIChatCompletionsModel(
            model=config.model,
            openai_client=client,
        )
    raise ValueError(f"Unknown provider: {config.provider}")


def build_model_settings(config: TraderConfig) -> ModelSettings:
    """Provider-appropriate ModelSettings carrying the reasoning knobs."""
    if config.provider == "openai":
        return ModelSettings(
            reasoning=Reasoning(effort=config.reasoning["effort"]),
            max_tokens=config.max_tokens,
        )
    if config.provider == "anthropic":
        return ModelSettings(
            max_tokens=config.max_tokens,
            extra_args={
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": config.reasoning["effort"]},
            },
        )
    if config.provider == "openrouter":
        return ModelSettings(
            max_tokens=config.max_tokens,
            extra_body={"reasoning": config.reasoning, "include_reasoning": True},
        )
    raise ValueError(f"Unknown provider: {config.provider}")
