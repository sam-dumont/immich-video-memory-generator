"""Only complete story judgments can reach the funded selection."""

import json
from copy import deepcopy

import pytest

from immich_memories.analysis.editorial_story_reading import read_period_story
from immich_memories.analysis.editorial_story_weighing import _weigh_stories
from immich_memories.analysis.editorial_story_weight_contract import StoryWeightDecisionError
from tests.test_editorial_story_reading import ScriptedJudge, fragment, opened, place


def story_rows():
    return [
        {"key": f"K{i:02}", "episodes": [f"S{i:04}"], "title": f"Occasion {i}"} for i in range(1, 4)
    ]


def weigh(judge, *, candidates=("K03",), stories=None, records=None):
    return _weigh_stories(
        judge,
        story_rows() if stories is None else stories,
        thesis="Several occasions.",
        hints={
            f"S{i:04}": {"moments": i + 1, "pictures": i + 1, "gate": "maybe"} for i in range(1, 4)
        },
        contract="Test memory.",
        record=([] if records is None else records).append,
        day_of=lambda _key: "2030-03-01",
        candidates=candidates,
    )


def test_copied_example_is_repaired_twice_then_stops_before_funding():
    copied = '{"about":["K02"],"weights":{"K01":"major","K03":"none"}}'
    judge = ScriptedJudge(lambda _stage, _prompt: copied)
    records, stories = [], story_rows()

    with pytest.raises(StoryWeightDecisionError) as failure:
        weigh(judge, stories=stories, records=records)

    assert failure.value.audit["ignored_about_entries"] == ["K02"]
    assert failure.value.audit["missing_weight_keys"] == ["K02"]
    assert [call["stage"] for call in judge.asked] == [
        "story-weighing-source",
        "story-weighing-source-repair",
        "story-weighing-source-repair-2",
    ]
    assert [row["status"] for row in records] == ["invalid", "invalid", "invalid"]
    assert all("weight" not in story for story in stories)


def test_a_sample_sized_repair_gets_one_more_bounded_repair():
    """A 60-row season table: the first reply names an unoffered central story, the
    repair answers half the table. The second repair carries both rejections."""
    complete = '{"about":[],"weights":{"K01":"major","K02":"minor","K03":"none"}}'
    source_replies = iter(
        [
            '{"about":["K02"],"weights":{"K01":"major","K02":"minor","K03":"none"}}',
            '{"about":[],"weights":{"K01":"major"}}',
            complete,
        ]
    )
    prompts = []

    def reply(stage, prompt):
        prompts.append(prompt)
        return next(source_replies) if stage.startswith("story-weighing-source") else complete

    judge = ScriptedJudge(reply)
    records = []

    weigh(judge, candidates=(), records=records)

    assert [call["stage"] for call in judge.asked][:3] == [
        "story-weighing-source",
        "story-weighing-source-repair",
        "story-weighing-source-repair-2",
    ]
    validations = [row for row in records if row.get("stage", "").endswith("-validation")]
    assert [row["status"] for row in validations][:3] == ["invalid", "invalid", "complete"]
    assert prompts[1].count("PREVIOUS ANSWER REJECTED") == 1
    assert prompts[2].count("PREVIOUS ANSWER REJECTED") == 2
    assert '"K02", "K03"' in prompts[2]


def test_complete_confirmation_does_not_require_a_weight_for_the_central_story():
    reply = '{"about":["K03"],"weights":{"K01":"minor","K02":"none"}}'
    records = []
    judge = ScriptedJudge(lambda _stage, _prompt: reply)

    result = weigh(judge, records=records)

    audit = records[-1]["judgment_audit"]
    assert [story["weight"] for story in result] == ["minor", "none", "dominant"]
    assert audit["central"]["confirmed"] == ["K03"]
    assert audit["central"]["fallback"] == []
    assert audit["review_required"] is False
    assert all(order["coverage_complete"] for order in audit["orders"].values())
    assert len(judge.asked) == 2


