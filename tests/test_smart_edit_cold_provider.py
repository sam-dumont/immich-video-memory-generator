"""Cold smart-edit provider runs have an enforceable, comparable boundary."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import probe_smart_edit_matrix as probe

from immich_memories.config_loader import Config


def test_zai_hosted_config_is_no_thinking_and_accepts_strict_vision(monkeypatch) -> None:
    monkeypatch.setenv("ZAI_API_KEY", "test-key")
    monkeypatch.setenv("ZAI_BASE_URL", "https://api.z.ai/api/paas/v4")

    llm = probe._hosted_llm_config("zai", "glm-4.6v")

    assert llm.provider == "zai"
    assert llm.model == "glm-4.6v"
    assert llm.thinking is False
    assert llm.no_thinking_params == {"thinking": {"type": "disabled"}}
    assert llm.send_image_detail is False


def test_melious_vision_config_does_not_invent_a_thinking_dialect(monkeypatch) -> None:
    monkeypatch.setenv("MELIOUS_AI_KEY", "test-key")
    monkeypatch.setenv("MELIOUS_AI_BASE_URL", "https://api.melious.ai/v1")

    llm = probe._hosted_llm_config("melious", "qwen3-vl-235b-a22b-instruct")

    assert llm.provider == "openai-compatible"
    assert llm.no_thinking_params == {}
    assert llm.extra_params == {}


def test_melious_qwen38_explicitly_disables_reasoning(monkeypatch) -> None:
    monkeypatch.setenv("MELIOUS_AI_KEY", "test-key")
    monkeypatch.setenv("MELIOUS_AI_BASE_URL", "https://api.melious.ai/v1")

    llm = probe._hosted_llm_config("melious", "qwen3.8-27b")

    assert llm.provider == "openai-compatible"
    assert llm.thinking is False
    assert llm.no_thinking_params == {}
    assert llm.extra_params == {"reasoning_effort": "none"}


def test_cold_config_moves_every_derived_cache_under_the_new_root(tmp_path: Path) -> None:
    root = tmp_path / "cold-provider"

    cold = probe._cold_config(Config(), root)

    assert cold.cache.cache_path == root
    assert cold.cache.database_path == root / "cache.db"


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-5.6-luna", 0.000404),
        ("glm-4.6v", 0.00043),
        ("qwen3.8-27b", 0.00082),
        ("qwen3-vl-235b-a22b-instruct", 0.00056),
        ("qwen2.5-vl-72b-instruct", 0.0004),
    ],
)
def test_hosted_cost_uses_reported_uncached_tokens(model: str, expected: float) -> None:
    cost = probe._estimated_provider_cost(
        model,
        {
            "llm_prompt_tokens": 1_000,
            "llm_cached_prompt_tokens": 200,
            "llm_completion_tokens": 200,
        },
    )

    assert cost == pytest.approx(expected)
