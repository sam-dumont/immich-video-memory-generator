"""Representation repairs preserve the inventory's complete-source requirement."""

import json
import re

import pytest

from immich_memories.analysis.editorial_moment_inventory import (
    DepictedMoment,
    inventory_event,
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


class ScriptedJudge:
    """Records every request and answers one group holding the page's sources.

    # WHY: the judge is the model boundary; the prompt bytes it receives are the
    # banked judgment key this test is about.
    """

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def ask(self, _stage, prompt, max_tokens=260, **_options):
        self.prompts.append(prompt)
        sources = re.findall(r'"source": "(U\d+)"', prompt)
        return json.dumps(
            {
                "moments": [
                    {
                        "same_as": None,
                        "sources": sources,
                        "primary": sources[0],
                        "content": "A clothed person walks by the water",
                    }
                ]
            }
        )


def day_units(moments):
    return [
        {
            "asset_id": f"picture-{index:02d}",
            "moment": moment,
            "taken": f"2030-02-05T10:0{index}:00",
            "facts": f"A clothed person walks by the water, view {index}.",
        }
        for index, moment in enumerate(moments)
    ]


def read_day(judge, *, event, moments, context):
    return inventory_event(
        judge,
        event=event,
        units=day_units(moments),
        context=context,
        line=lambda unit: unit["facts"],
    )


def test_the_same_day_is_one_request_whatever_the_film_calls_its_moments():
    month, year = ScriptedJudge(), ScriptedJudge()

    read_day(month, event="S0001", moments=["M001", "M001", "M002"], context="A day by the canal")
    read_day(year, event="S0147", moments=["M241", "M241", "M242"], context="A year of walks")

    assert month.prompts == year.prompts
    assert "G0001" in month.prompts[0] and "M001" not in month.prompts[0]


def test_the_moments_read_over_aliases_come_back_on_the_callers_own_assets():
    moments, _audit = read_day(
        ScriptedJudge(), event="S0001", moments=["M001", "M001", "M002"], context="A day"
    )

    assert [m.key for m in moments] == ["S0001:K0001"]
    assert moments[0].primary == "picture-00"
    assert moments[0].alternatives == ["picture-01", "picture-02"]


def test_the_story_context_is_recorded_even_though_the_request_does_not_carry_it():
    _moments, audit = read_day(
        ScriptedJudge(), event="S0001", moments=["M001"], context="A day by the canal"
    )

    assert audit["inferred_context"] == "A day by the canal"
    assert audit["version"] == "depicted-moments-v2"
