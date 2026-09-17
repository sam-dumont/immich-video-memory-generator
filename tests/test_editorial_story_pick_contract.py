"""The picker must see original duration and return a complete valid choice."""

import json
from types import SimpleNamespace

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


def test_overfull_pick_repair_sees_its_rejected_list_and_exact_excess():
    offered = {f"M{i:02d}" for i in range(1, 77)}
    rejected = json.dumps({"keep": sorted(offered)[:47], "unused_slots": 0})
    corrected = sorted(offered)[1:47]
    judge = Answers([rejected, json.dumps({"keep": corrected, "unused_slots": 0})])

    result = ask_moment_pick(
        judge,
        "pick",
        "Choose at most 46 from all 76 moments.",
        labels=offered,
        count=46,
        allow_fewer=True,
    )

    assert result == corrected
    assert len(result) == 46
    assert judge.calls[0][1] == "Choose at most 46 from all 76 moments."
    repair = judge.calls[1][1]
    assert rejected in repair
    assert "received 47 labels" in repair
    assert "Remove at least 1" in repair
    assert "complete replacement" in repair


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
def test_conflicting_choices_keep_model_priority_before_filling_the_next_moment(favourite):
    """A star on a moment the pick did not name buys no slot, however the choices conflict."""
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
    assert [c.key for c in result] == ["1", "2"]


@pytest.mark.parametrize("starred", [False, True])
def test_a_contested_shortlist_is_asked_from_its_inventory_star_or_not(starred):
    choices = [
        DepictedChoice(str(i), "K01", f"2030-05-01T1{i}:00", f"A ride {i}", str(i))
        for i in range(3)
    ]
    judge = Answers(['{"keep":["M02"]}'] * 2)
    selected = pick_story_moments(
        judge,
        story={"key": "K01", "title": "An outing"},
        choices=choices,
        count=1,
        starred=lambda c: starred and c.key == "2",
        contract="The month",
        record=lambda *_: None,
    )
    assert selected == [choices[1]]
    assert len(judge.calls) == 2
    for _, prompt in judge.calls:
        assert "A ride 1" in prompt  # the inventory content line carries the choice
        assert "Proposed picture" not in prompt


# The exact answer a hosted qwen3-30b gave on the June 2024 fixture (issue #908).
_ECHOED_ROW = (
    "M01 | 2024-06-01T08:15 | A person, identified as Charlie, sits at a cafe table in "
    "Brussels, Belgium, holding a coffee cup. The setting is indoor, with soft morning l "
    "| 2 picture(s)"
)


def test_an_echoed_offered_row_is_read_as_its_label_without_a_repair():
    judge = Answers([json.dumps({"keep": [_ECHOED_ROW]})])
    result = ask_moment_pick(judge, "pick", "Choose one row.", labels={"M01", "M02"}, count=1)
    assert result == ["M01"]
    assert [c[0] for c in judge.calls] == ["pick"]


def test_an_echoed_row_whose_label_was_never_offered_is_still_refused():
    unknown = f'{{"keep": ["M99 | {_ECHOED_ROW.split(" | ", 1)[1]}"]}}'
    judge = Answers([unknown] * 2)
    with pytest.raises(ValueError, match="absent from the offered rows"):
        ask_moment_pick(judge, "pick", "Choose one row.", labels={"M01", "M02"}, count=1)


def test_the_pick_prompt_names_the_label_shape_beside_the_rows():
    judge = Answers([json.dumps({"keep": ["M03"]})] * 2)
    choices = [
        DepictedChoice(str(i), "K01", f"2030-05-01T1{i}:00", "An outing", str(i)) for i in range(4)
    ]
    pick_story_moments(
        judge,
        story={"key": "K01", "title": "An outing"},
        choices=choices,
        count=1,
        starred=lambda _: False,
        contract="The month",
        record=lambda *_: None,
    )
    for _, prompt in judge.calls:
        assert 'each row starts with its label (e.g. "M01")' in prompt


def test_the_repair_question_lists_the_labels_that_were_offered():
    judge = Answers(['{"keep":["M09"]}', '{"keep":["M02"]}'])
    ask_moment_pick(judge, "pick", "Choose one row.", labels={"M02", "M01"}, count=1)
    assert 'The offered labels are "M01", "M02".' in judge.calls[1][1]


def test_a_refused_pick_is_asked_again_on_the_next_run_instead_of_replayed(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from immich_memories.analysis import editorial_text_gateway as gateway
    from immich_memories.analysis.editorial_structure_io import StructureTextJudge
    from immich_memories.config_models_llm import LLMConfig

    answers = iter(['{"keep":["M99"]}', '{"keep":["M99"]}', '{"keep":["M01"]}'])
    asked: list[str] = []

    # WHY: replaces the only external boundary, the text provider's HTTP transport.
    async def fake_query(prompt, _config, **_kwargs):
        asked.append(prompt)
        return next(answers)

    monkeypatch.setattr(gateway, "query_llm", fake_query)
    config = SimpleNamespace(
        llm=LLMConfig(
            provider="openai-compatible",
            base_url="http://text.test/v1",
            model="test-model",
            api_key="private-test-credential",
        )
    )

    out = tmp_path / "out"
    out.mkdir()

    def run() -> list[str]:
        judge = StructureTextJudge(config, out, cache_path=tmp_path / "judgments.sqlite")
        return ask_moment_pick(
            judge, "story-pick-K01", "Choose one row.", labels={"M01", "M02"}, count=1
        )

    with pytest.raises(ValueError, match="after bounded repair"):
        run()
    assert run() == ["M01"]
    assert len(asked) == 3


def test_one_label_where_a_list_belongs_is_one_pick():
    """The slip that broke the period read on a local 35B: a bare string for a list.

    A single offered label is not ambiguous, and the answer here is exhausted, so
    a repair round would raise StopIteration rather than quietly rescue the pick.
    """
    judge = Answers([json.dumps({"keep": "M01"})])

    assert ask_moment_pick(judge, "pick", "Choose one row.", labels={"M01", "M02"}, count=1) == [
        "M01"
    ]


def test_a_valid_shortfall_without_a_reason_survives_its_repair():
    """Thirteen of fourteen named moments are a choice; a missing sentence is not worth a film."""
    answers = iter(
        [
            json.dumps({"keep": ["M01", "M02"], "unused_slots": 1, "why_fewer": None}),
            json.dumps({"keep": ["M01", "M02"], "unused_slots": 1, "why_fewer": ""}),
        ]
    )
    recorded: list[dict] = []
    judge = SimpleNamespace(ask=lambda *_args, **_kwargs: next(answers))

    kept = ask_moment_pick(
        judge,
        "story-pick-K01",
        "question",
        labels={"M01", "M02", "M03"},
        count=3,
        allow_fewer=True,
        record=recorded.append,
    )

    assert kept == ["M01", "M02"]
    assert recorded == [{"keep": ["M01", "M02"], "unused_slots": 1, "why_fewer": "not given"}]
