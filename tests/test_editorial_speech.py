"""Duration fitting must keep speech boundaries and the selected interval together."""

import logging
import signal
from contextlib import contextmanager

import pytest

from immich_memories.analysis.editorial_structure_record import shave_content_duration


@contextmanager
def _must_finish(seconds: float):
    """Fail the test rather than hang the suite when the code under test never returns."""

    def _ran_out(_signum, _frame):
        raise AssertionError(f"shave_content_duration did not return within {seconds}s")

    previous = signal.signal(signal.SIGALRM, _ran_out)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def test_budget_trim_moves_to_a_pause_and_keeps_the_interval_consistent():
    video = {
        "kind": "video",
        "asset_id": "talk",
        "seconds": 8.0,
        "start_time": 0.0,
        "end_time": 8.0,
        "raw_seconds": 10.0,
        "speech_regions": [[5.0, 7.5]],
    }
    shave_content_duration([video], 6.0)
    assert 3.5 <= video["seconds"] <= 5.0
    assert video["end_time"] - video["start_time"] == video["seconds"]


def test_shaving_terminates_when_speech_holds_every_cut_point():
    """One utterance that holds every cut point must retire, not spin the shave forever."""
    # The measured utterance runs from before the minimum hold to a hair inside the end:
    # closer than the timeline's microsecond, so every speech-safe shave it allows rounds
    # straight back to the duration the carrier already has.
    spoken = {
        "asset_id": "toast",
        "kind": "video",
        "seconds": 12.0,
        "start_time": 0.0,
        "end_time": 12.0,
        "raw_seconds": 12.0,
        "speech_regions": [[1.0, 12.0 - 3e-7]],
    }
    stills = [
        {"asset_id": "left", "kind": "image", "seconds": 6.0, "start_time": 0.0, "end_time": 6.0},
        {"asset_id": "right", "kind": "image", "seconds": 6.0, "start_time": 0.0, "end_time": 6.0},
    ]

    with _must_finish(20.0):
        shaved = shave_content_duration([spoken, *stills], 15.0)

    assert spoken["seconds"] == 12.0, "no cut point in the utterance may be moved"
    assert [c["seconds"] for c in stills] == [3.5, 3.5], "the shavable holds still go to minimum"
    assert shaved == 10, "only real reductions count; the held carrier never shaved"


def test_speech_spanning_a_live_stitch_join_uses_the_stitched_clock():
    from immich_memories.analysis.editorial_speech import resolve_speech_cuts
    from immich_memories.processing.live_material import LiveRenderMaterial, LiveSourceEntry

    material = LiveRenderMaterial(
        (
            LiveSourceEntry("a", "va", 0, 0, 3),
            LiveSourceEntry("b", "vb", 2, 1, 4),
        )
    )
    carrier = {
        "asset_id": "a",
        "kind": "live-motion",
        "seconds": 4.0,
        "raw_seconds": 6.0,
        "live_material": material.as_dict(),
    }
    speech = {"va": [(2.0, 3.0)], "vb": [(1.0, 3.0)]}
    resolved = resolve_speech_cuts([carrier], speech.__getitem__, buffer=0.08)[0]
    assert resolved["seconds"] == 5.08  # vb's 3s maps to 5s in the stitched movie.
    assert resolved["speech_regions"] == [[1.92, 5.08]]
    assert resolved["end_time"] == resolved["seconds"]
    assert carrier["seconds"] == 4.0


def test_fitting_drops_an_indivisible_utterance_instead_of_cutting_it():
    from immich_memories.analysis.editorial_story_planner import trim_to_timing_budget

    carriers = [
        {"asset_id": "speech", "seconds": 8, "speech_regions": [[0, 8]], "story_weight": "minor"},
        {"asset_id": "photo", "seconds": 4, "story_weight": "dominant"},
    ]
    kept, dropped = trim_to_timing_budget(carriers, lambda _: 10, 3.5)
    assert [c["asset_id"] for c in kept] == ["photo"]
    assert [c["asset_id"] for c in dropped] == ["speech"]


def test_short_video_contributes_its_actual_duration_to_minimum_fit():
    from immich_memories.analysis.editorial_story_planner import trim_to_timing_budget

    carriers = [{"asset_id": str(i), "seconds": 2.0} for i in range(3)]
    kept, dropped = trim_to_timing_budget(carriers, lambda _: 6, 3.5)
    assert kept == carriers
    assert dropped == []


def test_a_source_that_cannot_be_measured_keeps_its_interval_and_does_not_poison_others(
    caplog,
):
    """Speech protection is a refinement: one unmeasurable source degrades alone."""
    from immich_memories.analysis.editorial_speech import resolve_speech_cuts
    from immich_memories.speech.facts import SpeechMeasurementUnavailable

    def regions_for(asset_id):
        if asset_id == "broken":
            raise SpeechMeasurementUnavailable("playback unavailable")
        return [(4.0, 8.0)]

    carriers = [
        {"asset_id": "broken", "kind": "video", "seconds": 6.0, "raw_seconds": 10.0},
        {"asset_id": "fine", "kind": "video", "seconds": 6.0, "raw_seconds": 10.0},
    ]
    with caplog.at_level(logging.WARNING, logger="immich_memories.analysis.editorial_speech"):
        resolved = resolve_speech_cuts(carriers, regions_for, buffer=0.08)

    assert any("broken" in record.message for record in caplog.records), (
        "the run must say which source could not be speech-measured and why"
    )
    broken = resolved[0]
    assert broken["seconds"] == 6.0, "an unmeasurable source must keep the selected interval"
    assert "speech_regions" not in broken, "no regions were measured; none may be invented"
    fine = resolved[1]
    assert fine["speech_regions"] == [[3.92, 8.08]], (
        "one unavailable carrier must not disable protection for the others"
    )


