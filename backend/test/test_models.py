"""Tests for backend.traders.models — factory routing per provider."""

import os

import pytest
from agents import ModelSettings, OpenAIChatCompletionsModel

from backend.traders.models import (
    TraderConfig,
    build_model,
    build_model_settings,
)


@pytest.fixture(autouse=True)
def api_keys_for_factory(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")


def test_openai_build_model_returns_model_id_string():
    cfg = TraderConfig(
        id="gpt", display_name="GPT",
        provider="openai", model="gpt-5.4",
        reasoning={"effort": "xhigh"}, max_tokens=64_000,
    )
    assert build_model(cfg) == "gpt-5.4"


def test_openai_settings_carry_reasoning_and_max_tokens():
    cfg = TraderConfig(
        id="gpt", display_name="GPT",
        provider="openai", model="gpt-5.4",
        reasoning={"effort": "xhigh"}, max_tokens=64_000,
    )
    s = build_model_settings(cfg)
    assert isinstance(s, ModelSettings)
    assert s.reasoning is not None
    assert s.reasoning.effort == "xhigh"
    assert s.max_tokens == 64_000


def test_anthropic_build_model_returns_litellm_model_and_applies_patch():
    from litellm.llms.anthropic.chat.transformation import AnthropicConfig

    from agents.extensions.models.litellm_model import LitellmModel

    cfg = TraderConfig(
        id="claude", display_name="Claude",
        provider="anthropic", model="anthropic/claude-opus-4-7",
        reasoning={"effort": "max"}, max_tokens=64_000,
    )
    model = build_model(cfg)
    assert isinstance(model, LitellmModel)
    # Monkey-patch must widen the gate to include Opus 4.7.
    assert AnthropicConfig._is_opus_4_6_model("anthropic/claude-opus-4-7") is True
    assert AnthropicConfig._is_opus_4_6_model("anthropic/claude-opus-4-6") is True
    assert AnthropicConfig._is_opus_4_6_model("anthropic/claude-opus-4-5") is False


def test_anthropic_settings_use_adaptive_thinking_with_effort():
    cfg = TraderConfig(
        id="claude", display_name="Claude",
        provider="anthropic", model="anthropic/claude-opus-4-7",
        reasoning={"effort": "max"}, max_tokens=64_000,
    )
    s = build_model_settings(cfg)
    assert s.max_tokens == 64_000
    assert s.reasoning is None  # anthropic route does not use OpenAI reasoning
    assert s.extra_args == {
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "max"},
    }


def test_openrouter_build_model_returns_chat_completions_model():
    cfg = TraderConfig(
        id="kimi", display_name="Kimi",
        provider="openrouter", model="moonshotai/kimi-k2.6",
        reasoning={"effort": "xhigh"}, max_tokens=64_000,
    )
    model = build_model(cfg)
    assert isinstance(model, OpenAIChatCompletionsModel)


def test_openrouter_settings_put_reasoning_in_extra_body():
    cfg = TraderConfig(
        id="kimi", display_name="Kimi",
        provider="openrouter", model="moonshotai/kimi-k2.6",
        reasoning={"effort": "xhigh"}, max_tokens=64_000,
    )
    s = build_model_settings(cfg)
    assert s.max_tokens == 64_000
    assert s.extra_body is not None
    assert s.extra_body["reasoning"] == {"effort": "xhigh"}
    assert s.extra_body["include_reasoning"] is True


def test_unknown_provider_raises():
    cfg = TraderConfig(
        id="???", display_name="?",
        provider="mystery", model="x",
        reasoning={"effort": "xhigh"}, max_tokens=100,
    )
    with pytest.raises(ValueError, match="Unknown provider"):
        build_model(cfg)
    with pytest.raises(ValueError, match="Unknown provider"):
        build_model_settings(cfg)
