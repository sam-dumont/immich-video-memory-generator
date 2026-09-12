"""Mechanical pick rules do not need a model when favourites already fill the grant."""

import json

import pytest

from immich_memories.analysis.editorial_story_shortlist import DepictedChoice, pick_story_moments


class PickJudge:
    # WHY: replay controlled text-model choices through the real mechanical pick rules;
    # these unit tests never open a model connection.
    def __init__(self, keep=("M01", "M02")):
        self.keep = keep
        self.calls = []

    def ask(self, stage, _prompt, **_kwargs):
        self.calls.append(stage)
        return json.dumps({"keep": self.keep})


def pick(
    judge,
    *,
    days=1,
    count=2,
    stars=(),
    company=None,
    choice_count=3,
    replacement_allowed=lambda _c: True,
):
    choices = [
        DepictedChoice(f"choice-{i}", "K01", f"2030-05-01T10:0{i}", f"View {i}", f"asset-{i}")
        for i in range(1, choice_count + 1)
    ]
    records = []
    selected = pick_story_moments(
        judge,
        story={"key": "K01", "title": "A visit", "seen": {"days": days}},
        choices=choices,
        count=count,
        starred=lambda choice: choice.key in stars,
        contract="Show the visit",
        record=lambda name, value: records.append((name, value)),
        kind_of=lambda choice: f" | with: {company[choice.key]}" if company else "",
        replacement_allowed=replacement_allowed,
    )
    return [choice.key for choice in selected], records


@pytest.mark.parametrize(
    "days,count,stars,expected",
    [
        (1, 2, ("choice-1", "choice-2"), ["choice-1", "choice-2"]),
        (5, 1, ("choice-2",), ["choice-2"]),
    ],
)
def test_favourites_that_fill_the_grant_skip_both_model_orders(days, count, stars, expected):
    judge = PickJudge(keep=("M03",))
    selected, records = pick(judge, days=days, count=count, stars=stars)
    assert selected == expected
    assert judge.calls == []
    assert records[0][1]["orders"] == []
    assert records[0][1]["chosen"] == expected


def test_multiday_favourites_leave_the_remaining_slot_to_both_model_orders():
    judge = PickJudge(keep=("M03", "M02"))
    selected, _ = pick(judge, days=5, stars=("choice-1", "choice-2"))
    assert selected == ["choice-1", "choice-3"]
    assert len(judge.calls) == 2


def test_second_picture_of_same_company_yields_to_a_new_relation():
    selected, _ = pick(
        PickJudge(),
        company={
            "choice-1": "mother",
            "choice-2": "mother",
            "choice-3": "grandfather",
        },
    )
    assert selected == ["choice-1", "choice-3"]


def test_company_rule_preserves_favourites_even_when_they_show_the_same_relation():
    selected, _ = pick(
        PickJudge(),
        stars=("choice-1", "choice-2"),
        company={
            "choice-1": "mother",
            "choice-2": "mother",
            "choice-3": "grandfather",
        },
    )
    assert selected == ["choice-1", "choice-2"]


@pytest.mark.parametrize("familiar_bundle", ["parent, child", "child, parent"])
def test_familiar_combination_is_skipped_for_a_relation_absent_from_the_selected_set(
    familiar_bundle,
):
    judge = PickJudge(keep=("M01", "M02", "M03"))
    selected, records = pick(
        judge,
        count=3,
        choice_count=5,
        company={
            "choice-1": "parent",
            "choice-2": "child",
            "choice-3": "parent",
            "choice-4": familiar_bundle,
            "choice-5": "grandparent",
        },
    )

    assert selected == ["choice-1", "choice-2", "choice-5"]
    assert records[0][1]["company_replacements"] == [
        {
            "removed": "choice-3",
            "added": "choice-5",
            "new_relations": ["grandparent"],
        }
    ]
    assert records[0][1]["count"] == 3
    assert judge.calls == ["story-pick-K01-source", "story-pick-K01-reversed"]


