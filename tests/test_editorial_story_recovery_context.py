"""Occasion recovery may relax standing votes, never the carrier's context requirement."""

import json
import re
from dataclasses import replace

import pytest

from tests.test_editorial_duration_planner_integration import run
from tests.test_editorial_story_first_planner import StoryJudge, make_source


class RecoveryJudge(StoryJudge):
    def __init__(self, *, weak=False):
        super().__init__()
        self.weak = weak

    def answer(self, stage, prompt):
        if stage.startswith("worthy-"):
            labels = re.findall(r"^(F\d+)(?: \(near home\))?:", prompt, re.MULTILINE)
            return json.dumps({"worthy": dict.fromkeys(labels, "A meaningful occasion")})
        if stage.startswith("story-weighing"):
            labels = re.findall(r"^(K\d+) \|", prompt, re.MULTILINE)
            return json.dumps({"about": [], "weights": dict.fromkeys(labels, "major")})
        if stage.startswith("standing-") and self.weak:
            labels = re.findall(r"^(P\d+):", prompt, re.MULTILINE)
            assert labels
            return json.dumps({"weak": dict.fromkeys(labels, "A weak picture")})
        return super().answer(stage, prompt)


def recovery_source(tmp_path, *, pictures=1, object_only=True, favourite=False):
    source = make_source(tmp_path, occasions=2, pictures=pictures)
    assets, annotations = dict(source.assets), dict(source.audience_annotations)
    for index in range(pictures):
        key = f"o0-p{index}"
        assets[key] = assets[key].model_copy(update={"is_favorite": favourite})
        if object_only:
            row = annotations[key]
            description = "A ceramic bowl sits alone on a wooden table."
            annotations[key] = replace(row, text=description, description=description)
    return replace(
        source,
        assets=assets,
        audience_annotations=annotations,
        annotations={key: row.text for key, row in annotations.items()},
    )


@pytest.mark.parametrize("pictures", [1, 2])
@pytest.mark.parametrize("weak", [False, True])
def test_recovery_cannot_revive_a_thin_object_story_even_when_it_is_major(tmp_path, pictures, weak):
    plan = run(recovery_source(tmp_path, pictures=pictures), RecoveryJudge(weak=weak))
    assert plan["carriers"]
    assert all(not c["asset_id"].startswith("o0-") for c in plan["carriers"])


@pytest.mark.parametrize("object_only,favourite", [(False, False), (True, True)])
def test_recovery_keeps_weak_people_pictures_and_owner_favourites(tmp_path, object_only, favourite):
    source = recovery_source(tmp_path, object_only=object_only, favourite=favourite)
    plan = run(source, RecoveryJudge(weak=True))
    assert "o0-p0" in {c["asset_id"] for c in plan["carriers"]}
    files = list(source.artifact_dir.rglob("story-selection.private.json"))
    assert len(files) == 1
    assert "o0-p0" in json.loads(files[0].read_text())["kept_without_standing"]
