"""Occasion recovery may relax standing, never the carrier's context requirement."""

import json
import re
from dataclasses import replace

import pytest

from tests.test_editorial_duration_planner_integration import run
from tests.test_editorial_story_first_planner import StoryJudge, make_source


class RecoveryJudge(StoryJudge):
    def answer(self, stage, prompt):
        if stage.startswith("story-episodes"):
            result = json.loads(super().answer(stage, prompt))
            for episode in result["new_episodes"]:
                episode["role"] = "central"
            return json.dumps(result)
        if stage.startswith("story-weighing"):
            labels = re.findall(r"^(K\d+) \|", prompt, re.MULTILINE)
            return json.dumps({"about": [], "weights": dict.fromkeys(labels, "major")})
        return super().answer(stage, prompt)


# The frame head reads the object-only pictures as a lone object: the facts refuse them.
LONE_OBJECT = (("nsfw_marqo", "no"), ("frame_kind", "lone_everyday_object"), ("people", "one"))


def recovery_source(tmp_path, *, pictures=1, object_only=True, favourite=False):
    source = make_source(tmp_path, occasions=2, pictures=pictures)
    assets, annotations = dict(source.assets), dict(source.audience_annotations)
    for index in range(pictures):
        key = f"o0-p{index}"
        assets[key] = assets[key].model_copy(update={"is_favorite": favourite})
        if object_only:
            row = annotations[key]
            description = "A ceramic bowl sits alone on a wooden table."
            annotations[key] = replace(
                row, text=description, description=description, heads=LONE_OBJECT
            )
    return replace(
        source,
        assets=assets,
        audience_annotations=annotations,
        annotations={key: row.text for key, row in annotations.items()},
    )


@pytest.mark.parametrize("pictures", [1, 2])
def test_recovery_cannot_revive_a_thin_object_story_even_when_it_is_major(tmp_path, pictures):
    plan = run(recovery_source(tmp_path, pictures=pictures), RecoveryJudge())
    assert plan["carriers"]
    assert all(not c["asset_id"].startswith("o0-") for c in plan["carriers"])


@pytest.mark.parametrize("object_only,favourite", [(False, False), (True, True)])
def test_recovery_keeps_people_pictures_and_owner_favourites(tmp_path, object_only, favourite):
    source = recovery_source(tmp_path, object_only=object_only, favourite=favourite)
    plan = run(source, RecoveryJudge())
    assert "o0-p0" in {c["asset_id"] for c in plan["carriers"]}


def test_recovery_does_not_force_a_rejected_motion_clip_back_into_the_film(tmp_path):
    from immich_memories.api.models import AssetType

    source = recovery_source(tmp_path, pictures=1)
    assets = dict(source.assets)
    assets["o0-p0"] = assets["o0-p0"].model_copy(
        update={"type": AssetType.VIDEO, "duration_seconds": 7.0}
    )
    source = replace(source, assets=assets)

    plan = run(source, RecoveryJudge())

    assert plan["carriers"]
    assert "o0-p0" not in {carrier["asset_id"] for carrier in plan["carriers"]}


def test_approved_scenery_video_remains_selectable_without_people(tmp_path):
    from immich_memories.api.models import AssetType

    source = recovery_source(tmp_path, pictures=1)
    assets = dict(source.assets)
    assets["o0-p0"] = assets["o0-p0"].model_copy(
        update={"type": AssetType.VIDEO, "duration_seconds": 7.0}
    )
    annotations = dict(source.audience_annotations)
    description = "A waterfall pours into a rocky pool."
    annotations["o0-p0"] = replace(
        annotations["o0-p0"],
        text=description,
        description=description,
        heads=(("nsfw_marqo", "no"), ("frame_kind", "place_or_scenery")),
    )
    source = replace(
        source,
        assets=assets,
        audience_annotations=annotations,
        annotations={key: row.text for key, row in annotations.items()},
    )

    plan = run(source, RecoveryJudge())

    assert any(c["asset_id"] == "o0-p0" and c["kind"] == "video" for c in plan["carriers"])
