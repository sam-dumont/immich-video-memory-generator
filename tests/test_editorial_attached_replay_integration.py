"""Attempt replay survives cache changes through the real final observer and planner."""

from contextlib import ExitStack
from dataclasses import replace

import pytest

from immich_memories.analysis.editorial_attached_outcomes import AttachedAttemptOutcomes
from immich_memories.analysis.editorial_final_attached import FinalAttachedPictures
from immich_memories.analysis.editorial_runtime_ports import production_attached_pictures
from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
from immich_memories.analysis.editorial_structure_planner import plan_structure
from immich_memories.processing.live_material import LiveRenderMaterial, LiveSourceEntry
from tests.editorial_story_fixtures import ControlledStoryJudge
from tests.test_editorial_attached_outcomes import replay_from_output
from tests.test_editorial_attached_samples import Effects, provider
from tests.test_editorial_duration_planner_integration import source


class Forbidden:
    def fetch(self, *_args, **_kwargs):
        pytest.fail("replay fetched new video bytes")

    def extract(self, *_args, **_kwargs):
        pytest.fail("replay decoded a new frame")

    def observe_sample(self, *_args, **_kwargs):
        pytest.fail("unavailable or empty material requested a model observation")

    def bind_sample(self, *_args, **_kwargs):
        pytest.fail("unavailable or empty material invented a sample")


def live_carrier():
    material = LiveRenderMaterial((LiveSourceEntry("still", "video", 1.0, 0.0, 3.0),))
    return {
        "asset_id": "still",
        "kind": "live-motion",
        "members": list(material.still_ids),
        "video_ids": list(material.video_ids),
        "trim_points": list(material.trim_points),
        "seconds": 2.0,
        "raw_seconds": 3.0,
        "live_material": material.as_dict(),
    }


def test_final_observer_replays_original_failure_after_fresh_attempt_populates_positive_cache(
    tmp_path,
):
    failed = Effects()
    failed.fetch = lambda _key: None
    cold_outcomes = AttachedAttemptOutcomes(output=tmp_path / "cold", scope={"test": "same"})
    cold = provider(tmp_path / "cache", failed, outcomes=cold_outcomes)
    original = FinalAttachedPictures(cold, Forbidden(), Forbidden())([live_carrier()])
    reference = replay_from_output(tmp_path / "cold")
    assert original.gaps and not original.records
    assert cold.metrics()["fetch_attempts"] == 1

    fresh = provider(tmp_path / "cache", Effects())
    assert fresh.acquire("video", parent_ids=("still",), start=0.0, end=2.0) is not None
    assert fresh.metrics()["fetch_attempts"] == fresh.metrics()["frame_decodes"] == 1

    warm_outcomes = AttachedAttemptOutcomes(
        output=tmp_path / "warm", scope={"test": "same"}, replay=reference
    )
    warm = provider(tmp_path / "cache", Forbidden(), outcomes=warm_outcomes)
    replayed = FinalAttachedPictures(warm, Forbidden(), Forbidden())([live_carrier()])
    assert replayed == original
    assert warm.metrics()["fetch_attempts"] == warm.metrics()["frame_decodes"] == 0
    assert replay_from_output(tmp_path / "warm").read()["complete"] is True


def test_changed_final_interval_rejects_before_acquisition_or_picture_observation(tmp_path):
    failed = Effects()
    failed.fetch = lambda _key: None
    cold = provider(
        tmp_path / "cache",
        failed,
        outcomes=AttachedAttemptOutcomes(output=tmp_path / "cold", scope={"test": "same"}),
    )
    FinalAttachedPictures(cold, Forbidden(), Forbidden())([live_carrier()])
    warm = provider(
        tmp_path / "cache",
        Forbidden(),
        outcomes=AttachedAttemptOutcomes(
            output=tmp_path / "warm",
            scope={"test": "same"},
            replay=replay_from_output(tmp_path / "cold"),
        ),
    )
    with pytest.raises(ValueError, match="material or complete request set changed"):
        FinalAttachedPictures(warm, Forbidden(), Forbidden())([{**live_carrier(), "seconds": 1.5}])
    assert warm.metrics()["requested_samples"] == 0


def test_real_factory_and_no_live_planner_seal_and_replay_empty_material(tmp_path, monkeypatch):
    from immich_memories.api.sync_client import SyncImmichClient

    monkeypatch.setattr(
        SyncImmichClient, "__init__", lambda *_a, **_k: pytest.fail("opened a client")
    )
    captured = source(tmp_path, seconds=24, pictures=6)
    bank = {}

    def run(captured, *, warm=False):
        with ExitStack() as resources:
            observe, samples = production_attached_pictures(
                captured,
                cache_path=tmp_path / "cache.sqlite",
                pictures=Forbidden(),
                pairs=Forbidden(),
                resources=resources,
            )
            result = plan_structure(
                captured,
                StructurePlannerPorts(
                    judge=ControlledStoryJudge(bank, require_hits=warm),
                    thumbnail_hash=lambda _: None,
                    observe_attached_material=observe,
                    attached_material_metrics=samples.metrics,
                ),
            )
            assert samples.metrics()["requested_samples"] == 0
            assert (
                result.plan["attached_sample_gaps"] == result.plan["attached_picture_facts"] == {}
            )
            return result.plan

    first = run(captured)
    reference = replay_from_output(captured.artifact_dir)
    assert reference.read()["requests"] == reference.read()["outcomes"] == {}
    replay_source = replace(
        captured, artifact_dir=tmp_path / "warm", attached_outcome_replay=reference
    )
    second = run(replay_source, warm=True)
    assert second["carriers"] == first["carriers"]
    assert replay_from_output(replay_source.artifact_dir).read()["complete"] is True

    with ExitStack() as resources, pytest.raises(ValueError, match="source/config scope mismatch"):
        production_attached_pictures(
            replace(replay_source, allow_live_motion=False),
            cache_path=tmp_path / "cache.sqlite",
            pictures=Forbidden(),
            pairs=Forbidden(),
            resources=resources,
        )
