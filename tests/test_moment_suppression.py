"""Photos that a video already covers should not also appear as stills."""

from __future__ import annotations

from datetime import datetime, timedelta

from immich_memories.photos.moment_suppression import (
    MomentAsset,
    suppress_photos_covered_by_motion,
)
from tests.conftest import make_asset

BASE = datetime(2026, 7, 4, 15, 0, 0)


def photo(asset_id: str, offset: float = 0.0, thumbnail_hash: str | None = None) -> MomentAsset:
    return MomentAsset(
        asset_id=asset_id,
        taken_at=BASE + timedelta(seconds=offset),
        thumbnail_hash=thumbnail_hash,
    )


def test_photo_already_represented_by_a_motion_clip_is_dropped() -> None:
    """A live photo still shares its asset ID with the clip built from it."""
    result = suppress_photos_covered_by_motion(
        photos=[photo("live-still"), photo("unrelated", offset=9000)],
        motion=[photo("live-still")],
        gap_seconds=120.0,
        hash_threshold=10,
    )

    assert result.kept_ids == ["unrelated"]
    assert result.identity_drops == 1


def test_burst_stills_behind_a_merged_clip_are_dropped() -> None:
    """A merged burst plays every shutter press, but only the first asset IDs it."""
    burst_clip = MomentAsset(
        asset_id="shot-1",
        taken_at=BASE,
        covered_asset_ids=("shot-2", "shot-3"),
    )

    result = suppress_photos_covered_by_motion(
        photos=[photo("shot-1"), photo("shot-2", 1.4), photo("shot-3", 2.9)],
        motion=[burst_clip],
        gap_seconds=120.0,
        hash_threshold=10,
    )

    assert result.kept_ids == []
    assert result.identity_drops == 3


SCENE = "ff00ff00ff00ff00"
SCENE_SHIFTED = "ff00ff00ff00ff03"  # 2 bits from SCENE — same framing, later frame
OTHER_SCENE = "00ff00ff00ff00ff"  # 64 bits from SCENE


def test_photo_of_the_same_scene_as_a_nearby_video_is_dropped() -> None:
    result = suppress_photos_covered_by_motion(
        photos=[photo("still", offset=20, thumbnail_hash=SCENE_SHIFTED)],
        motion=[photo("clip", offset=0, thumbnail_hash=SCENE)],
        gap_seconds=120.0,
        hash_threshold=10,
    )

    assert result.kept_ids == []
    assert result.similarity_drops == 1


def test_same_scene_on_a_different_day_survives() -> None:
    """The same room filmed twice is two memories, not one duplicate."""
    result = suppress_photos_covered_by_motion(
        photos=[photo("still", offset=86400, thumbnail_hash=SCENE)],
        motion=[photo("clip", offset=0, thumbnail_hash=SCENE)],
        gap_seconds=120.0,
        hash_threshold=10,
    )

    assert result.kept_ids == ["still"]


def test_different_scene_in_the_same_minute_survives() -> None:
    """Filming the kids then shooting the sunset is two subjects, not one."""
    result = suppress_photos_covered_by_motion(
        photos=[photo("sunset", offset=15, thumbnail_hash=OTHER_SCENE)],
        motion=[photo("clip", offset=0, thumbnail_hash=SCENE)],
        gap_seconds=120.0,
        hash_threshold=10,
    )

    assert result.kept_ids == ["sunset"]


def test_photo_without_a_thumbnail_hash_survives() -> None:
    """No hash means no evidence of redundancy — keeping it is the safe default."""
    result = suppress_photos_covered_by_motion(
        photos=[photo("unhashed", offset=5)],
        motion=[photo("clip", offset=0, thumbnail_hash=SCENE)],
        gap_seconds=120.0,
        hash_threshold=10,
    )

    assert result.kept_ids == ["unhashed"]


def test_photos_similar_to_each_other_but_not_to_a_clip_all_survive() -> None:
    """Photo-vs-photo redundancy is deduplication's job, not this filter's."""
    result = suppress_photos_covered_by_motion(
        photos=[
            photo("a", offset=0, thumbnail_hash=SCENE),
            photo("b", offset=3, thumbnail_hash=SCENE_SHIFTED),
        ],
        motion=[photo("clip", offset=1, thumbnail_hash=OTHER_SCENE)],
        gap_seconds=120.0,
        hash_threshold=10,
    )

    assert result.kept_ids == ["a", "b"]


def test_no_motion_clips_keeps_every_photo_in_order() -> None:
    result = suppress_photos_covered_by_motion(
        photos=[photo("a", 0, SCENE), photo("b", 5, SCENE), photo("c", 10, SCENE)],
        motion=[],
        gap_seconds=120.0,
        hash_threshold=10,
    )

    assert result.kept_ids == ["a", "b", "c"]


def test_thumbnails_are_only_resolved_for_photos_near_a_clip() -> None:
    """Fetching a thumbnail costs an HTTP round trip; a photo hours from any
    video can never be suppressed, so it must never be fetched."""
    asked: list[str] = []

    def resolve(asset_id: str) -> str | None:
        asked.append(asset_id)
        return SCENE

    result = suppress_photos_covered_by_motion(
        photos=[photo("near", offset=30), photo("far", offset=9000)],
        motion=[photo("clip", offset=0)],
        gap_seconds=120.0,
        hash_threshold=10,
        resolve_hash=resolve,
    )

    assert "far" not in asked
    assert result.kept_ids == ["far"]


def _asset(asset_id: str, offset: float = 0.0):
    return make_asset(
        asset_id,
        file_created_at=BASE + timedelta(seconds=offset),
        original_file_name=f"{asset_id}.heic",
    )


def test_live_photo_stills_are_filtered_out_of_the_photo_pool() -> None:
    """A Live Photo's still and the clip built from it are one asset. Immich
    normally keeps such stills out of the photo pool, but if one gets through it
    must not be animated on top of the motion clip already showing it."""
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.config_models_render import PhotoConfig
    from immich_memories.photos.moment_suppression import filter_photos_covered_by_motion

    still = _asset("live-still")
    burst_sibling = _asset("burst-2", offset=1.5)
    unrelated = _asset("holiday", offset=50000)

    clip = VideoClipInfo(
        asset=still,
        duration_seconds=3.0,
        live_burst_still_ids=["live-still", "burst-2"],
    )

    kept = filter_photos_covered_by_motion(
        [still, burst_sibling, unrelated],
        [clip],
        config=PhotoConfig(),
    )

    assert [a.id for a in kept] == ["holiday"]