@pytest.mark.parametrize("repeated_relations", ["child", "child, parent"])
def test_subset_or_reordered_duplicate_yields_without_losing_existing_relations(repeated_relations):
    judge = PickJudge()
    selected, records = pick(
        judge,
        company={
            "choice-1": "parent, child",
            "choice-2": repeated_relations,
            "choice-3": "grandparent",
        },
    )

    assert selected == ["choice-1", "choice-3"]
    assert records[0][1]["company_replacements"] == [
        {
            "removed": "choice-2",
            "added": "choice-3",
            "new_relations": ["grandparent"],
        }
    ]
    assert len(judge.calls) == 2


def test_new_relation_does_not_displace_a_picture_with_unique_existing_coverage():
    judge = PickJudge()
    selected, records = pick(
        judge,
        company={
            "choice-1": "parent, child",
            "choice-2": "parent, sibling",
            "choice-3": "grandparent",
        },
    )

    assert selected == ["choice-1", "choice-2"]
    assert records[0][1]["company_replacements"] == []
    assert len(judge.calls) == 2


def test_company_replacement_preserves_starred_duplicate_after_both_model_orders():
    judge = PickJudge(keep=("M01", "M02", "M03"))
    selected, records = pick(
        judge,
        days=3,
        count=3,
        choice_count=4,
        stars=("choice-2",),
        company={
            "choice-1": "parent",
            "choice-2": "parent",
            "choice-3": "child",
            "choice-4": "grandparent",
        },
    )

    assert selected == ["choice-2", "choice-3", "choice-4"]
    assert records[0][1]["company_replacements"] == [
        {
            "removed": "choice-1",
            "added": "choice-4",
            "new_relations": ["grandparent"],
        }
    ]
    assert len(judge.calls) == 2


def test_later_nonstarred_repeats_yield_first_without_expanding_the_pick():
    judge = PickJudge(keep=("M01", "M02", "M03"))
    selected, records = pick(
        judge,
        count=3,
        choice_count=5,
        company={
            "choice-1": "parent",
            "choice-2": "parent",
            "choice-3": "parent",
            "choice-4": "grandparent",
            "choice-5": "sibling",
        },
    )

    assert selected == ["choice-1", "choice-4", "choice-5"]
    assert records[0][1]["company_replacements"] == [
        {"removed": "choice-3", "added": "choice-4", "new_relations": ["grandparent"]},
        {"removed": "choice-2", "added": "choice-5", "new_relations": ["sibling"]},
    ]
    assert judge.calls == ["story-pick-K01-source", "story-pick-K01-reversed"]


def test_held_company_improvement_keeps_original_choices_and_is_checked_only_once():
    checked = []

    def reject(choice):
        checked.append(choice.key)
        return False

    judge = PickJudge()
    selected, records = pick(
        judge,
        company={
            "choice-1": "parent",
            "choice-2": "parent",
            "choice-3": "grandparent",
        },
        replacement_allowed=reject,
    )

    assert selected == ["choice-1", "choice-2"]
    assert checked == ["choice-3"]
    assert records[0][1]["company_replacements"] == []
    assert records[0][1]["company_rejected"] == ["choice-3"]
    assert len(judge.calls) == 2


def test_held_company_improvement_tries_next_available_novel_moment():
    checked = []

    def admit(choice):
        checked.append(choice.key)
        return choice.key == "choice-5"

    selected, records = pick(
        PickJudge(),
        choice_count=5,
        company={
            "choice-1": "parent",
            "choice-2": "parent",
            "choice-3": "parent",
            "choice-4": "grandparent",
            "choice-5": "sibling",
        },
        replacement_allowed=admit,
    )

    assert selected == ["choice-1", "choice-5"]
    assert checked == ["choice-4", "choice-5"]
    assert records[0][1]["company_rejected"] == ["choice-4"]
    assert records[0][1]["company_replacements"] == [
        {
            "removed": "choice-2",
            "added": "choice-5",
            "new_relations": ["sibling"],
        }
    ]
