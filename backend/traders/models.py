"""Per-trader model + ModelSettings factory.

Five routes, selected by `TraderConfig.provider`:
  - "openai"     — native OpenAI path. Pass model id as a string.
  - "anthropic"  — native Anthropic via LitellmModel, with the LiteLLM 1.83
                   monkey-patch applied so Opus 4.7 can use effort="max".
  - "google"     — native Gemini via LitellmModel (`gemini/<model>` prefix).
                   Thinking controlled by LiteLLM's unified `reasoning_effort`.
  - "deepseek"   — native DeepSeek via the OpenAI-compat endpoint at
                   api.deepseek.com. Reasoning knobs (`reasoning_effort`,
                   `thinking`) go into extra_body.
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
    if config.provider == "google":
        return LitellmModel(
            model=config.model,
            api_key=os.environ["GOOGLE_API_KEY"],
        )
    if config.provider == "deepseek":
        client = AsyncOpenAI(
            base_url="https://api.deepseek.com",
            api_key=os.environ["DEEPSEEK_API_KEY"],
        )
        return OpenAIChatCompletionsModel(
            model=config.model,
            openai_client=client,
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
        effort = config.reasoning.get("effort")
        kwargs: dict[str, Any] = {"max_tokens": config.max_tokens}
        # "none" disables reasoning; not always in the SDK's Literal. Omit the
        # field so the OpenAI default (no reasoning) applies.
        if effort and effort != "none":
            kwargs["reasoning"] = Reasoning(effort=effort)
        return ModelSettings(**kwargs)
    if config.provider == "anthropic":
        # Two thinking shapes, selected by config:
        #   {"budget_tokens": N}             → legacy "enabled" form (Haiku 4.5, Sonnet 4.x)
        #   {"effort": "max"\|"high"\|...}    → new "adaptive" form (Opus 4.7 only)
        # If both are present (e.g. Haiku eco where we want an "(low)" label
        # but the API needs an explicit budget), budget_tokens wins — the
        # adaptive form will be rejected by non-Opus-4.7 models.
        reasoning = config.reasoning
        if "budget_tokens" in reasoning:
            thinking = {"type": "enabled", "budget_tokens": int(reasoning["budget_tokens"])}
            extra: dict[str, Any] = {"thinking": thinking}
        else:
            extra = {
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": reasoning["effort"]},
            }
        # Anthropic's 2026 "automatic caching" mode — a single top-level
        # cache_control field opts in the system prompt + tool defs.
        # Still required; Anthropic does not cache implicitly.
        extra["cache_control"] = {"type": "ephemeral"}
        return ModelSettings(max_tokens=config.max_tokens, extra_args=extra)
    if config.provider == "google":
        # Two shapes supported: {"budget_tokens": N} → LiteLLM forwards as
        # Google's `thinkingConfig` with explicit budget (the max-reasoning path);
        # {"effort": "..."} → LiteLLM's unified reasoning_effort mapping.
        reasoning = config.reasoning
        extra: dict[str, Any] = {}
        if "budget_tokens" in reasoning:
            extra["thinking"] = {
                "type": "enabled",
                "budget_tokens": reasoning["budget_tokens"],
            }
        elif "effort" in reasoning:
            extra["reasoning_effort"] = reasoning["effort"]
        return ModelSettings(max_tokens=config.max_tokens, extra_args=extra)
    if config.provider == "deepseek":
        # Both `reasoning_effort` (if present) and `thinking` (if present)
        # go into the request body. Max mode sends both; eco sends just
        # thinking.disabled.
        body: dict[str, Any] = {}
        if effort := config.reasoning.get("effort"):
            body["reasoning_effort"] = effort
        if thinking := config.reasoning.get("thinking"):
            body["thinking"] = thinking
        return ModelSettings(
            max_tokens=config.max_tokens,
            extra_body=body or None,
        )
    if config.provider == "openrouter":
        return ModelSettings(
            max_tokens=config.max_tokens,
            extra_body={"reasoning": config.reasoning, "include_reasoning": True},
        )
    raise ValueError(f"Unknown provider: {config.provider}")
