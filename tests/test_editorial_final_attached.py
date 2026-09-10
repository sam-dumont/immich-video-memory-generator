"""Final attached evidence uses displayed intervals and only tightens audience decisions."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest

from immich_memories.analysis.editorial_final_attached import (
    AttachedMaterialEvidence,
    FinalAttachedPictures,
)
from immich_memories.analysis.editorial_sampled_pair_confirmation import CachedSampledPairConfirmer
from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
from immich_memories.analysis.editorial_structure_planner import plan_structure
from immich_memories.analysis.selection_same_picture import SamePicturePairDecision
from immich_memories.analysis.selection_trace import Trace
from immich_memories.processing.live_material import LiveRenderMaterial, LiveSourceEntry
from tests.conftest import make_asset
from tests.editorial_story_fixtures import ControlledStoryJudge
from tests.test_editorial_attached_samples import Effects
from tests.test_editorial_attached_samples import provider as samples_provider
from tests.test_editorial_attached_samples import source as media
from tests.test_editorial_duration_planner_integration import source
from tests.test_editorial_picture_facts import config, fake_transport
from tests.test_editorial_picture_facts import provider as pictures_provider
from tests.test_editorial_visual_body_audience import picture_record


def test_actual_final_acquisition_uses_clipped_segment_retains_zero_alias_and_replays(
    tmp_path, monkeypatch
):
    calls = fake_transport(monkeypatch)
    parents, _videos = media()
    timestamp = parents["still"].file_created_at.timestamp()
    material = LiveRenderMaterial(
        (
            LiveSourceEntry("alias", "video", timestamp, 1.5, 1.5),
            LiveSourceEntry("still", "video", timestamp, 1.5, 3.0),
        )
    )
    carrier = {
        "asset_id": "still",
        "kind": "live-motion",
        "members": list(material.still_ids),
        "video_ids": list(material.video_ids),
        "trim_points": list(material.trim_points),
        "seconds": 1.0,
        "raw_seconds": 1.5,
        "live_material": material.as_dict(),
    }
    before = deepcopy(carrier)
    effects = Effects()

    def run(effects):
        samples = samples_provider(tmp_path / "material", effects)
        pictures = pictures_provider(tmp_path)
        pairs = CachedSampledPairConfirmer(
            assets=parents,
            allowed_ids=parents,
            llm_config=config(),
            cache_path=tmp_path / "facts.sqlite",
            image_dir=tmp_path / "picture-facts-images",
            trace=Trace(),
            sheet_dir=tmp_path / "pairs",
        )
        try:
            result = FinalAttachedPictures(samples, pictures, pairs)([carrier])
            return (
                result,
                samples.metrics(),
                pairs.preview_hashes(tuple(result.records), result.records),
            )
        finally:
            pictures.close()
            pairs.close()

    first, _metrics, hashes = run(effects)
    assert carrier == before and not first.gaps
    assert len(first.records) == len(hashes) == 1
    record = next(iter(first.records.values()))
    assert record["sample_binding"]["parent_ids"] == ["alias", "still"]
    assert record["sample_binding"]["start"] == 1.5
    assert record["sample_binding"]["end"] == 2.5
    assert effects.decodes[0][1] == 2.0
    assert effects.fetches == ["video"] and len(calls) == 1

    class Forbidden:
        def fetch(self, *_args):
            pytest.fail("exact final sample replay fetched media")

        def extract(self, *_args, **_kwargs):
            pytest.fail("exact final sample replay decoded a frame")

    second, metrics, warm_hashes = run(Forbidden())
    assert second == first and warm_hashes == hashes and len(calls) == 1
    assert metrics["fetch_attempts"] == metrics["frame_decodes"] == 0


@pytest.mark.parametrize(
    "audience,uncovered_sample,bathing_sample,removed_verdict",
    [
        pytest.param("sendable", False, False, None, id="sendable-safe"),
        pytest.param("sendable", True, False, "family_only", id="sendable-body-hold"),
        pytest.param("family", False, False, None, id="family-safe"),
        pytest.param("family", True, False, None, id="family-ordinary-uncovered"),
        pytest.param("family", False, True, "do_not_show", id="family-bathing"),
        pytest.param("family", True, True, "do_not_show", id="family-bathing-body-hold"),
    ],
)
def test_actual_planner_checks_final_live_intervals_and_does_not_refill_a_sample_hold(
    tmp_path, audience, uncovered_sample, bathing_sample, removed_verdict
):
    captured = replace(source(tmp_path, seconds=24, pictures=6), audience=audience)
    assets = {}
    first_time = next(iter(captured.assets.values())).file_created_at
    for index, (key, asset) in enumerate(captured.assets.items()):
        assets[key] = asset.model_copy(
            update={
                "live_photo_video_id": f"video-{index}",
                "file_created_at": first_time
                + timedelta(seconds=(index // 2) * 600 + (index % 2) * 2),
            }
        )
    captured = replace(
        captured,
        assets=assets,
        motion_residuals={key: {"residual": 2.0} for key in assets},
        companion_assets={
            asset.live_photo_video_id: make_asset(asset.live_photo_video_id, duration=3.0)
            for asset in assets.values()
        },
    )
    inspected = []

    def inspect(carriers):
        live = [carrier for carrier in carriers if carrier["kind"] == "live-motion"]
        assert live
        assert sum(carrier["seconds"] for carrier in carriers) <= 16.5
        inspected.extend(deepcopy(live))
        records, members = {}, {}
        for index, carrier in enumerate(live):
            material = LiveRenderMaterial.from_dict(carrier["live_material"])
            interval = material.selected_interval(
                carrier["seconds"], raw_seconds=carrier["raw_seconds"]
            )
            assert material.displayed_interval(*interval)
            key = f"bound-{carrier['asset_id']}"
            members[carrier["asset_id"]] = (key,)
            records[key] = {
                **picture_record("yes" if uncovered_sample and index == 0 else "no"),
                "description": (
                    "A person is bathing in a bathtub."
                    if bathing_sample and index == 0
                    else "A clothed person moves furniture."
                ),
            }
        return AttachedMaterialEvidence(members, members, records)

    judge = ControlledStoryJudge()
    ports = StructurePlannerPorts(
        judge=judge,
        thumbnail_hash=lambda _: None,
        rank=lambda _query, documents: dict.fromkeys(range(len(documents)), 1.0),
        reranker_identity={"model": "controlled", "endpoint": "test://local"},
        observe_picture=lambda _: {
            **picture_record(),
            "description": "A clothed person moves furniture.",
        },
        confirm_sampled_pairs=lambda pairs, _records: (
            tuple(SamePicturePairDecision(*pair, False) for pair in pairs),
            {},
        ),
        sampled_preview_hashes=lambda ids, _records: dict.fromkeys(ids, "0000000000000000"),
        observe_attached_material=inspect,
    )
    plan = plan_structure(captured, ports).plan
    assert inspected
    removed = [
        row for row in plan["cut_carriers"] if row.get("review_stage") == "final-attached-audience"
    ]
    assert len(removed) == int(removed_verdict is not None)
    if removed_verdict is not None:
        assert removed[0]["asset_id"] == inspected[0]["asset_id"]
        assert removed[0]["asset_id"] not in {row["asset_id"] for row in plan["carriers"]}
        verdict = plan["shareability"]["verdicts"][removed[0]["asset_id"]]
        assert (
            verdict["verdict"] == removed_verdict and verdict["prior_verdict"]["verdict"] == "share"
        )
        if bathing_sample:
            sample_key = verdict["attached_sample_checks"][0]
            sample_verdict = plan["attached_sample_audience"][sample_key]
            assert sample_verdict["activity"]["finding"] == "bathing"
            assert sample_verdict["verdict"] == "do_not_show"
            assert any(
                call["stage"].endswith("-activity")
                and "A person is bathing in a bathtub." in call["prompt"]
                for call in judge.calls
            )
    assert len(plan["carriers"]) + len(removed) == len(inspected)
    assert all(carrier in inspected for carrier in plan["carriers"])
