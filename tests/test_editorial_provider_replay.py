"""Frozen provider replays stop before visual acquisition."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import probe_editorial_provider_replay as replay
import probe_smart_edit_matrix as matrix

from immich_memories.config_models_llm import LLMConfig


def _asset(alias: str, moment_id: str, taken_at: str) -> dict[str, object]:
    return {
        "alias": alias,
        "asset_id": f"private-{alias}",
        "moment_id": moment_id,
        "taken_at": taken_at,
        "media_kind": "photo",
        "favourite": False,
        "description": f"Visible scene for {moment_id}.",
        "context": [],
        "selected": True,
    }


def test_melious_replay_uses_the_verified_qwen38_no_thinking_dialect(monkeypatch) -> None:
    monkeypatch.setenv("MELIOUS_AI_KEY", "test-key")
    monkeypatch.setenv("MELIOUS_AI_BASE_URL", "https://api.melious.ai/v1")

    config = replay._provider_config("melious", "qwen3.8-27b")

    assert config.thinking is False
    assert config.no_thinking_params == {}
    assert config.extra_params == {"reasoning_effort": "none"}


def test_configured_replay_reuses_the_self_hosted_transport(monkeypatch) -> None:
    local = LLMConfig(
        provider="openai-compatible",
        base_url="http://localhost:9999/v1",
        model="configured-model",
        thinking=True,
        no_thinking_params={"chat_template_kwargs": {"enable_thinking": False}},
    )
    monkeypatch.setattr(replay, "get_config", lambda: SimpleNamespace(llm=local))

    config = replay._provider_config("configured", "local-qwen")

    assert config.provider == "openai-compatible"
    assert config.model == "local-qwen"
    assert config.thinking is False
    assert config.no_thinking_params == {"chat_template_kwargs": {"enable_thinking": False}}


def test_frozen_legacy_motion_labels_are_replayed_as_still_evidence() -> None:
    for media_kind in ("live_photo", "motion", "video"):
        legacy = _asset("A010", "M001", "2020-01-01T12:00:00+00:00")
        legacy["media_kind"] = media_kind

        candidates = replay._fine_cut_candidates(
            {
                "counts": {"fine_cut_candidates": 1},
                "assets": [legacy],
            }
        )

        assert candidates[0].media_kind == "photo"
        assert media_kind not in candidates[0].wall_line()


def test_frozen_motion_backed_video_keeps_its_effective_medium() -> None:
    observed = _asset("A010", "M001", "2020-01-01T12:00:00+00:00")
    observed.update(
        {
            "media_kind": "video",
            "source_media_kind": "video",
            "motion_observed": True,
            "motion_contribution": "meaningful",
        }
    )

    candidates = replay._fine_cut_candidates(
        {
            "counts": {"fine_cut_candidates": 1},
            "assets": [observed],
        }
    )

    assert candidates[0].media_kind == "video"
    assert candidates[0].motion_observed is True
    assert candidates[0].render_mode == "motion"


def test_frozen_video_with_useless_motion_replays_as_a_photo() -> None:
    observed = _asset("A010", "M001", "2020-01-01T12:00:00+00:00")
    observed.update(
        {
            "media_kind": "video",
            "source_media_kind": "video",
            "motion_observed": True,
            "motion_contribution": "still_sufficient",
            "motion_reason": "The framing barely changes.",
        }
    )

    candidates = replay._fine_cut_candidates(
        {
            "counts": {"fine_cut_candidates": 1},
            "assets": [observed],
        }
    )

    assert candidates[0].media_kind == "photo"
    assert candidates[0].motion_observed is True
    assert candidates[0].render_mode == "still"


def test_frozen_grounded_live_photo_replays_as_live_motion() -> None:
    observed = _asset("A010", "M001", "2020-01-01T12:00:00+00:00")
    observed.update(
        {
            "media_kind": "live_photo",
            "source_media_kind": "live_photo",
            "motion_observed": True,
            "motion_contribution": "meaningful",
            "motion_reason": "A quiet expression changes into a laugh.",
        }
    )

    candidates = replay._fine_cut_candidates(
        {
            "counts": {"fine_cut_candidates": 1},
            "assets": [observed],
        }
    )

    assert candidates[0].media_kind == "live-motion"
    assert candidates[0].render_mode == "motion"


def test_final_cut_stage_replays_frozen_hierarchical_assets_without_vision(
    monkeypatch, tmp_path: Path
) -> None:
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    result_path = case_dir / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "case": {
                    "key": "blind-year",
                    "label": "A blind year",
                    "product": "year_in_review",
                    "brief": "Make a truthful memory.",
                },
                "edit": {
                    "configuration": {
                        "shape": "hierarchical",
                        "text_model": "local-model",
                        "capacity": {"moment_capacity": 2},
                    },
                    "thesis": {"thesis": "A year of change."},
                    "chapter_readings": [
                        {"chapter_id": "C001", "label": "Earlier", "moment_ids": ["M001"]},
                        {"chapter_id": "C002", "label": "Later", "moment_ids": ["M002"]},
                    ],
                    "allocation": {
                        "allocations": [
                            {"chapter_id": "C001", "slots": 1},
                            {"chapter_id": "C002", "slots": 1},
                        ]
                    },
                },
            }
        )
    )
    (case_dir / "final-cut.json").write_text(
        json.dumps(
            {
                "configuration": {"capacity": 2, "shape": "hierarchical"},
                "counts": {"fine_cut_candidates": 2},
                "selection": {"keep": [], "required_asset_ids": []},
                "assets": [
                    _asset("A010", "M001", "2020-01-01T12:00:00+00:00"),
                    _asset("A020", "M002", "2020-02-01T12:00:00+00:00"),
                ],
            }
        )
    )

    prompts: list[str] = []

    async def answer(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        if "reduce-only global review" in prompt:
            return json.dumps(
                {
                    "schema_version": "description-final-sequence-review-v1",
                    "keep": ["A010"],
                    "cut": [{"asset_id": "A020", "reason": "Repeats the stronger beat."}],
                    "overall_reason": "One grounded scene is enough.",
                }
            )
        return json.dumps(
            {
                "schema_version": "description-final-asset-cut-v1",
                "keep": [{"asset_id": "A001", "reason": "Distinct lived scene."}],
                "comparisons": [],
                "overall_reason": "The sequence is concise.",
            }
        )

    monkeypatch.setattr(matrix, "query_llm", answer)
    monkeypatch.setenv("OPENAI_KEY", "test-key")
    args = SimpleNamespace(
        result=result_path,
        out=tmp_path / "replay.json",
        provider="openai",
        model="gpt-5.6-terra",
        stage="final-cut",
        timeout_seconds=30,
    )

    result = asyncio.run(replay._replay(args))

    assert result["status"] == "complete"
    assert result["configuration"]["frozen_final_candidates"] is True
    assert result["configuration"]["vision_calls"] == 0
    assert result["counts"]["pre_global_review_assets"] == 2
    assert [row["asset_id"] for row in result["selection"]["keep"]] == ["A010"]
    assert result["metrics"]["total"]["elapsed_seconds"] >= 0
    assert len(prompts) == 3


def test_global_review_stage_replays_only_the_frozen_pre_global_wall(
    monkeypatch, tmp_path: Path
) -> None:
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    result_path = case_dir / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "case": {
                    "key": "blind-year",
                    "label": "A blind year",
                    "product": "year_in_review",
                    "brief": "Make a truthful memory.",
                },
                "edit": {
                    "configuration": {"shape": "hierarchical", "text_model": "local-model"},
                    "thesis": {"thesis": "A year of change."},
                },
            }
        )
    )
    assets = [
        _asset("A010", "M001", "2020-01-01T12:00:00+00:00"),
        _asset("A020", "M002", "2020-02-01T12:00:00+00:00"),
        _asset("A030", "M003", "2020-03-01T12:00:00+00:00"),
    ]
    (case_dir / "final-cut.json").write_text(
        json.dumps(
            {
                "configuration": {"capacity": 3, "shape": "hierarchical"},
                "counts": {"fine_cut_candidates": 3},
                "selection": {
                    "keep": [{"asset_id": "A010", "reason": "Reference winner."}],
                    "pre_global_review_keep": [
                        {"asset_id": "A010", "reason": "Chapter winner."},
                        {"asset_id": "A020", "reason": "Chapter winner."},
                    ],
                    "required_asset_ids": [],
                },
                "assets": assets,
            }
        )
    )

    prompts: list[str] = []

    async def answer(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        return json.dumps(
            {
                "schema_version": "description-final-sequence-review-v1",
                "keep": ["A010"],
                "cut": [{"asset_id": "A020", "reason": "Repeats the stronger beat."}],
                "overall_reason": "One scene is enough.",
            }
        )

    monkeypatch.setattr(matrix, "query_llm", answer)
    monkeypatch.setenv("OPENAI_KEY", "test-key")
    args = SimpleNamespace(
        result=result_path,
        out=tmp_path / "replay.json",
        provider="openai",
        model="gpt-5.6-luna",
        stage="global-review",
        timeout_seconds=30,
    )

    result = asyncio.run(replay._replay(args))

    assert result["status"] == "complete"
    assert result["configuration"]["vision_calls"] == 0
    assert result["counts"] == {
        "wall_assets": 2,
        "selected_assets": 1,
        "represented_moments": 1,
    }
    assert [row["asset_id"] for row in result["selection"]["keep"]] == ["A010"]
    assert len(prompts) == 1


def test_deliberation_stage_can_reopen_the_complete_frozen_candidate_pool(
    monkeypatch, tmp_path: Path
) -> None:
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    result_path = case_dir / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "case": {
                    "key": "blind-year",
                    "label": "A blind year",
                    "product": "year_in_review",
                    "brief": "Make a truthful memory.",
                },
                "edit": {
                    "configuration": {"shape": "hierarchical", "text_model": "local-model"},
                    "thesis": {"thesis": "A year of change."},
                },
            }
        )
    )
    assets = [
        _asset("A010", "M001", "2020-01-01T12:00:00+00:00"),
        _asset("A020", "M002", "2020-02-01T12:00:00+00:00"),
        _asset("A030", "M003", "2020-03-01T12:00:00+00:00"),
    ]
    (case_dir / "final-cut.json").write_text(
        json.dumps(
            {
                "configuration": {"capacity": 2, "shape": "hierarchical"},
                "counts": {"fine_cut_candidates": 3},
                "selection": {
                    "keep": [{"asset_id": "A010", "reason": "Reference winner."}],
                    "pre_global_review_keep": [
                        {"asset_id": "A010", "reason": "Chapter winner."},
                        {"asset_id": "A020", "reason": "Chapter winner."},
                    ],
                    "required_asset_ids": [],
                },
                "assets": assets,
            }
        )
    )
    answers = iter(
        (
            json.dumps(
                {
                    "schema_version": "description-final-sequence-review-v2",
                    "cut": [],
                    "overall_reason": "Both initial beats remain distinct.",
                }
            ),
            json.dumps(
                {
                    "schema_version": "description-final-asset-audit-v1",
                    "verdict": "revise",
                    "findings": [
                        {
                            "kind": "missing_place_or_progression",
                            "asset_ids": ["A020"],
                            "visible_defect": "The current beat lacks visible progression.",
                            "missing_contribution": "A later state, if the pool contains it.",
                        }
                    ],
                    "overall_reason": "One grounded gap warrants reopening the pools.",
                }
            ),
            json.dumps(
                {
                    "schema_version": "description-final-asset-reconsideration-v1",
                    "changes": [
                        {
                            "finding_id": "F001",
                            "add_asset_ids": ["A030"],
                            "remove_asset_ids": ["A020"],
                            "reason": "A030 visibly supplies the later state.",
                        }
                    ],
                    "overall_reason": "One grounded replacement improves progression.",
                }
            ),
            json.dumps(
                {
                    "schema_version": "description-final-asset-delta-validation-v1",
                    "verdict": "accept",
                    "supported_finding_ids": ["F001"],
                    "reason": "The replacement resolves the exact cited gap.",
                }
            ),
            json.dumps(
                {
                    "schema_version": "description-final-asset-audit-v1",
                    "verdict": "stable",
                    "findings": [],
                    "overall_reason": "The revised corpus has distinct contributions.",
                }
            ),
            json.dumps(
                {
                    "schema_version": "description-final-sequence-review-v2",
                    "cut": [],
                    "overall_reason": "The verified pair remains concise.",
                }
            ),
        )
    )
    prompts: list[str] = []

    async def answer(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr(matrix, "query_llm", answer)
    monkeypatch.setenv("OPENAI_KEY", "test-key")
    args = SimpleNamespace(
        result=result_path,
        out=tmp_path / "replay.json",
        provider="openai",
        model="gpt-5.6-luna",
        stage="deliberation",
        timeout_seconds=30,
        max_iterations=3,
    )

    result = asyncio.run(replay._replay(args))

    assert result["status"] == "complete"
    assert result["configuration"]["vision_calls"] == 0
    assert result["counts"]["candidate_pool_assets"] == 3
    assert result["counts"]["initial_wall_assets"] == 2
    assert result["counts"]["selected_assets"] == 2
    assert result["counts"]["deliberation_iterations"] == 2
    assert [row["asset_id"] for row in result["selection"]["keep"]] == ["A010", "A030"]
    assert result["deliberation"]["stop_reason"] == "stable"
    assert len(prompts) == 6
    assert "A030|M003|" in prompts[2]


def test_default_stage_still_replays_the_frozen_moment_wall(monkeypatch, tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "cards.json").write_text(
        json.dumps({"cards": [{"moment_id": "M001"}, {"moment_id": "M002"}]})
    )
    old_thesis = {
        "thesis": "The reference reading.",
        "sustained_threads": [],
        "turning_points": [],
        "ordinary_texture": [],
    }
    result_path = case_dir / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "case": {"key": "month"},
                "edit": {
                    "configuration": {
                        "shape": "flat",
                        "text_model": "local-model",
                        "capacity": {"moment_capacity": 1},
                    },
                    "thesis": old_thesis,
                    "selection": {"keep": []},
                    "thesis_calls": [{"prompt": "Read the frozen moment wall."}],
                    "selection_calls": [
                        {
                            "prompt": (
                                "Reading: "
                                + json.dumps(
                                    old_thesis,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                )
                                + "\nChoose moments."
                            )
                        }
                    ],
                    "lifecycle_requirements": [],
                },
            }
        )
    )
    answers = iter(
        (
            json.dumps(
                {
                    "schema_version": "description-memory-thesis-v2",
                    "thesis": "Two moments form one small thread.",
                    "sustained_threads": [
                        {
                            "summary": "The thread continues.",
                            "evidence_moment_ids": ["M001", "M002"],
                        }
                    ],
                    "turning_points": [],
                    "ordinary_texture": [],
                }
            ),
            json.dumps(
                {
                    "schema_version": "description-moment-selection-v2",
                    "keep": [{"moment_id": "M001", "reason": "It carries the thread."}],
                    "audit_summary": "One moment is enough.",
                    "comparisons": [
                        {
                            "kept_moment_id": "M001",
                            "rejected_moment_id": "M002",
                            "reason": "The first is more visible.",
                        }
                    ],
                    "overall_reason": "The cut is concise.",
                }
            ),
        )
    )

    async def answer(*_args, **_kwargs):
        return next(answers)

    monkeypatch.setattr(replay, "query_llm", answer)
    monkeypatch.setenv("OPENAI_KEY", "test-key")
    args = SimpleNamespace(
        result=result_path,
        out=tmp_path / "replay.json",
        provider="openai",
        model="gpt-5.6-terra",
        timeout_seconds=30,
    )

    result = asyncio.run(replay._replay(args))

    assert result["status"] == "complete"
    assert [row["moment_id"] for row in result["selection"]["keep"]] == ["M001"]
