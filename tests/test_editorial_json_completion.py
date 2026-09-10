"""JSON completion and transport completion have distinct replay contracts.

The gateway-level cases arrive with the slice that ports
`editorial_moment_editor`, `editorial_text_gateway` and
`editorial_structure_claims`.
"""

import json

import pytest

from immich_memories.analysis.editorial_json_completion import (
    complete_final_json,
    json_format_repair_prompt,
    validate_empty_array_pairs,
)


def test_complete_revision_survives_unfinished_trailing_explanation():
    raw = '{"threads":[{"claim":"draft"}]}\nRevised:\n{"threads":[{"claim":"final"}]}\nThis meets the rules because'
    assert complete_final_json(raw) == '{"threads":[{"claim":"final"}]}'


@pytest.mark.parametrize(
    "raw",
    [
        '{"threads":[{"claim":"finished inner object"}]',
        '{"threads":[]}\nRevision: {"threads":[',
        '{"threads":[]} }',
        "no decision",
    ],
)
def test_incomplete_parent_or_revision_never_reuses_a_complete_fragment(raw):
    with pytest.raises(ValueError):
        complete_final_json(raw)


def test_braces_in_strings_do_not_split_objects():
    raw = '{"text":"literal { and } inside a string"}\nExplanation ends here'
    assert complete_final_json(raw) == '{"text":"literal { and } inside a string"}'


def test_field_incomplete_revision_never_falls_back_to_complete_draft():
    with pytest.raises(ValueError, match="exactly the requested fields"):
        complete_final_json('{"a":1,"b":2}\nRevision: {"b":3}', fields=("a", "b"))


@pytest.mark.parametrize(
    "raw",
    [
        '{"a":1},{"a":2,"b":3}',
        '{"a":1,"a":2,"b":3}',
        '{"a":{"x":1,"x":2},"b":3}',
        '{"a":1},{"b":',
        '{"a":1},{"b":2},',
        '{"a":1},{"b":2} explanation',
        '[{"a":1},{"b":2}]',
        '[{"a":1},{"b":2}',
        '{"a":1},{"b":2},{"c":3}',
        '{"a":1}\nRevision: {"b":2}',
    ],
)
def test_field_sequences_reject_overwrite_incomplete_or_ambiguous_content(raw):
    with pytest.raises(ValueError):
        complete_final_json(raw, fields=("a", "b"))


def test_a_complete_disjoint_sequence_is_merged_without_a_second_ask():
    merged = complete_final_json(
        '  {"claim":"a ride","anchors":["F02","F01"]},\n{"extra":{"literal":"é"}}  ',
        fields=("claim", "anchors", "extra"),
    )

    assert json.loads(merged) == {
        "claim": "a ride",
        "anchors": ["F02", "F01"],
        "extra": {"literal": "é"},
    }


def test_an_opted_in_pair_supplies_the_array_the_model_omitted_beside_an_empty_claim():
    """An empty claim carries no anchors; the model routinely leaves the array out."""
    filled = complete_final_json(
        '{"claim":""}',
        fields=("claim", "anchors"),
        empty_array_pairs=(("claim", "anchors"),),
    )

    assert json.loads(filled) == {"claim": "", "anchors": []}


def test_a_stated_claim_never_has_its_missing_anchors_invented():
    with pytest.raises(ValueError, match="exactly the requested fields"):
        complete_final_json(
            '{"claim":"a stated claim"}',
            fields=("claim", "anchors"),
            empty_array_pairs=(("claim", "anchors"),),
        )


def test_a_field_outside_the_requested_schema_fails_the_pair_contract():
    with pytest.raises(ValueError, match="exactly the requested fields"):
        complete_final_json(
            '{"claim":"","anchors":[],"invented":true}',
            fields=("claim", "anchors"),
            empty_array_pairs=(("claim", "anchors"),),
        )


@pytest.mark.parametrize(
    "pairs",
    [
        [("claim", "anchors")],
        (("claim",),),
        (("claim", "claim"),),
        (("claim", "unrequested"),),
        (("claim", ""),),
        (("claim", "anchors"), ("anchors", "second_claim")),
    ],
)
def test_empty_array_pairs_must_be_disjoint_declared_and_immutable(pairs):
    with pytest.raises(ValueError):
        validate_empty_array_pairs(("claim", "anchors", "second_claim"), pairs)


def test_the_repair_ask_keeps_the_original_evidence_and_names_the_one_object():
    """Re-asking from scratch loses the evidence that produced the near-miss."""
    repaired = json_format_repair_prompt("Contract: a race. Evidence: F01 start.")

    assert repaired.startswith("Contract: a race. Evidence: F01 start.")
    assert "ONE outer pair of braces" in repaired
