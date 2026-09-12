"""Representation repairs preserve the inventory's complete-source requirement."""

import json

import pytest

from immich_memories.analysis.editorial_moment_inventory import (
    DepictedMoment,
    read_inventory_page,
)


def page(raw, *, new_ids=("U0002", "U0003")):
    return read_inventory_page(
        raw,
        new_ids=set(new_ids),
        existing={"K0001": DepictedMoment("K0001", "S0001", "A visit", "U0001")},
        units={"U0001": {}, "U0002": {}, "U0003": {"favourite": True}},
    )


@pytest.mark.parametrize("fenced", [False, True])
def test_complete_outer_array_preserves_groups_alternatives_and_favourite(fenced):
    rows = [
        {
            "same_as": "K0001",
            "sources": ["U0002", "U0003"],
            "primary": "U0002",
            "content": "Family at a visit",
        }
    ]
    raw = json.dumps(rows)
    if fenced:
        raw = f"```json\n{raw}\n```"
    assert page(raw) == page(json.dumps({"moments": rows}))
    assert page(raw) == [("K0001", "U0003", ["U0001", "U0002"], "Family at a visit")]


@pytest.mark.parametrize("fenced", [False, True])
def test_outer_array_with_missing_sources_still_fails_coverage(fenced):
    raw = json.dumps(
        [{"same_as": None, "sources": ["U0002"], "primary": "U0002", "content": "A visit"}]
    )
    if fenced:
        raw = f"```json\n{raw}\n```"
    with pytest.raises(ValueError, match="coverage is incomplete"):
        page(raw)


def test_incomplete_outer_array_is_not_completed_from_its_first_object():
    raw = '[{"same_as":null,"sources":["U0002","U0003"],"primary":"U0002","content":"A visit"}'
    with pytest.raises(ValueError):
        page(raw)


def test_outer_array_retains_every_separate_moment():
    rows = [
        {"same_as": "K0001", "sources": ["U0002"], "primary": "U0001", "content": "A visit"},
        {"same_as": None, "sources": ["U0003"], "primary": "U0003", "content": "A later walk"},
    ]
    assert page(json.dumps(rows)) == [
        ("K0001", "U0001", ["U0002"], "A visit"),
        (None, "U0003", [], "A later walk"),
    ]


def test_outer_array_with_unknown_sources_cannot_satisfy_coverage():
    rows = [{"sources": ["U0002", "U9999"], "primary": "U0002", "content": "A visit"}]
    with pytest.raises(ValueError, match="coverage is incomplete"):
        page(json.dumps(rows))
