"""Behavior tests for realistic, media-aware trip Auto duration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from immich_memories.api.models import Asset, AssetType, VideoClipInfo
from immich_memories.planning.auto_duration import resolve_trip_auto_duration


def _asset(asset_id: str, when: datetime, asset_type: AssetType) -> Asset:
    return Asset(
        id=asset_id,
        type=asset_type,
        fileCreatedAt=when,
        fileModifiedAt=when,
        updatedAt=when,
    )


def _clip(asset_id: str, when: datetime, duration: float = 10.0) -> VideoClipInfo:
    return VideoClipInfo(
        asset=_asset(asset_id, when, AssetType.VIDEO),
        duration_seconds=duration,
        width=1920,
        height=1080,
    )


def _dense_trip(active_days: int) -> tuple[list[VideoClipInfo], list[Asset]]:
    start = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
    clips: list[VideoClipInfo] = []
    photos: list[Asset] = []
    for day in range(active_days):
        when = start + timedelta(days=day)
        clips.extend(_clip(f"v-{day}-{index}", when) for index in range(3))
        photos.extend(_asset(f"p-{day}-{index}", when, AssetType.IMAGE) for index in range(4))
    return clips, photos


def _resolve(clips: list[VideoClipInfo], photos: list[Asset]):
    return resolve_trip_auto_duration(
        clips,
        photos,
        avg_clip_duration=5.0,
        photo_duration=4.0,
        title_duration=3.5,
        ending_duration=4.0,
    )


@pytest.mark.parametrize(
    ("active_days", "expected_seconds"),
    [(2, 60.0), (7, 100.0), (12, 150.0), (40, 300.0)],
)
def test_dense_trip_auto_duration_uses_a_bounded_active_day_curve(
    active_days: int,
    expected_seconds: float,
) -> None:
    """Regression: calendar length must not recreate the old 35-seconds/day target."""
    clips, photos = _dense_trip(active_days)

    result = _resolve(clips, photos)

    assert result.total_seconds == expected_seconds
    assert result.active_days == active_days


def test_auto_duration_shrinks_when_twelve_days_have_sparse_media() -> None:
    """Regression: Auto must not promise 150s when the diverse pool supports only 55s."""
    start = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
    photos = [_asset(f"p-{day}", start + timedelta(days=day), AssetType.IMAGE) for day in range(12)]

    result = _resolve([], photos)

    assert result.active_days == 12
    assert result.editorial_seconds == 150.0
    assert result.diverse_capacity_seconds == 55.5
    assert result.total_seconds == 55.0


def test_one_photo_burst_cannot_manufacture_a_long_memory() -> None:
    """Regression: hundreds of same-day photos count as four diverse photo moments."""
    when = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
    photos = [_asset(f"p-{index}", when, AssetType.IMAGE) for index in range(500)]

    result = _resolve([], photos)

    assert result.active_days == 1
    assert result.diverse_capacity_seconds == 23.5
    assert result.total_seconds == 20.0


def test_full_source_duration_does_not_inflate_auto_capacity() -> None:
    """Regression: a five-minute source contributes one five-second usable excerpt."""
    when = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)

    result = _resolve([_clip("long", when, duration=300.0)], [])

    assert result.diverse_capacity_seconds == 12.5
    assert result.total_seconds == 10.0


def test_empty_trip_has_zero_auto_duration() -> None:
    result = _resolve([], [])

    assert result.total_seconds == 0.0
    assert result.active_days == 0
