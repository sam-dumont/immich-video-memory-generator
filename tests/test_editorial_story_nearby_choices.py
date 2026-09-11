"""Competing nearby pictures stay visible without becoming extra physical slots."""

import json
import re
from dataclasses import replace
from datetime import timedelta

import pytest

from tests.editorial_story_fixtures import ControlledStoryJudge
from tests.test_editorial_duration_planner_integration import run
from tests.test_editorial_story_first_planner import make_source


class CompanyJudge(ControlledStoryJudge):
    """Prefer the source showing shared company if production still offers it."""

    def answer(self, stage, prompt):
        if stage.startswith("story-pick-"):
            count = int(re.search(r"gets (\d+) picture", prompt)[1])
            rows = re.findall(r"^(M\d{2}) \| (.*)$", prompt, re.MULTILINE)
            ordered = sorted(rows, key=lambda row: "Friends sharing the outing" not in row[1])
            return json.dumps({"keep": [label for label, _ in ordered[:count]]})
        result = super().answer(stage, prompt)
        if stage.startswith("moment-inventory"):
            data = json.loads(result)
            data["moments"][1]["content"] = "Friends sharing the outing"
            return json.dumps(data)
        return result


def nearby_source(tmp_path, *, favourite=False, held=False):
    source = make_source(tmp_path, occasions=2, pictures=3)
    assets = dict(source.assets)
    annotations = dict(source.audience_annotations)
    for occasion in range(2):
        first, later = f"o{occasion}-p0", f"o{occasion}-p1"
        old = assets[later].file_created_at
        new = assets[first].file_created_at + timedelta(seconds=6)
        assets[later] = assets[later].model_copy(update={"file_created_at": new})
        assets[first] = assets[first].model_copy(update={"is_favorite": favourite})
        row = annotations[later]
        text = row.text.replace(old.isoformat(), new.isoformat())
        if held:
            text = "A person is bathing in a bathtub."
        annotations[later] = replace(row, text=text, description=text if held else row.description)
    return replace(
        source,
        assets=assets,
        audience_annotations=annotations,
        annotations={key: row.text for key, row in annotations.items()},
    )


def test_later_company_picture_reaches_pick_without_creating_duplicate_depth(tmp_path):
    judge = CompanyJudge()
    plan = run(nearby_source(tmp_path), judge)
    selected = {c["asset_id"] for c in plan["carriers"]}
    assert {"o0-p1", "o1-p1"} <= selected
    assert not {"o0-p0", "o1-p0"} & selected
    assert len(selected) == 4  # two physical moments per outing, not three
    prompts = [c["prompt"] for c in judge.calls if c["stage"].startswith("story-pick-")]
    assert prompts and all("Friends sharing the outing" in prompt for prompt in prompts)


@pytest.mark.parametrize("favourite,held", [(True, False), (False, True)])
def test_nearby_comparison_preserves_favourites_and_audience_holds(tmp_path, favourite, held):
    plan = run(nearby_source(tmp_path, favourite=favourite, held=held), CompanyJudge())
    selected = {c["asset_id"] for c in plan["carriers"]}
    if favourite:
        assert selected == {"o0-p0", "o1-p0"}  # existing starred capture-group narrowing
    else:
        assert selected and all(any(a.startswith(f"o{i}-") for a in selected) for i in range(2))
    assert not {"o0-p1", "o1-p1"} & selected
    assert len(selected) <= 4


def test_actual_planner_compares_bound_preview_observations_before_admission(tmp_path):
    from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
    from immich_memories.analysis.editorial_structure_planner import plan_structure
    from tests.test_editorial_visual_body_audience import picture_record

    observed = []

    def observe(asset_id):
        observed.append(asset_id)
        record = picture_record()
        record["description"] = (
            f"A clothed participant shares the outing; observed picture {asset_id}."
        )
        return record

    class ObservedJudge(CompanyJudge):
        def answer(self, stage, prompt):
            if stage.startswith("story-pick-"):
                assert "Proposed picture (cached preview only):" in prompt
                assert "picture observations: A clothed participant shares the outing" in prompt
                assert observed  # acquisition precedes the first choice, not just its admission
            return super().answer(stage, prompt)

    captured = nearby_source(tmp_path)
    plan = plan_structure(
        captured,
        StructurePlannerPorts(
            judge=ObservedJudge(),
            thumbnail_hash=lambda _: None,
            rank=lambda _query, documents: dict.fromkeys(range(len(documents)), 1.0),
            reranker_identity={"endpoint": "test://local", "model": "controlled"},
            observe_picture=observe,
        ),
    ).plan
    assert len(observed) == len(set(observed)) == 6
    assert len(plan["carriers"]) == 4
    assert all("picture observations" not in line for line in captured.annotations.values())


def test_dense_early_favourites_cannot_crowd_later_favourites_out_of_actual_shortlist(tmp_path):
    captured = make_source(tmp_path, occasions=2, pictures=12)
    assets = dict(captured.assets)
    annotations = dict(captured.audience_annotations)
    for occasion in range(2):
        start = assets[f"o{occasion}-p0"].file_created_at
        for index in range(12):
            key = f"o{occasion}-p{index}"
            old = assets[key].file_created_at
            taken = start + timedelta(seconds=6 * index if index < 10 else 1200 * (index - 9))
            assets[key] = assets[key].model_copy(
                update={"file_created_at": taken, "is_favorite": True}
            )
            row = annotations[key]
            annotations[key] = replace(
                row, text=row.text.replace(old.isoformat(), taken.isoformat())
            )
    captured = replace(
        captured,
        assets=assets,
        audience_annotations=annotations,
        annotations={key: row.text for key, row in annotations.items()},
    )
    plan = run(captured, ControlledStoryJudge())
    selected = {row["asset_id"] for row in plan["carriers"]}
    assert {f"o{o}-p{i}" for o in range(2) for i in (10, 11)} <= selected
    shortlist_files = list(captured.artifact_dir.rglob("story-shortlist-pass-1.private.json"))
    assert len(shortlist_files) == 1
    shortlist = json.loads(shortlist_files[0].read_text())
    assert all(len(row["shortlisted"]) == 3 for row in shortlist.values())
    assert all(not row["nearby_alternatives"] for row in shortlist.values())


def test_nearby_nomination_is_bounded_and_respects_the_owners_representative():
    from immich_memories.analysis.editorial_story_shortlist import (
        DepictedChoice,
        nearby_picture_alternatives,
    )

    choices = [
        DepictedChoice(str(i), "outing", f"2030-05-02T10:00:{i:02d}", "People cycling", str(i))
        for i in range(5)
    ]
    units = {
        c.primary: ("outing", {"asset_id": c.primary, "moment": "capture", "taken": c.taken})
        for c in choices
    }
    # A nearby favourite leads the alternative comparison, but does not add capacity.
    assert nearby_picture_alternatives(
        choices[:1], choices, units, starred=lambda c: c.key == "3"
    ) == [choices[3]]
    # If the original representative is already a favourite, its burst stays closed.
    assert (
        nearby_picture_alternatives(choices[:1], choices, units, starred=lambda c: c.key == "0")
        == []
    )
