"""Selection reuses episode importance while retaining explicit subject scope."""

import json
import re
from dataclasses import replace

import pytest

from immich_memories.analysis.editorial_intent import build_editorial_intent
from tests.test_editorial_duration_planner_integration import run
from tests.test_editorial_story_first_planner import StoryJudge, make_source


@pytest.mark.parametrize("product", ["year_in_review", "monthly_highlights", "person_spotlight"])
def test_story_importance_does_not_need_a_separate_worthiness_ballot(tmp_path, product):
    source = make_source(tmp_path)
    case = replace(source.case, product=product)
    source = replace(
        source,
        case=case,
        intent=build_editorial_intent(product, case.ranges, brief=case.brief),
    )
    # WHY: deterministic responses replace the paid editorial model boundary.
    judge = StoryJudge()

    plan = run(source, judge)

    assert plan["carriers"]
    assert {row["asset_id"] for row in plan["carriers"]} <= source.assets.keys()
    assert plan["content_seconds"] <= plan["target_seconds"]
    assert not any(call["stage"].startswith("worthy-") for call in judge.calls)
    assert any(call["stage"].startswith("story-understanding") for call in judge.calls)


class ScopedStoryJudge(StoryJudge):
    def answer(self, stage, prompt):
        if stage.startswith("worthy-"):
            offered = set(re.findall(r"^(F\d+):", prompt, re.MULTILINE))
            assert offered
            return json.dumps(
                {"worthy": dict.fromkeys(offered & {"F01", "F02"}, "Shows the requested outing")}
            )
        return super().answer(stage, prompt)


@pytest.mark.parametrize("product", ["custom", "trip"])
def test_scoped_admission_still_excludes_unrelated_material(tmp_path, product):
    source = make_source(tmp_path)
    case = replace(source.case, product=product, brief="Show only the first two canal outings.")
    source = replace(
        source,
        case=case,
        intent=build_editorial_intent(product, case.ranges, brief=case.brief),
        gps={
            f"o{occasion}-p{picture}": (50.0, float(occasion))
            for occasion in range(4)
            for picture in range(3)
        },
    )
    # WHY: the model boundary declares which outings belong to the requested subject.
    judge = ScopedStoryJudge()

    plan = run(source, judge)

    assert plan["carriers"]
    assert all(row["asset_id"].startswith(("o0-", "o1-")) for row in plan["carriers"])
    assert any(call["stage"].startswith("worthy-") for call in judge.calls)


class DismissiveWeightJudge(StoryJudge):
    def answer(self, stage, prompt):
        if stage.startswith("story-weighing"):
            keys = re.findall(r"^(K\d{2}) \|", prompt, re.MULTILINE)
            return json.dumps({"about": [], "weights": dict.fromkeys(keys, "none")})
        return super().answer(stage, prompt)


def test_existing_central_episode_reading_protects_an_occasion_without_an_extra_ballot(tmp_path):
    source = make_source(tmp_path)
    # WHY: the later model pass disagrees with the monthly reading's central occasion.
    judge = DismissiveWeightJudge()

    plan = run(source, judge)

    assert plan["carriers"], "the already-read central occasion must retain a place"
    assert all(row["asset_id"].startswith(("o0-", "o2-")) for row in plan["carriers"])
    assert not any(call["stage"].startswith("worthy-") for call in judge.calls)
