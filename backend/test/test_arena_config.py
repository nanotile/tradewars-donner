"""Tests for the model-catalog config and selection resolution."""

import json

import pytest

from backend.arena.arena import ArenaConfig, DEFAULT_CONFIG_PATH, reasoning_label


@pytest.fixture
def cfg() -> ArenaConfig:
    return ArenaConfig.load(DEFAULT_CONFIG_PATH)


def test_catalog_loads_with_expected_models(cfg):
    assert set(cfg.models) == {
        "claude-opus-4-7",
        "claude-haiku-4-5",
        "gpt-5-5",
        "gpt-5-4-mini",
        "gemini-3-1-pro",
        "gemini-3-1-flash-lite",
        "kimi-k2-6",
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    }
    assert cfg.duration_seconds == 720
    assert cfg.max_tokens == 64_000


def test_each_catalog_entry_has_at_least_one_reasoning_option(cfg):
    for mid, spec in cfg.models.items():
        assert spec["display_name"]
        assert spec["provider"] in {"anthropic", "openai", "google", "openrouter", "deepseek"}
        assert spec["model"]
        opts = spec["reasoning_options"]
        assert isinstance(opts, list) and 1 <= len(opts) <= 2, mid
        for opt in opts:
            assert opt["label"]
            assert isinstance(opt["reasoning"], dict)


def test_max_preset_resolves_to_full_strength_models(cfg):
    traders = cfg.from_selections(cfg.preset_selections("max"))
    by_id = {t.id: t for t in traders}
    assert "Claude Opus 4.7 (max)" in by_id
    assert by_id["Claude Opus 4.7 (max)"].provider == "anthropic"
    assert by_id["Claude Opus 4.7 (max)"].reasoning == {"effort": "max"}
    assert "GPT 5.5 (xhigh)" in by_id
    assert by_id["GPT 5.5 (xhigh)"].reasoning == {"effort": "xhigh"}
    assert "Gemini 3.1 Pro Preview (32k)" in by_id
    assert by_id["Gemini 3.1 Pro Preview (32k)"].reasoning == {"budget_tokens": 32_000}
    assert "DeepSeek V4 Pro (max)" in by_id
    assert by_id["DeepSeek V4 Pro (max)"].provider == "deepseek"


def test_eco_preset_resolves_to_cheap_models(cfg):
    traders = cfg.from_selections(cfg.preset_selections("eco"))
    ids = {t.id for t in traders}
    assert ids == {
        "Claude Haiku 4.5 (low)",
        "GPT 5.4-mini (none)",
        "Gemini 3.1 Flash-Lite Preview (low)",
        "DeepSeek V4 Flash (off)",
    }


def test_duplicate_selections_are_disambiguated_with_hash_suffix(cfg):
    traders = cfg.from_selections([
        {"model_id": "kimi-k2-6", "reasoning_label": "xhigh"},
        {"model_id": "kimi-k2-6", "reasoning_label": "xhigh"},
        {"model_id": "kimi-k2-6", "reasoning_label": "low"},
        {"model_id": "kimi-k2-6", "reasoning_label": "xhigh"},
    ])
    assert [t.id for t in traders] == [
        "Kimi K2.6 (xhigh)",
        "Kimi K2.6 (xhigh) #2",
        "Kimi K2.6 (low)",
        "Kimi K2.6 (xhigh) #3",
    ]


def test_selection_carries_max_tokens_from_top_level(cfg):
    traders = cfg.from_selections(cfg.preset_selections("max"))
    for t in traders:
        assert t.max_tokens == 64_000


def test_unknown_model_id_raises_keyerror(cfg):
    with pytest.raises(KeyError):
        cfg.from_selections([{"model_id": "nope", "reasoning_label": "max"}])


def test_unknown_reasoning_label_raises_key_error(cfg):
    with pytest.raises(KeyError, match="Unknown reasoning_label"):
        cfg.from_selections([{"model_id": "claude-opus-4-7", "reasoning_label": "ultra"}])


def test_with_traders_returns_new_config(cfg):
    traders = cfg.from_selections(cfg.preset_selections("eco"))
    arena_cfg = cfg.with_traders(traders)
    assert arena_cfg.traders == traders
    assert arena_cfg.duration_seconds == cfg.duration_seconds
    assert arena_cfg.max_tokens == cfg.max_tokens


def test_load_from_custom_path(tmp_path):
    payload = {
        "duration_seconds": 60,
        "max_tokens": 1000,
        "models": {
            "x": {
                "display_name": "X",
                "provider": "openai",
                "model": "gpt-5.5",
                "reasoning_options": [{"label": "low", "reasoning": {"effort": "low"}}],
            }
        },
        "presets": {"max": [{"model_id": "x", "reasoning_label": "low"}]},
    }
    p = tmp_path / "c.json"
    p.write_text(json.dumps(payload))
    cfg = ArenaConfig.load(p)
    assert cfg.duration_seconds == 60
    traders = cfg.from_selections(cfg.preset_selections("max"))
    assert traders[0].id == "X (low)"


def test_reasoning_label_helper_unchanged():
    assert reasoning_label({"effort": "max"}) == "max"
    assert reasoning_label({"budget_tokens": 32_000}) == "32k"
    assert reasoning_label({"thinking": {"type": "disabled"}}) == "off"
    assert reasoning_label({"effort": "max", "thinking": {"type": "enabled"}}) == "max"
