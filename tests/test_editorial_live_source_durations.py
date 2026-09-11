"""Actual companion bounds constrain optional Live material before certification."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from immich_memories.analysis.editorial_source_route import project_source_rendering
from immich_memories.analysis.motion_rendering import motion_renderings
from immich_memories.api.models import AssetType
from immich_memories.config_loader import Config
from immich_memories.processing.editorial_live_render import validate_editorial_live_clip
from tests.conftest import make_asset
from tests.editorial_story_fixtures import ControlledStoryJudge
from tests.test_editorial_duration_planner_integration import run, source
from tests.test_editorial_source_route import demand


@pytest.fixture(autouse=True)
def no_remote_or_media(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Source duration tests must not fetch, decode or run a model")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)


def material(times, durations, *, videos=None):
    start = datetime(2000, 1, 1, tzinfo=UTC)
    photos, companions = [], {}
    for index, (seconds, duration) in enumerate(zip(times, durations, strict=True)):
        key = videos[index] if videos else f"video-{index}"
        photo = make_asset(
            f"still-{index}", duration=None, file_created_at=start + timedelta(seconds=seconds)
        ).model_copy(update={"type": AssetType.IMAGE, "live_photo_video_id": key})
        photos.append(photo)
        companions[key] = make_asset(key).model_copy(
            update={"type": AssetType.VIDEO, "duration_seconds": duration}
        )
    return photos, companions


def carrier(rendering, *, seconds=None):
    seconds = rendering.duration_seconds if seconds is None else seconds
    return {
        "asset_id": rendering.still_ids[0],
        "kind": "live-motion",
        "members": list(rendering.still_ids),
        "video_ids": list(rendering.video_ids),
        "trim_points": [list(pair) for pair in rendering.trim_points],
        "live_material": rendering.material.as_dict(),
        "seconds": round(seconds, 2),
        "raw_seconds": round(rendering.duration_seconds, 2),
    }


def test_captured_short_pair_splits_instead_of_certifying_estimated_three_seconds():
    # Anonymous regression from two conserved sources whose old 3s support was false.
    photos, companions = material([0, 2.537], [2.368, 2.070])
    original = [photo.model_dump() for photo in photos]
    strict = motion_renderings(photos, Config(), companion_assets=companions)
    legacy = motion_renderings(photos, Config())

    assert set(strict) == {photo.id for photo in photos}
    assert [strict[photo.id].still_ids for photo in photos] == [("still-0",), ("still-1",)]
    assert [strict[photo.id].duration_seconds for photo in photos] == [2.368, 2.070]
    assert all(not result.beats_a_still for result in strict.values())
    assert legacy["still-0"].still_ids == ("still-0", "still-1")
    assert legacy["still-0"].duration_seconds == 4.5
    assert [photo.model_dump() for photo in photos] == original


def test_captured_overlapping_pair_hands_off_at_actual_end_without_a_timeline_hole():
    photos, companions = material([0, 2.032], [2.967, 2.068])
    result = motion_renderings(photos, Config(), companion_assets=companions)["still-0"]

    assert result.still_ids == ("still-0", "still-1")
    assert result.trim_points[0] == (0.0, 2.967)
    assert result.trim_points[1] == pytest.approx((0.4855, 2.068))
    assert result.duration_seconds == pytest.approx(4.5495)
    first_end = -2.967 / 2 + result.trim_points[0][1]
    next_start = 2.032 - 2.068 / 2 + result.trim_points[1][0]
    assert first_end == pytest.approx(next_start)
    assert result.beats_a_still
    assert motion_renderings(photos[::-1], Config(), companion_assets=companions) == {
        photo.id: result for photo in photos
    }


@pytest.mark.parametrize(
    "durations,gap", [([1.0, 8.0], 0.75), ([8.0, 1.0], 0.25), ([2.0, 4.0], 2.9)]
)
def test_heterogeneous_handoffs_are_bounded_and_contiguous(durations, gap):
    photos, companions = material([0, gap], durations)
    result = motion_renderings(photos, Config(), companion_assets=companions)["still-0"]
    assert len(result.video_ids) == 2
    for trim, duration in zip(result.trim_points, durations, strict=True):
        assert 0 <= trim[0] < trim[1] <= duration
    first_end = -durations[0] / 2 + result.trim_points[0][1]
    second_start = gap - durations[1] / 2 + result.trim_points[1][0]
    assert first_end == pytest.approx(second_start)


def test_touching_supports_are_separate_families():
    photos, companions = material([0, 3], [2, 4])
    result = motion_renderings(photos, Config(), companion_assets=companions)
    assert result["still-0"].video_ids == ("video-0",)
    assert result["still-1"].video_ids == ("video-1",)


@pytest.mark.parametrize("last_video", ["middle", "last"])
def test_zero_slice_preserves_alias_but_does_not_request_displayed_video(last_video):
    photos, companions = material([0, 1, 1], [3, 3, 3], videos=["first", "middle", last_video])
    result = motion_renderings(photos, Config(), companion_assets=companions)["still-0"]
    assert result.still_ids == tuple(photo.id for photo in photos)
    assert result.material.source_entries[1].start == result.material.source_entries[1].end == 1.5
    assert result.video_ids == ("first", last_video)
    assert result.duration_seconds == 4.0
    assert len(result.material.source_entries) == 3
    assert len(result.material.segments) == 2


@pytest.mark.parametrize("times", [[0, 0], [0, 0.5]])
def test_positive_repeated_companion_is_offered_once_and_other_still_remains_selectable(times):
    photos, companions = material(times, [3, 3], videos=["shared", "shared"])
    result = motion_renderings(photos, Config(), companion_assets=companions)
    assert set(result) == {"still-0"}
    rendering = result["still-0"]
    assert rendering.video_ids == ("shared",)
    assert rendering.still_ids == ("still-0",)
    assert len(rendering.material.segments) == 1
    assert 0 < rendering.duration_seconds <= companions["shared"].duration_seconds
    _, candidates = demand([*photos, *companions.values()])
    assert {row.clip.asset.id for row in candidates} == {"still-0", "still-1"}


@pytest.mark.parametrize("missing", [None, 0.0, "absent"])
def test_unavailable_companion_never_borrows_still_duration_or_removes_the_still(missing):
    photos, companions = material([0, 1], [2, 2])
    photos[1] = photos[1].model_copy(update={"duration_seconds": 100.0})
    if missing == "absent":
        del companions["video-1"]
    else:
        companions["video-1"] = companions["video-1"].model_copy(
            update={"duration_seconds": missing}
        )
    result = motion_renderings(photos, Config(), companion_assets=companions)
    assert set(result) == {"still-0"}
    _, candidates = demand([*photos, *companions.values()])
    assert {row.clip.asset.id for row in candidates} == {"still-0", "still-1"}
    assert motion_renderings(photos, Config(), companion_assets={}) == {}


@pytest.mark.parametrize("duration", [-0.01, float("nan"), float("inf"), True, False])
def test_invalid_companion_duration_fails_before_motion_is_offered(duration):
    photos, companions = material([0], [duration])
    with pytest.raises(ValueError, match="finite and positive"):
        motion_renderings(photos, Config(), companion_assets=companions)


@pytest.mark.parametrize("change", [{"id": "different-source"}, {"type": AssetType.IMAGE}])
def test_companion_identity_and_media_type_are_not_advisory(change):
    photos, companions = material([0], [2])
    companions["video-0"] = companions["video-0"].model_copy(update=change)
    with pytest.raises(ValueError, match="source link"):
        motion_renderings(photos, Config(), companion_assets=companions)


def test_actual_source_bounds_round_trip_to_certificate_and_changed_metadata_is_rejected():
    photos, companions = material([0, 2.032], [2.967, 2.068])
    config = Config()
    result = motion_renderings(photos, config, companion_assets=companions)["still-0"]
    _, candidates = demand([*photos, *companions.values()])
    declared = carrier(result)
    projected = project_source_rendering(
        [declared],
        candidates,
        config=config,
        include_live_photos=True,
        companion_assets=companions,
    )
    clip = next(row.clip for row in projected.candidates if row.clip.asset.id == "still-0")
    assert validate_editorial_live_clip(clip) == result.material
    assert projected.plan.selected_asset_ids == ("still-0",)
    assert projected.plan.selections[0].end_time == result.duration_seconds
    assert all(
        segment.end <= companions[segment.video_id].duration_seconds
        for segment in result.material.displayed_interval(0, result.duration_seconds)
    )
    changed = dict(companions)
    changed["video-1"] = changed["video-1"].model_copy(update={"duration_seconds": 2.5})
    for captured in (changed, {}):
        with pytest.raises(ValueError, match="manifest"):
            project_source_rendering(
                [declared],
                candidates,
                config=config,
                include_live_photos=True,
                companion_assets=captured,
            )


def test_real_planner_and_projector_share_the_captured_companion_map(tmp_path):
    captured = source(tmp_path, seconds=15, pictures=2)
    assets = {key: value.model_copy(deep=True) for key, value in captured.assets.items()}
    start = next(iter(assets.values())).file_created_at
    companions = {}
    for index, (asset, duration) in enumerate(zip(assets.values(), [2.967, 2.068], strict=True)):
        asset.file_created_at = start + timedelta(seconds=index * 2.032)
        asset.live_photo_video_id = f"companion-{index}"
        companions[asset.live_photo_video_id] = make_asset(asset.live_photo_video_id).model_copy(
            update={"type": AssetType.VIDEO, "duration_seconds": duration}
        )
    captured = replace(
        captured,
        assets=assets,
        companion_assets=companions,
        motion_residuals={key: {"residual": 9.0} for key in assets},
    )
    result = run(captured, ControlledStoryJudge())
    assert len(result["carriers"]) == 1
    selected = result["carriers"][0]
    expected = motion_renderings(
        list(assets.values()), captured.config, companion_assets=companions
    )
    assert selected["kind"] == "live-motion"
    assert selected["live_material"] == expected[selected["asset_id"]].material.as_dict()
    assert selected["raw_seconds"] == 4.55
    _, candidates = demand([*assets.values(), *companions.values()])
    projected = project_source_rendering(
        result["carriers"],
        candidates,
        config=captured.config,
        include_live_photos=True,
        companion_assets=companions,
    )
    assert projected.plan.selected_asset_ids == (selected["asset_id"],)
    assert projected.plan.selections[0].end_time <= 4.5495
