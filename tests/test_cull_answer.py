"""Strict independent parsing for fused record-shot and Cull decisions."""

from __future__ import annotations

import json

import pytest

from immich_memories.analysis.editorial_contracts import RecordShotMark


def test_structured_cull_evidence_derives_the_only_durable_reason() -> None:
    """A rejection's human explanation is local data, not actuating model prose."""
    from immich_memories.analysis.cull_answer import CullDecision

    decision = CullDecision(
        "asset",
        "unusable_exposure",
        "detail_lost_to_highlights",
    )

    assert decision.evidence == "detail_lost_to_highlights"
    assert decision.reason == "highlight clipping erased the visible detail"


@pytest.mark.parametrize(
    "policy_prose",
    (
        "A relative alternative is stronger.",
        "This is an uninteresting selfie.",
        "This repeats an earlier shot.",
        "This does not support the thesis.",
    ),
)
def test_allowed_defect_cannot_actuate_from_free_policy_prose(policy_prose: str) -> None:
    """An allowed defect label cannot smuggle an editorial cut through free prose."""
    from immich_memories.analysis.cull_answer import read_cull_namespaces

    answer = read_cull_namespaces(
        json.dumps(
            {
                "schema_version": "episode-scan-v3",
                "pack": 1,
                "record_shots": [],
                "cull_rejects": [
                    {
                        "tile": 1,
                        "defect": "unusable_exposure",
                        "reason": policy_prose,
                    }
                ],
            }
        ),
        pack_alias=1,
        tile_map={1: "asset"},
    )

    assert answer is not None
    assert answer.cull_valid is False
    assert answer.cull_rejects == ()


@pytest.mark.parametrize(
    "unsafe", ('quote"mark', "back\\slash", "line\nbreak", "caf\N{LATIN SMALL LETTER E WITH ACUTE}")
)
@pytest.mark.parametrize("field", ("function", "reason"))
def test_record_mark_constructor_rejects_text_outside_the_safe_wire_alphabet(
    field: str,
    unsafe: str,
) -> None:
    """Direct record marks enforce the same bounded alphabet as the wire parser."""
    values = {"function": "result proof", "reason": "Records the visible result."}
    values[field] = unsafe

    with pytest.raises(ValueError, match="record-shot"):
        RecordShotMark("asset", values["function"], values["reason"])


def test_invalid_record_text_fails_open_without_erasing_structured_cull() -> None:
    """One unsafe record namespace cannot erase its valid structured sibling."""
    from immich_memories.analysis.cull_answer import read_cull_namespaces

    answer = read_cull_namespaces(
        json.dumps(
            {
                "schema_version": "episode-scan-v3",
                "pack": 1,
                "record_shots": [
                    {
                        "tile": 1,
                        "function": "result proof",
                        "reason": 'The model called it "important".',
                    }
                ],
                "cull_rejects": [
                    {
                        "tile": 2,
                        "defect": "unusable_motion_blur",
                        "evidence": "frame_smeared_beyond_use",
                    }
                ],
            }
        ),
        pack_alias=1,
        tile_map={1: "record", 2: "blur"},
    )

    assert answer is not None
    assert answer.record_valid is False
    assert answer.record_shots == ()
    assert answer.cull_valid is True
    assert tuple(decision.asset_id for decision in answer.cull_rejects) == ("blur",)


def test_safe_model_text_keeps_apostrophes_and_basic_punctuation() -> None:
    """The canonical alphabet stays useful for terse visual descriptions."""
    mark = RecordShotMark("asset", "ticket's proof", "Visible: gate #4 - admitted!")

    assert mark.function == "ticket's proof"
    assert mark.reason == "Visible: gate #4 - admitted!"


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
    ("defect", "evidence"),
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
def test_cull_constructor_rejects_non_defects_and_mismatched_evidence(
    defect: str, evidence: str
) -> None:
    """No caller can actuate topic policy by constructing a Cull decision directly."""
    from immich_memories.analysis.cull_answer import CullDecision

    with pytest.raises(ValueError, match="Cull"):
        CullDecision("asset", defect, evidence)


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
                    "evidence": "detail_lost_to_highlights",
                },
                {
                    "tile": 2,
                    "defect": "unusable_motion_blur",
                    "evidence": "frame_smeared_beyond_use",
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
            "frame_smeared_beyond_use",
        ),
    )
    assert answer.warnings == ("!! cull reject conflicted with record-shot mark: test",)


@pytest.mark.parametrize(
    ("record_shots", "cull_rejects", "record_valid", "cull_valid", "record_ids", "cull_ids"),
    (
        (
            [{"tile": 1, "function": "admission proof", "reason": "Records a ticket."}],
            [
                {
                    "tile": True,
                    "defect": "unusable_exposure",
                    "evidence": "detail_lost_to_highlights",
                }
            ],
            True,
            False,
            ("ticket",),
            (),
        ),
        (
            [{"page": 2, "tile": 1, "function": "proof", "reason": "Wrong page."}],
            [
                {
                    "tile": 2,
                    "defect": "unusable_exposure",
                    "evidence": "detail_lost_to_highlights",
                }
            ],
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
        {
            "tile": 1,
            "defect": "unusable_exposure",
            "evidence": "detail_lost_to_highlights",
        },
        {
            "tile": True,
            "defect": "unusable_exposure",
            "evidence": "detail_lost_to_highlights",
        },
        {
            "tile": 99,
            "defect": "unusable_exposure",
            "evidence": "detail_lost_to_highlights",
        },
        {
            "page": 2,
            "tile": 2,
            "defect": "unusable_exposure",
            "evidence": "detail_lost_to_highlights",
        },
        {"tile": 2, "defect": "unusable_exposure", "evidence": "relative_weakness"},
        {"tile": 2, "defect": "repetition", "evidence": "frame_smeared_beyond_use"},
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
                    {
                        "tile": 1,
                        "defect": "unusable_exposure",
                        "evidence": "detail_lost_to_highlights",
                    },
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
