"""Strict independent parsing for fused record-shot and Cull decisions."""

from __future__ import annotations

import json

import pytest

from immich_memories.analysis.editorial_contracts import RecordShotMark


@pytest.mark.parametrize(
    ("function", "reason"),
    (
        ("", "Visible evidence."),
        ("proof", " "),
        ("f" * 49, "Visible evidence."),
        ("proof", "r" * 97),
    ),
)
def test_record_mark_constructor_rejects_unusable_function_or_reason(
    function: str, reason: str
) -> None:
    """Direct callers cannot bypass the bounded visible-function contract."""
    with pytest.raises(ValueError, match="record-shot"):
        RecordShotMark("asset", function, reason)


@pytest.mark.parametrize(
    ("defect", "reason"),
    (
        ("subject", "It is a selfie."),
        ("repetition", "Another frame is stronger."),
        ("relative_weakness", "Another frame is stronger."),
        ("thesis_relevance", "It does not fit the story."),
        ("duration", "The clip is short."),
        ("resolution", "The source is small."),
        ("similarity", "It resembles another frame."),
        ("unusable_exposure", " "),
        ("unusable_exposure", "r" * 97),
    ),
)
def test_cull_constructor_rejects_non_defects_and_unusable_reasons(
    defect: str, reason: str
) -> None:
    """No caller can actuate topic policy by constructing a Cull decision directly."""
    from immich_memories.analysis.cull_answer import CullDecision

    with pytest.raises(ValueError, match="Cull"):
        CullDecision("asset", defect, reason)


def test_record_mark_wins_a_namespace_collision_without_discarding_siblings() -> None:
    """A protected record invalidates only its own Cull rejection."""
    from immich_memories.analysis.cull_answer import (
        CullDecision,
        RecordShotMark,
        read_cull_namespaces,
    )

    raw = json.dumps(
        {
            "schema_version": "episode-scan-v3",
            "pack": 1,
            "episode_readings": [],
            "record_shots": [
                {
                    "tile": 1,
                    "function": "result proof",
                    "reason": "Records the result.",
                }
            ],
            "cull_rejects": [
                {
                    "tile": 1,
                    "defect": "unusable_exposure",
                    "reason": "The pixels are completely blown out.",
                },
                {
                    "tile": 2,
                    "defect": "unusable_motion_blur",
                    "reason": "The frame is unreadable through motion blur.",
                },
            ],
        }
    )

    answer = read_cull_namespaces(
        raw,
        pack_alias=1,
        tile_map={1: "test", 2: "blur"},
    )

    assert answer is not None
    assert answer.record_shots == (RecordShotMark("test", "result proof", "Records the result."),)
    assert answer.cull_rejects == (
        CullDecision(
            "blur",
            "unusable_motion_blur",
            "The frame is unreadable through motion blur.",
        ),
    )
    assert answer.warnings == ("!! cull reject conflicted with record-shot mark: test",)


@pytest.mark.parametrize(
    ("record_shots", "cull_rejects", "record_valid", "cull_valid", "record_ids", "cull_ids"),
    (
        (
            [{"tile": 1, "function": "admission proof", "reason": "Records a ticket."}],
            [{"tile": True, "defect": "unusable_exposure", "reason": "Unreadable."}],
            True,
            False,
            ("ticket",),
            (),
        ),
        (
            [{"page": 2, "tile": 1, "function": "proof", "reason": "Wrong page."}],
            [{"tile": 2, "defect": "unusable_exposure", "reason": "Unreadable."}],
            False,
            True,
            (),
            ("bad",),
        ),
    ),
)
def test_malformed_namespace_does_not_erase_its_valid_sibling(
    record_shots,
    cull_rejects,
    record_valid: bool,
    cull_valid: bool,
    record_ids: tuple[str, ...],
    cull_ids: tuple[str, ...],
) -> None:
    """Record and Cull validity are independent inside one complete outer object."""
    from immich_memories.analysis.cull_answer import read_cull_namespaces

    answer = read_cull_namespaces(
        json.dumps(
            {
                "schema_version": "episode-scan-v3",
                "pack": 1,
                "episode_readings": "irrelevant to Pass 1",
                "record_shots": record_shots,
                "cull_rejects": cull_rejects,
            }
        ),
        pack_alias=1,
        tile_map={1: "ticket", 2: "bad"},
    )

    assert answer is not None
    assert answer.record_valid is record_valid
    assert answer.cull_valid is cull_valid
    assert tuple(mark.asset_id for mark in answer.record_shots) == record_ids
    assert tuple(decision.asset_id for decision in answer.cull_rejects) == cull_ids


