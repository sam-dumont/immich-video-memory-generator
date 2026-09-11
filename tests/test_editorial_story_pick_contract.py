"""The picker must see original duration and return a complete valid choice."""

import json

import pytest

from immich_memories.analysis.editorial_story_pick_contract import (
    ask_moment_pick,
    source_kind_marker,
)
from immich_memories.analysis.editorial_story_shortlist import DepictedChoice, pick_story_moments


class Answers:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.calls = []

    def ask(self, stage, prompt, **_kwargs):
        self.calls.append((stage, prompt))
        return next(self.answers)


@pytest.mark.parametrize("field", ["raw_seconds", "source_seconds", "duration"])
def test_source_length_is_not_replaced_with_six_second_carrier(field):
    marker = source_kind_marker({"kind": "video", field: 3600, "seconds": 6})
    assert marker == " | video 3600 s source"


def test_raw_length_precedes_other_duration_fields():
    assert (
        source_kind_marker({"kind": "video", "raw_seconds": 400, "duration": 6})
        == " | video 400 s source"
    )


@pytest.mark.parametrize("value", [None, 0, -1, float("nan"), float("inf"), True, "long"])
def test_absent_source_length_is_unknown_not_the_planned_hold(value):
    assert (
        source_kind_marker({"kind": "video", "raw_seconds": value, "seconds": 6})
        == " | video (source duration unknown)"
    )


@pytest.mark.parametrize(
    "answer",
    [
        "M01",
        '{"keep":["M01","M02"]}',
        '{"keep":["M09"]}',
        '{"keep":[]}',
        '{"keep":[1]}',
        '{"keep":"M01"}',
    ],
)
def test_invalid_response_is_repaired_in_full_without_truncating_it(answer):
    judge = Answers([answer, '{"keep":["M02"]}'])
    result = ask_moment_pick(judge, "pick", "Choose one row.", labels={"M01", "M02"}, count=1)
    assert result == ["M02"]
    assert [c[0] for c in judge.calls] == ["pick", "pick-repair"]


def test_repeated_invalid_answer_fails_after_one_repair():
    judge = Answers(['{"keep":["M01","M01"]}'] * 2)
    with pytest.raises(ValueError, match="after bounded repair"):
        ask_moment_pick(judge, "pick", "Choose two rows.", labels={"M01", "M02"}, count=2)
    assert len(judge.calls) == 2


def test_both_pick_orders_use_label_free_format_and_validate_the_grant():
    judge = Answers([json.dumps({"keep": ["M03"]})] * 2)
    choices = [
        DepictedChoice(str(i), "K01", f"2030-05-01T1{i}:00", "An outing", str(i)) for i in range(4)
    ]
    result = pick_story_moments(
        judge,
        story={"key": "K01", "title": "An outing"},
        choices=choices,
        count=1,
        starred=lambda _: False,
        contract="The month",
        record=lambda *_: None,
    )
    assert result == [choices[2]]
    assert len(judge.calls) == 2
    for _, prompt in judge.calls:
        format_instruction = prompt.rsplit("Return JSON only", 1)[1]
        assert "exactly 1" in format_instruction
        assert "unused_slots" not in prompt and "why_fewer" not in prompt
        assert "M01" not in format_instruction and "M04" not in format_instruction


@pytest.mark.parametrize("favourite", [False, True])
def test_conflicting_choices_keep_favourite_or_model_priority_before_filling_next_moment(favourite):
    choices = [
        DepictedChoice(str(i), "K01", f"2030-05-01T10:0{i}", "An outing", str(i)) for i in range(3)
    ]
    judge = Answers(['{"keep":["M02","M01"]}'] * 2)

    def compatible(candidate, selected):
        return candidate.key == "2" or all(other.key == "2" for other in selected)

    result = pick_story_moments(
        judge,
        story={"key": "K01", "title": "An outing"},
        choices=choices,
        count=2,
        starred=lambda c: favourite and c.key == "0",
        contract="The month",
        record=lambda *_: None,
        compatible=compatible,
    )
    assert [c.key for c in result] == ["0" if favourite else "1", "2"]


@pytest.mark.parametrize("favourites_fill", [False, True])
def test_picture_comparison_is_once_per_contested_choice_and_skips_filled_favourites(
    favourites_fill,
):
    choices = [
        DepictedChoice(str(i), "K01", f"2030-05-01T1{i}:00", "A ride", str(i)) for i in range(3)
    ]
    observed = []

    def picture(choice):
        observed.append(choice.key)
        return (
            "Friends smiling in the setting"
            if choice.key == "1"
            else "Distant activity under dense overlays"
        )

    judge = Answers(['{"keep":["M02"]}'] * 2)
    selected = pick_story_moments(
        judge,
        story={"key": "K01", "title": "An outing"},
        choices=choices,
        count=1,
        starred=lambda c: favourites_fill and c.key == "2",
        contract="The month",
        record=lambda *_: None,
        picture_of=picture,
    )
    if favourites_fill:
        assert observed == [] and judge.calls == []
        assert selected == [choices[2]]
    else:
        assert observed == ["0", "1", "2"]
        assert selected == [choices[1]]
        for _, prompt in judge.calls:
            assert "Friends smiling in the setting" in prompt
            assert "Distant activity under dense overlays" in prompt
            assert "cached preview only" in prompt
