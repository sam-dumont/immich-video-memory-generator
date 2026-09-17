"""A complete editorial shortfall is not a request to refill with unchosen views."""

import json
import re

import pytest

from immich_memories.analysis.editorial_story_shortlist import DepictedChoice, pick_story_moments


class DensityJudge:
    def __init__(self, orders):
        self.orders = iter(orders)
        self.calls = []

    def ask(self, stage, prompt, **_kwargs):
        self.calls.append((stage, prompt))
        return json.dumps(next(self.orders))


def vote(*labels, unused):
    return {
        "keep": list(labels),
        "unused_slots": unused,
        "why_fewer": "The remaining views repeat the same activity without another contribution.",
    }


def choose(judge, *, stars=(), count=3, size=5):
    choices = [
        DepictedChoice(
            str(i),
            "K01",
            f"2030-05-{i + 1:02d}T10:00:00",
            "Another view of the same activity",
            str(i),
        )
        for i in range(size)
    ]
    records = []
    result = pick_story_moments(
        judge,
        story={"key": "K01", "title": "An outing", "seen": {"days": 1}},
        choices=choices,
        count=count,
        starred=lambda c: c.key in stars,
        contract="Remember the outing",
        record=lambda _name, value: records.append(value),
    )
    return result, records


@pytest.mark.parametrize("second", ["M01", "M03"])
def test_explicit_one_picture_votes_do_not_get_refilled_to_three(second):
    judge = DensityJudge([vote("M01", unused=2), vote(second, unused=2)])
    result, records = choose(judge)
    assert len(result) == 1
    assert result[0].key == "0"
    assert len(judge.calls) == 2
    assert records[-1]["editorial_limit"] == 1


def test_an_explicit_shortfall_keeps_only_what_the_pick_named_beside_stars():
    """Stars are indicators: they do not reserve the slots a complete shortfall gave back."""
    judge = DensityJudge([vote("M04", unused=2)] * 2)
    result, records = choose(judge, stars={"0", "1"})
    assert [c.key for c in result] == ["3"]
    assert records[-1]["editorial_limit"] == 1


def test_available_capacity_does_not_force_every_unstarred_view_into_the_pick():
    judge = DensityJudge([vote("M01", unused=2)] * 2)
    result, _records = choose(judge, size=3)
    assert len(result) == 1
    assert len(judge.calls) == 2


def test_unexplained_underfilled_answer_still_requires_a_complete_repair():
    complete = {"keep": ["M01", "M02", "M03"]}
    judge = DensityJudge([{"keep": ["M01"]}, complete, complete])
    result, _records = choose(judge)
    assert len(result) == 3
    assert [stage for stage, _prompt in judge.calls] == [
        "story-pick-K01-source",
        "story-pick-K01-source-repair",
        "story-pick-K01-reversed",
    ]


def test_repair_distinguishes_unused_grant_from_rejected_candidates():
    complete = {"keep": ["M01", "M02", "M03"], "unused_slots": 0}
    judge = DensityJudge([{**complete, "unused_slots": 2}, complete, complete])
    result, records = choose(judge)
    assert len(result) == 3
    repair = judge.calls[1][1]
    assert "3 - 3 = 0" in repair
    assert "not the number of rejected candidates" in repair
    assert all(v["unused_slots"] == 0 for v in records[0]["vote_records"])


def test_normal_planner_does_not_reopen_declined_depth_in_later_passes(tmp_path):
    from tests.test_editorial_duration_planner_integration import run
    from tests.test_editorial_story_first_planner import StoryJudge, make_source

    class OneContribution(StoryJudge):
        def answer(self, stage, prompt):
            if stage.startswith("story-pick-"):
                count = int(re.search(r"gets (\d+) picture", prompt)[1])
                labels = re.findall(r"^(M\d+) \|", prompt, re.MULTILINE)
                # Keep the same first source irrespective of display order.
                return json.dumps(vote(min(labels), unused=count - 1))
            return super().answer(stage, prompt)

    source = make_source(tmp_path, occasions=4, pictures=4)
    plan = run(source, OneContribution())
    assert len(plan["carriers"]) == 2
    (audit_path,) = source.artifact_dir.rglob("story-selection.private.json")
    audit = json.loads(audit_path.read_text())
    assert len(audit["editorially_closed"]) == 2
    assert not audit["kept_without_standing"]
    assert all(row["added"] == 0 for row in audit["passes"][1:])


@pytest.mark.parametrize(
    "count,videos,stars,expected",
    [
        (1, 3, (), ["0", "1", "2"]),  # one slot is still a choice between a video and a still
        (2, 1, (), ["0"]),
        (2, 3, ("0", "1"), ["0", "1", "2"]),  # stars settle nothing: the depth is still contested
        (3, 2, (), ["0", "1"]),
    ],
)
def test_every_offered_video_is_sampled_whatever_the_grant(count, videos, stars, expected):
    choices = [
        DepictedChoice(str(i), "K01", f"2030-05-01T10:{i}0:00", "A different cover pose", str(i))
        for i in range(3)
    ]
    judge = DensityJudge([vote("M01", unused=count - 1)] * 2)
    reads, records = [], []
    pick_story_moments(
        judge,
        story={"key": "K01", "title": "An outing"},
        choices=choices,
        count=count,
        starred=lambda c: c.key in stars,
        contract="Family",
        record=lambda _, r: records.append(r),
        is_video=lambda c: int(c.key) < videos,
        motion_of=lambda c: reads.append(c.key) or "The same toss, catch and bend sequence.",
    )
    assert reads == expected
    for _stage, prompt in judge.calls:
        assert ("Sampled sequence:" in prompt) == bool(expected)
        if expected:
            assert (
                "A cover's pose or inventory label cannot establish a separate activity" in prompt
            )
