"""Runtime shortfalls should not be filled with photos before anything else."""

from __future__ import annotations

from immich_memories.analysis.clip_refiner import (
    _BackfillContext,
    _resolve_backfill_candidates,
)
from immich_memories.analysis.smart_pipeline import ClipWithSegment, PipelineConfig
from immich_memories.api.models import AssetType, VideoClipInfo
from tests.conftest import make_asset


def _clip(asset_id: str, *, is_photo: bool, favorite: bool) -> ClipWithSegment:
    asset = make_asset(asset_id, is_favorite=favorite)
    if is_photo:
        asset.type = AssetType.IMAGE
    return ClipWithSegment(
        clip=VideoClipInfo(asset=asset, duration_seconds=4.0),
        start_time=0.0,
        end_time=4.0,
        score=0.5,
    )


def _context(**overrides) -> _BackfillContext:
    defaults = {
        "config": PipelineConfig(),
        "selected_count": 8,
        "photo_count": 4,
        "non_favorite_count": 6,
        "temporal_window": 0.0,
        "occupied_moments": [],
    }
    defaults.update(overrides)
    return _BackfillContext(**defaults)


def test_a_non_favorite_video_is_preferred_over_relaxing_the_photo_ratio() -> None:
    """The ladder used to spend the photo ratio first, so a shortage of runtime
    was answered with more stills — a real June video ended at 7 photos of 13
    while the cap had just been computed as 4. Photos are the last thing to give."""
    available = [
        _clip("photo-1", is_photo=True, favorite=True),
        _clip("video-1", is_photo=False, favorite=False),
    ]

    # Strict fails for both: the photo would exceed a 50% cap (it is a favorite,
    # so nothing else blocks it), and the video would exceed the non-favorite
    # ratio. Relaxing either constraint unblocks exactly one, so the order the
    # ladder tries them in decides what ends up on screen.
    config = PipelineConfig()
    config.prioritize_favorites = True
    config.max_non_favorite_ratio = 0.7
    config.target_clips = 8

    resolved = _resolve_backfill_candidates(
        available,
        context=_context(config=config, selected_count=8, photo_count=4, non_favorite_count=6),
        active_photo_limit=0.50,
        remaining_budget=8.0,
    )

    assert resolved.tier != "photo_ratio_70"
    assert [c.clip.asset.id for c in resolved.items] == ["video-1"]


def test_a_repeated_moment_is_conceded_after_extra_photos() -> None:
    """Order of concessions, worst-last (#489).

    A real August filled a 10.9s gap by relaxing temporal spacing and shipped
    four near-identical videos of the same field, two of them four minutes
    apart. A bounded number of extra stills reads better than the same shot
    twice, so the photo ratio is now spent first.
    """
    from datetime import UTC, datetime

    from immich_memories.analysis.clip_refiner import (
        _BackfillContext,
        _resolve_backfill_candidates,
    )
    from immich_memories.analysis.smart_pipeline import ClipWithSegment, PipelineConfig
    from immich_memories.api.models import AssetType
    from tests.conftest import make_clip

    taken = datetime(2016, 8, 15, 5, 32, tzinfo=UTC)

    def member(asset_id: str, when: datetime, is_photo: bool) -> ClipWithSegment:
        clip = make_clip(asset_id, duration=6.0, file_created_at=when)
        if is_photo:
            clip.asset.type = AssetType.IMAGE
        return ClipWithSegment(clip=clip, start_time=0.0, end_time=4.0, score=0.5)

    same_moment_video = member("v-again", taken.replace(minute=36), is_photo=False)
    another_photo = member("p-elsewhere", datetime(2016, 8, 20, 12, 0, tzinfo=UTC), is_photo=True)

    context = _BackfillContext(
        config=PipelineConfig(target_clips=10, avg_clip_duration=5.0),
        selected_count=4,
        photo_count=2,
        non_favorite_count=4,
        temporal_window=5.0,
        occupied_moments=[taken],
    )

    resolved = _resolve_backfill_candidates(
        [same_moment_video, another_photo],
        context=context,
        active_photo_limit=0.5,
        remaining_budget=10.0,
    )

    assert resolved.tier != "temporal_spacing", "a repeat is the last thing to concede"
    assert any(c.clip.asset.id == "p-elsewhere" for c in resolved.items)