@pytest.mark.parametrize(
    "raw",
    (
        '{"schema_version":"episode-scan-v3","pack":1',
        '{"schema_version":"episode-scan-v2","pack":1,"record_shots":[],"cull_rejects":[]}',
        '{"schema_version":"episode-scan-v3","pack":true,"record_shots":[],"cull_rejects":[]}',
    ),
)
def test_malformed_outer_envelope_has_no_pass_one_decisions(raw: str) -> None:
    """Fragments, stale schemas, and boolean aliases cannot actuate rejection."""
    from immich_memories.analysis.cull_answer import read_cull_namespaces

    assert read_cull_namespaces(raw, pack_alias=1, tile_map={}) is None


@pytest.mark.parametrize(
    "member",
    (
        {"tile": 1, "function": "proof", "reason": "A."},
        {"tile": 99, "function": "proof", "reason": "Unknown."},
        {"tile": 2, "function": "f" * 49, "reason": "Visible."},
        {"tile": 2, "function": "proof", "reason": "r" * 97},
    ),
)
def test_duplicate_unknown_or_overlong_record_member_invalidates_record_namespace(
    member: dict[str, object],
) -> None:
    """One bad record member cannot leave a plausible-looking partial record list."""
    from immich_memories.analysis.cull_answer import read_cull_namespaces

    answer = read_cull_namespaces(
        json.dumps(
            {
                "schema_version": "episode-scan-v3",
                "pack": 1,
                "record_shots": [
                    {"tile": 1, "function": "proof", "reason": "A."},
                    member,
                ],
                "cull_rejects": [],
            }
        ),
        pack_alias=1,
        tile_map={1: "a", 2: "b"},
    )

    assert answer is not None
    assert answer.record_valid is False
    assert answer.record_shots == ()
    assert answer.cull_valid is True


@pytest.mark.parametrize(
    "member",
    (
        {"tile": 1, "defect": "unusable_exposure", "reason": "Duplicate."},
        {"tile": True, "defect": "unusable_exposure", "reason": "Unreadable."},
        {"tile": 99, "defect": "unusable_exposure", "reason": "Unknown."},
        {
            "page": 2,
            "tile": 2,
            "defect": "unusable_exposure",
            "reason": "Wrong page.",
        },
        {"tile": 2, "defect": "unusable_exposure", "reason": "r" * 97},
        {"tile": 2, "defect": "repetition", "reason": "A sibling is stronger."},
    ),
)
def test_boolean_unknown_cross_page_overlong_or_policy_cull_invalidates_namespace(
    member: dict[str, object],
) -> None:
    """Malformed and policy-shaped rejects fail the whole Cull namespace open."""
    from immich_memories.analysis.cull_answer import read_cull_namespaces

    answer = read_cull_namespaces(
        json.dumps(
            {
                "schema_version": "episode-scan-v3",
                "pack": 1,
                "record_shots": [],
                "cull_rejects": [
                    {"tile": 1, "defect": "unusable_exposure", "reason": "Unreadable."},
                    member,
                ],
            }
        ),
        pack_alias=1,
        tile_map={1: "a", 2: "b"},
    )

    assert answer is not None
    assert answer.record_valid is True
    assert answer.cull_valid is False
    assert answer.cull_rejects == ()
