"""Tests for backend.arena.arena.ArenaConfig.load."""

import json
from pathlib import Path

from backend.arena.arena import ArenaConfig, DEFAULT_CONFIG_PATH


def test_default_config_loads_with_four_traders():
    cfg = ArenaConfig.load(DEFAULT_CONFIG_PATH)
    assert cfg.duration_seconds == 3600
    assert len(cfg.traders) == 4
    ids = [t.id for t in cfg.traders]
    assert set(ids) == {"claude", "gpt", "kimi", "deepseek"}


def test_config_preserves_reasoning_and_max_tokens_per_trader():
    cfg = ArenaConfig.load(DEFAULT_CONFIG_PATH)
    by_id = {t.id: t for t in cfg.traders}
    assert by_id["claude"].provider == "anthropic"
    assert by_id["claude"].reasoning == {"effort": "max"}
    assert by_id["gpt"].provider == "openai"
    assert by_id["gpt"].reasoning == {"effort": "xhigh"}
    assert by_id["kimi"].provider == "openrouter"
    assert by_id["deepseek"].provider == "openrouter"
    for t in cfg.traders:
        assert t.max_tokens == 64_000


def test_config_loads_from_custom_path(tmp_path: Path):
    payload = {
        "duration_seconds": 120,
        "traders": [
            {
                "id": "a", "display_name": "A",
                "provider": "openai", "model": "gpt-5.4",
                "reasoning": {"effort": "low"}, "max_tokens": 4000,
            }
        ],
    }
    p = tmp_path / "custom.json"
    p.write_text(json.dumps(payload))
    cfg = ArenaConfig.load(p)
    assert cfg.duration_seconds == 120
    assert len(cfg.traders) == 1
    assert cfg.traders[0].model == "gpt-5.4"
