"""Grouped automation scope has one identity from candidate discovery through launch."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date

import pytest

from immich_memories.api.person_expression import PersonExpression
from immich_memories.automation.candidate_scorer import score_and_rank
from immich_memories.automation.candidates import (
    CandidateCategory,
    MemoryCandidate,
    make_memory_key,
)
from immich_memories.automation.generation_request import GenerationRequest

FIRST = PersonExpression.parse('( "Person A" OR "Person B" ) AND "Person C"')
SECOND = PersonExpression.parse('"Person A" OR ( "Person B" AND "Person C" )')
START, END = date(2024, 1, 1), date(2024, 12, 31)


def candidate(*, expression=None, names=None, key="base-key", extra=None):
    params = dict(extra or {})
    if expression is not None:
        params["person_expression"] = expression
    return MemoryCandidate(
        memory_type="multi_person",
        category=CandidateCategory.MULTI_PERSON,
        date_range_start=START,
        date_range_end=END,
        person_names=[] if names is None else names,
        memory_key=key,
        score=0.5,
        reason="Observed people scope",
        asset_count=20,
        extra_params=params,
    )


def test_candidate_key_is_the_exact_child_completion_key_before_launch():
    row = candidate(expression=FIRST.to_dict(), names=["Person C", "Person B", "Person A"])
    assert row.person_names == list(FIRST.leaf_values)
    assert row.memory_key != "base-key"
    request = GenerationRequest.from_candidate(row, upload=False)
    assert request.memory_key == row.memory_key
    assert request.person_expression == FIRST
    assert f"--memory-key={row.memory_key}" in request.to_argv()
    assert f"--people-expression={FIRST.display_label}" in request.to_argv()


def test_different_groupings_survive_real_prelaunch_dedup_but_exact_duplicates_do_not():
    first = candidate(expression=FIRST.to_dict())
    second = candidate(expression=SECOND.to_dict())
    duplicate = candidate(expression=FIRST.to_dict())
    assert first.person_names == second.person_names
    assert first.memory_key != second.memory_key
    ranked = score_and_rank([first, second, duplicate], set(), END, {})
    assert {row.memory_key for row in ranked} == {first.memory_key, second.memory_key}
    assert len(ranked) == 2
    assert all(
        GenerationRequest.from_candidate(row, False).memory_key == row.memory_key for row in ranked
    )


@pytest.mark.parametrize("typed", [False, True])
def test_normalized_ast_is_independent_of_callers_and_keeps_other_parameters(typed):
    record = FIRST.to_dict()
    row = candidate(expression=FIRST if typed else record, extra={"recency_date": END})
    assert row.extra_params == {"person_expression": FIRST.to_dict(), "recency_date": END}
    record.clear()
    assert GenerationRequest.from_candidate(row, False).person_expression == FIRST


def test_prebound_key_and_serialized_candidate_roundtrip_are_idempotent():
    key = make_memory_key(
        "multi_person", START, END, list(FIRST.leaf_values), person_expression=FIRST
    )
    row = candidate(expression=FIRST.to_dict(), key=key)
    assert row.memory_key == key
    restored = MemoryCandidate(**asdict(row))
    copied = replace(restored)
    assert copied.memory_key == restored.memory_key == row.memory_key
    assert copied.extra_params == row.extra_params
    assert GenerationRequest.from_candidate(copied, False).memory_key == key


@pytest.mark.parametrize(
    "record",
    [{"any": []}, {"all": [{"person": "Person A"}, {}]}, {"person": ""}, "Person A"],
)
def test_invalid_ast_fails_during_candidate_construction(record):
    with pytest.raises(ValueError):
        candidate(expression=record)


def test_disagreeing_flat_names_fail_before_dedup():
    with pytest.raises(ValueError, match="candidate names"):
        candidate(expression=FIRST.to_dict(), names=["Person A", "Person B"])


@pytest.mark.parametrize("extra", [{}, {"person_expression": None}, {"recency_date": END}])
def test_flat_candidate_keys_names_and_extra_parameters_are_unchanged(extra):
    names = ["Person B", "Person A"]
    key = make_memory_key("multi_person", START, END, names)
    row = candidate(names=names, key=key, extra=extra)
    assert row.memory_key == key == "multi_person:2024-01-01:2024-12-31:person a,person b"
    assert row.person_names == names
    assert row.extra_params == extra
    assert GenerationRequest.from_candidate(row, False).memory_key == key
