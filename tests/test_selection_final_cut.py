"""The final asset cut can reallocate moment slots without escaping its wall."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import probe_description_moment_cut as prototype
import probe_smart_edit_matrix as matrix
from probe_selection_final_cut import (
    FINAL_ASSET_CUT_SCHEMA,
    FineCutCandidate,
    final_asset_cut_prompt,
    read_final_asset_cut,
)

START = datetime(2025, 1, 1, tzinfo=UTC)


def _candidate(index: int, moment: str) -> FineCutCandidate:
    return FineCutCandidate(
        alias=f"A{index:03d}",
        asset_id=f"private-{index}",
        moment_id=moment,
        taken_at=START + timedelta(hours=index),
        media_kind="photo",
        favourite=index == 1,
        description=f"Visible scene {index}",
    )


def _answer(*keep: str, rejected: str = "A003") -> str:
    return json.dumps(
        {
            "schema_version": FINAL_ASSET_CUT_SCHEMA,
            "keep": [
                {"asset_id": alias, "reason": "Adds a distinct visible beat."} for alias in keep
            ],
            "comparisons": [
                {
                    "kept_asset_id": keep[0],
                    "rejected_asset_id": rejected,
                    "reason": "The kept scene carries more lived action.",
                }
            ],
            "overall_reason": "The sequence carries the thesis without repetition.",
        }
    )


def test_prompt_says_moment_slots_can_be_reallocated() -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M001"), _candidate(3, "M002"))

    prompt = final_asset_cut_prompt(
        candidates,
        memory_label="A month",
        memory_type="monthly_highlights",
        editorial_brief="Make a truthful memory.",
        thesis={"thesis": "A grounded month"},
        capacity=2,
        required_aliases=("A001",),
    )

    assert "several genuinely different" in prompt
    assert "assets from one rich moment" in prompt
    assert "retain none from a weaker shortlisted moment" in prompt
    assert "return at most 1 additional assets" in prompt


def test_hierarchical_capacity_scales_the_prior_editorial_allocation() -> None:
    candidates = tuple(_candidate(index, "M001" if index <= 3 else "M002") for index in range(1, 9))
    text_result = {
        "chapter_readings": [
            {"chapter_id": "C001", "label": "Earlier", "moment_ids": ["M001"]},
            {"chapter_id": "C002", "label": "Later", "moment_ids": ["M002"]},
        ],
        "allocation": {
            "allocations": [
                {"chapter_id": "C001", "slots": 1},
                {"chapter_id": "C002", "slots": 3},
            ]
        },
    }

    plans = matrix._hierarchical_final_cut_plan(
        text_result,
        candidates,
        required_aliases=("A001",),
        capacity=6,
    )

    assert [plan["capacity"] for plan in plans] == [2, 4]
    assert sum(plan["capacity"] for plan in plans) == 6
    assert plans[0]["required_aliases"] == ("A001",)


def test_hierarchical_final_cut_repairs_an_ungrounded_keep(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    candidates = (
        replace(_candidate(1, "M001"), alias="A420"),
        replace(_candidate(2, "M001"), alias="A431"),
        replace(_candidate(3, "M002"), alias="A503"),
    )
    answers = iter((_answer("A999", rejected="A002"), _answer("A003", rejected="A002")))

    async def ask(prompt, *_args, **_kwargs):
        return matrix.TextCall(prompt, next(answers), 0.1, False, False)

    monkeypatch.setattr(matrix, "_ask_text", ask)
    cut, calls = asyncio.run(
        matrix._hierarchical_final_asset_cut(
            (
                {
                    "chapter_id": "C001",
                    "label": "One chapter",
                    "source_slots": 1,
                    "candidates": candidates,
                    "required_aliases": (),
                    "capacity": 1,
                },
            ),
            case=matrix.Case(
                key="case",
                label="A memory",
                product="year_in_review",
                ranges=(),
                target_seconds=600.0,
                brief="Make a truthful memory.",
            ),
            thesis={"thesis": "A grounded year."},
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            concurrency=2,
            timeout_seconds=30,
        )
    )

    assert len(calls) == 2
    assert [row["asset_id"] for row in cut["keep"]] == ["A503"]
    assert cut["chapters"][0]["calls"] == 2
    progress = capsys.readouterr().out
    assert "final-asset-cut: 1/1 chapters complete" in progress
    assert "0 cache hits, 2 actual calls" in progress
    assert "ETA 0s" in progress


def test_hierarchical_final_cut_repairs_missing_favourite_representation(
    monkeypatch, tmp_path: Path
) -> None:
    candidates = (
        replace(_candidate(1, "M001"), alias="A420"),
        replace(_candidate(2, "M001"), alias="A431"),
        replace(_candidate(3, "M002"), alias="A503"),
    )
    answers = iter((_answer("A002"), _answer("A001")))
    prompts = []

    async def ask(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        return matrix.TextCall(prompt, next(answers), 0.1, False, False)

    monkeypatch.setattr(matrix, "_ask_text", ask)
    cut, calls = asyncio.run(
        matrix._hierarchical_final_asset_cut(
            (
                {
                    "chapter_id": "C001",
                    "label": "One chapter",
                    "source_slots": 1,
                    "candidates": candidates,
                    "required_aliases": (),
                    "capacity": 1,
                },
            ),
            case=matrix.Case(
                key="case",
                label="A memory",
                product="year_in_review",
                ranges=(),
                target_seconds=600.0,
                brief="Make a truthful memory.",
            ),
            thesis={"thesis": "A grounded year."},
            llm_config=SimpleNamespace(),
            cache_path=tmp_path / "cache.db",
            concurrency=2,
            timeout_seconds=30,
        )
    )

    assert len(calls) == 2
    assert [row["asset_id"] for row in cut["keep"]] == ["A420"]
    assert "keep at least one\nFAVOURITE asset from that moment" in prompts[1]


def test_required_and_optional_assets_merge_in_chronological_wall_order() -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M001"), _candidate(3, "M002"))

    cut = read_final_asset_cut(
        _answer("A002"),
        candidates,
        capacity=2,
        required_aliases=("A001",),
    )

    assert [row["asset_id"] for row in cut["keep"]] == ["A001", "A002"]


def test_optional_assets_cannot_overfill_the_remaining_capacity() -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M001"), _candidate(3, "M002"))

    with pytest.raises(ValueError, match="exceeds remaining"):
        read_final_asset_cut(
            _answer("A002", "A003"),
            candidates,
            capacity=2,
            required_aliases=("A001",),
        )


def test_required_asset_echo_is_a_recorded_noop() -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M001"), _candidate(3, "M002"))

    cut = read_final_asset_cut(
        _answer("A001", rejected="A002"),
        candidates,
        capacity=2,
        required_aliases=("A001",),
    )

    assert [row["asset_id"] for row in cut["keep"]] == ["A001"]
    assert cut["discarded_required_echoes"] == 1


def test_bad_debug_comparison_does_not_discard_a_valid_cut() -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M001"), _candidate(3, "M002"))

    cut = read_final_asset_cut(
        _answer("A002", rejected="A001"),
        candidates,
        capacity=2,
        required_aliases=("A001",),
    )

    assert [row["asset_id"] for row in cut["keep"]] == ["A001", "A002"]
    assert cut["comparisons"] == []
    assert cut["discarded_comparisons"] == 1


def test_a_shortlisted_favourite_moment_can_be_dropped_but_not_misrepresented() -> None:
    candidates = (_candidate(1, "M001"), _candidate(2, "M001"), _candidate(3, "M002"))

    dropped = read_final_asset_cut(
        _answer("A003", rejected="A002"),
        candidates,
        capacity=1,
    )
    assert [row["asset_id"] for row in dropped["keep"]] == ["A003"]

    with pytest.raises(ValueError, match="no favourite"):
        read_final_asset_cut(
            _answer("A002", rejected="A003"),
            candidates,
            capacity=1,
        )


def test_moment_audit_allows_no_comparison_only_when_nothing_is_rejected() -> None:
    all_kept = json.dumps(
        {
            "schema_version": prototype.SELECTION_SCHEMA,
            "keep": [
                {"moment_id": "M001", "reason": "Distinct lived scene."},
                {"moment_id": "M002", "reason": "Different lived scene."},
            ],
            "audit_summary": "Both moments earn runtime.",
            "comparisons": [],
            "overall_reason": "The small complete wall is coherent.",
        }
    )

    assert len(prototype._read_selection(all_kept, frozenset({"M001", "M002"}), 13)["keep"]) == 2

    rejected_without_comparison = json.loads(all_kept)
    rejected_without_comparison["keep"].pop()
    with pytest.raises(ValueError, match="audit"):
        prototype._read_selection(
            json.dumps(rejected_without_comparison),
            frozenset({"M001", "M002"}),
            13,
        )