def test_an_unmeasurable_live_motion_member_keeps_the_carrier_unchanged():
    from immich_memories.analysis.editorial_speech import resolve_speech_cuts
    from immich_memories.processing.live_material import LiveRenderMaterial, LiveSourceEntry
    from immich_memories.speech.facts import SpeechMeasurementUnavailable

    material = LiveRenderMaterial(
        (
            LiveSourceEntry("a", "va", 0, 0, 3),
            LiveSourceEntry("b", "vb", 2, 1, 4),
        )
    )
    carrier = {
        "asset_id": "a",
        "kind": "live-motion",
        "seconds": 4.0,
        "raw_seconds": 6.0,
        "live_material": material.as_dict(),
    }

    def regions_for(video_id):
        if video_id == "vb":
            raise SpeechMeasurementUnavailable("audio extraction failed")
        return [(2.0, 3.0)]

    resolved = resolve_speech_cuts([carrier], regions_for, buffer=0.08)
    assert resolved[0]["seconds"] == 4.0
    assert "speech_regions" not in resolved[0]


def test_speech_facts_transport_and_decode_failures_are_unavailable(tmp_path):
    """Playback transport and audio-extraction failures degrade, not abort."""
    import httpx

    from immich_memories.config_models_analysis import SpeechConfig
    from immich_memories.speech.facts import SpeechFacts, SpeechMeasurementUnavailable
    from tests.conftest import make_asset

    config = SpeechConfig()
    assets = {"v": make_asset("v")}

    def stub_detector():
        return type("Stub", (), {"detect": staticmethod(lambda *_a, **_k: [])})()

    def failing_fetch(asset_id):
        raise httpx.ConnectError("server down")

    facts = SpeechFacts(assets=assets, cache_dir=tmp_path, fetch=failing_fetch, config=config)
    facts.detector = stub_detector()
    with pytest.raises(SpeechMeasurementUnavailable):
        facts("v")

    facts = SpeechFacts(assets=assets, cache_dir=tmp_path, fetch=lambda _: None, config=config)
    facts.detector = stub_detector()
    with pytest.raises(SpeechMeasurementUnavailable):
        facts("v")


def test_speech_facts_still_fail_loudly_when_the_cache_identity_drifts(tmp_path, monkeypatch):
    """A tampered/mismatched cache record is an invariant break, not an unavailable source."""
    import json
    import subprocess

    from immich_memories.config_models_analysis import SpeechConfig
    from immich_memories.speech.facts import SpeechFacts
    from tests.conftest import make_asset

    config = SpeechConfig()
    assets = {"v": make_asset("v")}
    silent = tmp_path / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:d=0.3",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            "0.3",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-y",
            str(silent),
        ],
        check=True,
        capture_output=True,
    )

    facts = SpeechFacts(
        assets=assets,
        cache_dir=tmp_path,
        fetch=lambda _: silent.read_bytes(),
        config=config,
    )
    assert facts("v") == [], "the silent source measures no speech regions"

    # The same call replays the banked answer without measuring again...
    assert facts("v") == []

    # ...but a cache row whose identity no longer matches its input is refused
    # loudly by a fresh process (whose memo does not already hold the answer).
    cache_files = [p for p in tmp_path.glob("*.json") if p.name != "silent.mp4"]
    assert len(cache_files) == 1
    record = json.loads(cache_files[0].read_text())
    record["identity"]["source"] = "tampered"
    cache_files[0].write_text(json.dumps(record))
    refetched = SpeechFacts(
        assets=assets,
        cache_dir=tmp_path,
        fetch=lambda _: silent.read_bytes(),
        config=config,
    )
    with pytest.raises(ValueError, match="Speech cache source changed"):
        refetched("v")


def test_real_planner_budgets_video_lengths_and_fits_after_speech_detection(tmp_path):
    from dataclasses import replace

    from immich_memories.analysis.editorial_speech import resolve_speech_cuts
    from immich_memories.analysis.editorial_structure_planner import plan_structure
    from immich_memories.api.models import AssetType
    from immich_memories.processing.editorial_timing import build_editorial_timing_policy
    from tests.test_editorial_duration_planner_integration import source
    from tests.test_editorial_timing import _ports

    captured = source(tmp_path, seconds=60, pictures=50)
    captured = replace(
        captured,
        assets={
            key: asset.model_copy(update={"type": AssetType.VIDEO, "duration_seconds": 12.0})
            for key, asset in captured.assets.items()
        },
    )
    timing = build_editorial_timing_policy(
        config=captured.config,
        target_seconds=60,
        memory_type=captured.case.product,
        transition="cut",
    )
    calls = []

    def speech(carriers):
        calls.append([dict(c) for c in carriers])
        return resolve_speech_cuts(carriers, lambda _: [(4.0, 8.0)], buffer=0.08)

    plan = plan_structure(
        replace(captured, render_timing=timing), replace(_ports(), resolve_speech=speech)
    ).plan
    assert calls and all(c["seconds"] == 6.0 for c in calls[0])
    assert len(calls[0]) == 8  # 49.5 seconds funds eight six-second videos.
    assert sum(c["seconds"] for c in plan["carriers"]) <= 49.5
    for carrier in plan["carriers"]:
        assert carrier["end_time"] - carrier["start_time"] == carrier["seconds"]
        assert carrier["end_time"] <= 3.92 or carrier["end_time"] >= 8.08
