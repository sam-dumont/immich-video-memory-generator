"""Physical partition limits reserve story capacity before picture acquisition."""

import json
import re
from dataclasses import replace

import pytest

from immich_memories.analysis import editorial_structure_planner as planner
from tests.editorial_story_fixtures import AnnualStoryJudge, ControlledStoryJudge
from tests.test_editorial_duration_planner_integration import (
    asked_again,
    run,
    semantic_plan,
    source,
)
from tests.test_editorial_on_this_day_year_limit import make_source


@pytest.mark.parametrize("seconds", [60, 180])
def test_unlimited_contract_ignores_partition_resolver_and_keeps_every_request(
    tmp_path, monkeypatch, seconds
):
    captured = source(tmp_path, seconds=seconds)
    cold_judge = ControlledStoryJudge()
    cold = run(captured, cold_judge)
    original = planner.select_story_first

    def forbidden(_taken):
        pytest.fail("An unlimited contract must not partition story allocation")

    def unlimited(**kwargs):
        assert kwargs["partition_limit"] is None
        kwargs["partition_of"] = forbidden
        return original(**kwargs)

    monkeypatch.setattr(planner, "select_story_first", unlimited)
    warm_judge = ControlledStoryJudge(cold_judge.bank, require_hits=True)
    warm = run(captured, warm_judge)
    assert semantic_plan(warm) == semantic_plan(cold)
    assert [(row["stage"], row["prompt"]) for row in warm_judge.calls] == asked_again(
        cold_judge.calls
    )
    assert all(row["cache_hit"] for row in warm_judge.calls)


@pytest.mark.parametrize("starred", [False, True])
def test_equal_weight_occasions_keep_chronology_and_a_star_cannot_reorder_them(tmp_path, starred):
    """One slot per year, two equal occasions in each: the earlier occasion takes the slot, and a
    star on the later one is an indicator of a picture, not a claim on the year's slot."""

    class EqualWeightJudge(AnnualStoryJudge):
        def answer(self, stage, prompt):
            if stage.startswith("story-weighing"):
                keys = re.findall(r"^(K\d{2}) \|", prompt, re.MULTILINE)
                return json.dumps({"about": [], "weights": dict.fromkeys(keys, "major")})
            return super().answer(stage, prompt)

    captured = make_source(tmp_path)
    if starred:
        captured = replace(
            captured,
            assets={
                key: asset.model_copy(update={"is_favorite": key.endswith("e1-p1")})
                for key, asset in captured.assets.items()
            },
        )
    judge = EqualWeightJudge()
    plan = run(captured, judge)
    assert [row["asset_id"] for row in plan["carriers"]] == [
        f"y{year}-e0-p2" for year in (2030, 2031, 2032)
    ]
    assert plan["intent_report"]["violations"] == []
    assert not any(row["stage"].startswith("moment-inventory") for row in judge.calls)


def test_audience_rejections_and_occasion_fallback_cannot_reopen_full_partitions(tmp_path):
    captured = make_source(tmp_path)
    # The occasion the weighing funds keeps one sendable picture; the pictures the pick
    # reaches for first do not.
    private = {key for key in captured.assets if key.endswith(("-e1-p1", "-e1-p2"))}
    rows = {
        key: replace(
            row,
            text="A person is bathing in a bathtub.",
            description="A person is bathing in a bathtub.",
        )
        if key in private
        else row
        for key, row in captured.audience_annotations.items()
    }
    captured = replace(
        captured,
        audience="sendable",
        audience_annotations=rows,
        annotations={key: row.text for key, row in rows.items()},
    )
    judge = AnnualStoryJudge()
    plan = run(captured, judge)
    assert len(plan["carriers"]) == 3
    assert {row["asset_id"] for row in plan["carriers"]}.isdisjoint(private)
    assert plan["intent_report"]["coverage"] == {f"year-{year}": 1 for year in (2030, 2031, 2032)}
    assert plan["intent_report"]["violations"] == []
    assert plan["shareability"]["substituted"]