def test_a_complete_quiet_period_needs_no_central_story_or_fallback():
    reply = '{"about":[],"weights":{"K01":"none","K02":"none","K03":"none"}}'
    records = []

    result = weigh(ScriptedJudge(lambda _stage, _prompt: reply), candidates=(), records=records)

    assert [story["weight"] for story in result] == ["none", "none", "none"]
    audit = records[-1]["judgment_audit"]
    assert audit["central"] == {"confirmed": [], "fallback": [], "overrode_explicit_none": []}
    assert audit["review_required"] is False


def test_a_corrected_reply_replaces_all_initial_weights_joins_and_retitles():
    invalid = json.dumps(
        {
            "about": [],
            "weights": {"K01": "major"},
            "join": [["K01", "K02"]],
            "retitle": {"K01": "An abandoned edit"},
        }
    )
    corrected = '{"about":["K03"],"weights":{"K01":"none","K02":"minor"}}'
    judge = ScriptedJudge(
        lambda stage, _prompt: invalid if stage == "story-weighing-source" else corrected
    )
    records = []

    result = weigh(judge, records=records)

    assert [story["weight"] for story in result] == ["none", "minor", "dominant"]
    assert [story["title"] for story in result] == ["Occasion 1", "Occasion 2", "Occasion 3"]
    assert [story["episodes"] for story in result] == [["S0001"], ["S0002"], ["S0003"]]
    assert len(judge.asked) == 3
    repair = judge.prompt("story-weighing-source-repair")
    assert repair.startswith(judge.prompt("story-weighing-source"))
    assert "missing_weight_keys" in repair
    assert "entire story table" in repair
    assert [row["status"] for row in records[:-1]] == ["invalid", "complete", "complete"]


def test_valid_source_cannot_mutate_stories_when_reverse_stays_invalid():
    valid = json.dumps(
        {
            "about": [],
            "weights": {"K01": "major", "K02": "minor", "K03": "none"},
            "join": [["K01", "K02"]],
            "retitle": {"K01": "A proposed edit"},
        }
    )
    judge = ScriptedJudge(
        lambda stage, _prompt: valid if stage == "story-weighing-source" else "not JSON"
    )
    stories, records = story_rows(), []
    before = deepcopy(stories)

    with pytest.raises(StoryWeightDecisionError):
        weigh(judge, stories=stories, records=records)

    assert [
        {key: story[key] for key in old} for story, old in zip(stories, before, strict=True)
    ] == before
    assert all("weight" not in story and "joined_into" not in story for story in stories)
    assert [call["stage"] for call in judge.asked] == [
        "story-weighing-source",
        "story-weighing-reversed",
        "story-weighing-reversed-repair",
        "story-weighing-reversed-repair-2",
    ]
    assert [row["status"] for row in records] == ["complete", "invalid", "invalid", "invalid"]
    assert records[-1]["judgment_audit"]["parse_failed"] is True


def test_period_reader_persists_incomplete_audit_when_weighing_exhausts_repair():
    def reply(stage, _prompt):
        if stage == "story-episodes-1":
            return place(["M000/1"], "S0001", [opened("S0001", "A small outing")])
        if stage == "story-understanding-1":
            return json.dumps(
                {
                    "thesis": "A small outing.",
                    "about": [],
                    "stories": [
                        {"title": "A small outing", "episodes": ["S0001"], "purpose": "The outing."}
                    ],
                    "uncertainties": [],
                }
            )
        return '{"about":[],"weights":{}}'

    records = []
    judge = ScriptedJudge(reply)

    with pytest.raises(StoryWeightDecisionError):
        read_period_story(
            judge, evidence=[fragment(0)], contract="Test memory.", prior={}, record=records.append
        )

    assert records[-1]["status"] == "incomplete"
    assert "incomplete story weighting" in records[-1]["failure"]
    validations = [row for row in records[-1]["synthesis"] if row["stage"].endswith("-validation")]
    assert [row["attempt"] for row in validations] == [1, 2, 3]
    assert all(row["judgment_audit"]["missing_weight_keys"] == ["K01"] for row in validations)
    assert [call["stage"] for call in judge.asked][-3:] == [
        "story-weighing-source",
        "story-weighing-source-repair",
        "story-weighing-source-repair-2",
    ]
