"""A dense day earns several slots. It must not spend them on one instant.

An event period — one holding at least three times the median period's
material — is granted _EVENT_CLIPS_PER_PERIOD slots so a real event is not
reduced to a single frame. Those slots were filled by score alone, and every
one of them joined coverage_ids, which the same-moment dedup is forbidden to
touch. On a month with no favourites and dozens of pictures of one event, that
shipped three clips of the same instant.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from immich_memories.analysis.clip_distribution import _event_periods_of
from immich_memories.analysis.clip_refiner import ClipRefiner
from immich_memories.analysis.clip_scaler import ClipScaler
from immich_memories.analysis.smart_pipeline import ClipWithSegment, PipelineConfig
from immich_memories.api.models import Asset, AssetType, Person, VideoClipInfo

EVENT_DAY = datetime(2011, 8, 15, 12, 0, tzinfo=UTC)


def _clip(asset_id: str, when: datetime, score: float, *, peopled: bool = False) -> ClipWithSegment:
    asset = Asset(
        id=asset_id,
        type=AssetType.VIDEO,
        fileCreatedAt=when,
        fileModifiedAt=when,
        updatedAt=when,
        isFavorite=False,
        # An event period must show people: density alone once promoted 130
        # photos of an empty apartment over the month's real days.
        people=[Person(id="p1", name="Someone")] if peopled else [],
    )
    return ClipWithSegment(
        clip=VideoClipInfo(asset=asset, duration_seconds=5.0),
        start_time=0.0,
        end_time=5.0,
        score=score,
    )


def _pool() -> list[ClipWithSegment]:
    """One dense event day of four moments, plus ten ordinary days.

    The event's best three frames all sit inside its first moment, which is
    what a burst looks like: the same instant photographed over and over.
    """
    clips: list[ClipWithSegment] = []
    for moment in range(4):
        for frame in range(3):
            when = EVENT_DAY + timedelta(minutes=moment * 40 + frame)
            score = 0.9 if moment == 0 else 0.5
            clips.append(_clip(f"event-m{moment}-f{frame}", when, score, peopled=True))
    for day in range(10):
        clips.append(_clip(f"ordinary-{day}", EVENT_DAY - timedelta(days=day + 1), 0.4))
    return clips


def _moment_of(clip: ClipWithSegment) -> tuple:
    when = clip.clip.asset.file_created_at
    return (when.date(), when.hour, when.minute // 10)


def test_an_event_periods_slots_span_more_than_one_moment() -> None:
    """Three slots, three different moments — not three frames of one."""
    clips = _pool()
    refiner = ClipRefiner(PipelineConfig(target_clips=8), ClipScaler())

    selected = refiner._select_without_favorites(
        clips, clips, target_count=8, event_periods=_event_periods_of(clips)
    )

    reserved = [c for c in selected if c.clip.asset.id in refiner._coverage_ids]
    from_event = [c for c in reserved if c.clip.asset.file_created_at.date() == EVENT_DAY.date()]

    assert len(from_event) == 3, "the event period should still earn its three slots"
    assert len({_moment_of(c) for c in from_event}) >= 2


def test_a_period_with_fewer_moments_than_slots_still_fills_them() -> None:
    """Spreading must not shrink an event that genuinely happened in one place.

    Two moments and three slots: the third comes from the better moment's
    second-best frame rather than being given up.
    """
    from immich_memories.analysis.clip_distribution import spread_across_moments

    early = [_clip(f"early-{n}", EVENT_DAY + timedelta(minutes=n), 0.9 - n / 100) for n in range(3)]
    later = [_clip(f"later-{n}", EVENT_DAY + timedelta(minutes=40 + n), 0.5) for n in range(2)]

    picked = spread_across_moments(early + later, 3, window_minutes=10.0)

    assert len(picked) == 3
    assert len({_moment_of(c) for c in picked}) == 2


def test_the_best_moment_is_served_first() -> None:
    """Spreading changes which frames are taken, never that the best leads."""
    from immich_memories.analysis.clip_distribution import spread_across_moments

    dull = [_clip(f"dull-{n}", EVENT_DAY + timedelta(minutes=n), 0.2) for n in range(3)]
    best = [_clip(f"best-{n}", EVENT_DAY + timedelta(minutes=40 + n), 0.9) for n in range(3)]

    picked = spread_across_moments(dull + best, 2, window_minutes=10.0)

    assert picked[0].clip.asset.id == "best-0"
    assert len({_moment_of(c) for c in picked}) == 2


def test_a_single_slot_is_still_the_periods_best() -> None:
    """An ordinary period gets one clip, and spreading must not change which."""
    from immich_memories.analysis.clip_distribution import spread_across_moments

    clips = [_clip(f"c-{n}", EVENT_DAY + timedelta(minutes=n * 20), 0.1 * n) for n in range(5)]

    picked = spread_across_moments(clips, 1, window_minutes=10.0)

    assert [c.clip.asset.id for c in picked] == ["c-4"]
