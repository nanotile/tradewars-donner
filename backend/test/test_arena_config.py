"""Tests for backend.arena.arena.ArenaConfig.load + reasoning_label helper."""

import json
from pathlib import Path

from backend.arena.arena import ArenaConfig, DEFAULT_CONFIG_PATH, reasoning_label


def test_default_config_loads_four_traders():
    cfg = ArenaConfig.load(DEFAULT_CONFIG_PATH)
    assert [t.id for t in cfg.traders] == ["claude", "gpt", "gemini", "deepseek"]


def test_max_mode_selects_max_variants():
    cfg = ArenaConfig.load(DEFAULT_CONFIG_PATH, max_mode=True)
    by_id = {t.id: t for t in cfg.traders}
    assert by_id["claude"].model == "anthropic/claude-opus-4-7"
    assert by_id["claude"].reasoning == {"effort": "max"}
    assert by_id["gpt"].model == "gpt-5.5"
    assert by_id["gpt"].reasoning == {"effort": "xhigh"}
    assert by_id["gemini"].model == "gemini/gemini-3.1-pro-preview"
    assert by_id["gemini"].reasoning == {"budget_tokens": 32_000}
    assert by_id["deepseek"].provider == "deepseek"
    assert by_id["deepseek"].model == "deepseek-v4-pro"
    assert by_id["deepseek"].reasoning == {
        "effort": "max",
        "thinking": {"type": "enabled"},
    }


def test_eco_mode_selects_eco_variants():
    cfg = ArenaConfig.load(DEFAULT_CONFIG_PATH, max_mode=False)
    by_id = {t.id: t for t in cfg.traders}
    assert by_id["claude"].model == "anthropic/claude-haiku-4-5"
    # Haiku 4.5 doesn't support adaptive thinking, so eco carries an explicit
    # budget_tokens that models.py translates to the enabled form. Effort stays
    # for the UI label only.
    assert by_id["claude"].reasoning == {"effort": "low", "budget_tokens": 1024}
    assert by_id["gpt"].model == "gpt-5.4-mini"
    assert by_id["gpt"].reasoning == {"effort": "none"}
    assert by_id["gemini"].model == "gemini/gemini-3.1-flash-lite-preview"
    assert by_id["gemini"].reasoning == {"effort": "low"}
    assert by_id["deepseek"].provider == "deepseek"
    assert by_id["deepseek"].model == "deepseek-v4-flash"
    assert by_id["deepseek"].reasoning == {"thinking": {"type": "disabled"}}


def test_display_names_match_mode():
    cfg_max = ArenaConfig.load(DEFAULT_CONFIG_PATH, max_mode=True)
    cfg_eco = ArenaConfig.load(DEFAULT_CONFIG_PATH, max_mode=False)
    max_names = {t.id: t.display_name for t in cfg_max.traders}
    eco_names = {t.id: t.display_name for t in cfg_eco.traders}
    assert max_names["claude"] == "Claude Opus 4.7"
    assert max_names["gpt"] == "GPT 5.5"
    assert max_names["deepseek"] == "DeepSeek V4 Pro"
    assert eco_names["gpt"] == "GPT 5.4-mini"
    assert eco_names["claude"] == "Claude Haiku 4.5"
    assert eco_names["deepseek"] == "DeepSeek V4 Flash"


def test_max_tokens_is_per_trader_and_shared_across_modes():
    cfg_max = ArenaConfig.load(DEFAULT_CONFIG_PATH, max_mode=True)
    cfg_eco = ArenaConfig.load(DEFAULT_CONFIG_PATH, max_mode=False)
    for t in cfg_max.traders + cfg_eco.traders:
        assert t.max_tokens == 64_000


def test_config_loads_from_custom_path(tmp_path: Path):
    payload = {
        "duration_seconds": 120,
        "traders": [
            {
                "id": "a",
                "max_tokens": 4000,
                "max": {
                    "display_name": "A-max",
                    "provider": "openai",
                    "model": "gpt-5.5",
                    "reasoning": {"effort": "xhigh"},
                },
                "eco": {
                    "display_name": "A-eco",
                    "provider": "openai",
                    "model": "gpt-5.4-mini",
                    "reasoning": {"effort": "none"},
                },
            }
        ],
    }
    p = tmp_path / "custom.json"
    p.write_text(json.dumps(payload))

    cfg = ArenaConfig.load(p, max_mode=True)
    assert cfg.duration_seconds == 120
    assert cfg.traders[0].display_name == "A-max"
    assert cfg.traders[0].model == "gpt-5.5"

    cfg = ArenaConfig.load(p, max_mode=False)
    assert cfg.traders[0].display_name == "A-eco"
    assert cfg.traders[0].model == "gpt-5.4-mini"


def test_reasoning_label_effort():
    assert reasoning_label({"effort": "max"}) == "max"
    assert reasoning_label({"effort": "none"}) == "none"


def test_reasoning_label_budget_tokens():
    assert reasoning_label({"budget_tokens": 32_000}) == "32k"
    assert reasoning_label({"budget_tokens": 500}) == "500"


def test_reasoning_label_thinking_toggle():
    assert reasoning_label({"thinking": {"type": "disabled"}}) == "off"
    assert reasoning_label({"thinking": {"type": "enabled"}}) == "on"


def test_reasoning_label_effort_wins_when_both_present():
    # DeepSeek max mode carries both effort + thinking; effort is the
    # user-visible summary.
    assert reasoning_label({"effort": "max", "thinking": {"type": "enabled"}}) == "max"
