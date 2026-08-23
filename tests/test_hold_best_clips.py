"""Holding a clip longer must not undo the cut its boundaries were chosen for.

Every end_time comes out of the speech-aware snap, which puts the cut inside a
pause. Nothing between there and FFmpeg re-checks it, so a raw `end += 2.0` to
cover a duration shortfall lands mid-sentence — and it does so on the
highest-scored clips, the ones whose speech is what earned them the score.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from immich_memories.analysis.clip_backfill import _hold_best_clips_longer
from immich_memories.analysis.smart_pipeline import ClipWithSegment
from immich_memories.api.models import AssetType
from tests.conftest import make_clip


def _video(
    *,
    duration: float,
    start: float,
    end: float,
    safe_gaps: list[tuple[float, float]] | None = None,
) -> ClipWithSegment:
    clip = make_clip(
        "held-001", duration=duration, file_created_at=datetime(2021, 4, 3, 10, tzinfo=UTC)
    )
    clip.safe_cut_gaps = safe_gaps
    return ClipWithSegment(clip=clip, start_time=start, end_time=end, score=0.9)


def _photo(*, duration: float, start: float, end: float) -> ClipWithSegment:
    member = _video(duration=duration, start=start, end=end)
    member.clip.asset.type = AssetType.IMAGE
    return member


def test_a_held_end_stops_before_the_speech_that_follows_it() -> None:
    """Speech runs from 10.6s; the two-second hold used to land inside it."""
    member = _video(
        duration=30.0, start=5.0, end=10.0, safe_gaps=[(0.0, 5.5), (9.4, 10.6), (14.0, 20.0)]
    )

    _hold_best_clips_longer([member], gap_seconds=4.0)

    assert member.end_time <= 10.6, "the cut was pushed into the speech after it"


def test_a_held_end_reaches_the_silence_it_can_safely_use() -> None:
    """Refusing to move at all would trade one regression for another.

    The pause the end sits in runs to 11.6s, so there is room to hold the clip
    — the cut just has to land inside that pause rather than past it.
    """
    member = _video(duration=30.0, start=5.0, end=10.0, safe_gaps=[(9.4, 11.6)])

    gained, _held = _hold_best_clips_longer([member], gap_seconds=4.0)

    assert member.end_time == pytest.approx(10.8), "moved out into the silence"
    assert gained == pytest.approx(0.8)


def test_a_video_whose_pauses_were_never_measured_is_left_where_it_is() -> None:
    """A cached segment restores the end without the evidence behind it.

    The analysis cache short-circuits the audio pass, so on a repeat run the
    boundaries come back speech-snapped with nothing left to say where the
    pauses are. An end that cannot be vouched for does not move.
    """
    member = _video(duration=30.0, start=5.0, end=10.0)

    gained, _held = _hold_best_clips_longer([member], gap_seconds=4.0)

    assert member.end_time == 10.0
    assert gained == 0.0


def test_a_photo_is_still_held_longer_because_it_has_no_audio_to_cut_through() -> None:
    """Nothing to cut through means nothing to protect, cache or no cache."""
    member = _photo(duration=8.0, start=0.0, end=4.0)

    gained, _held = _hold_best_clips_longer([member], gap_seconds=4.0)

    assert member.end_time == pytest.approx(6.0)
    assert gained == pytest.approx(2.0)


def test_the_hold_reports_how_many_clips_it_actually_moved() -> None:
    """The caller logs this count, and used to log the size of the whole cut."""
    holdable = _photo(duration=8.0, start=0.0, end=4.0)
    left_alone = _video(duration=30.0, start=5.0, end=10.0)

    gained, held = _hold_best_clips_longer([holdable, left_alone], gap_seconds=1.0)

    assert held == 1
    assert gained == pytest.approx(1.0)


def test_a_merged_live_photo_is_footage_and_answers_to_the_gap_rule() -> None:
    """A Live Photo carries a video component and is rendered from it.

    Treating it as a still exempted it from the gap check, so the hold pass
    extended a merged burst by two seconds with nothing looking at where the
    speech was — on exactly the clips the gap rule exists to protect.
    """
    member = _photo(duration=30.0, start=5.0, end=10.0)
    member.clip.asset.live_photo_video_id = "the-video-component"

    gained, _held = _hold_best_clips_longer([member], gap_seconds=4.0)

    assert member.end_time == 10.0, "footage without measured pauses does not move"
    assert gained == 0.0


def test_the_pauses_that_placed_a_cut_survive_the_cache(tmp_path) -> None:
    """A cached rerun must hold a clip exactly as the cold run did.

    The gaps were recorded on the segment and never persisted, so a second
    run restored the boundaries without the evidence behind them, refused to
    extend anything, and shipped a shorter cut than the identical fresh run.
    """
    from immich_memories.analysis.analyzer_models import ScoredSegment
    from immich_memories.analysis.cache_projection import apply_cached_segment
    from immich_memories.cache.database import VideoAnalysisCache
    from tests.conftest import make_asset

    cache = VideoAnalysisCache(tmp_path / "cache.db")
    asset = make_asset("held-cached")
    segment = ScoredSegment(start_time=5.0, end_time=10.0)
    segment.safe_cut_gaps = [(0.0, 5.5), (9.4, 11.6)]
    cache.save_analysis(asset, segments=[segment])

    restored = cache.get_analysis("held-cached")
    assert restored is not None
    clip = make_clip("held-cached", duration=30.0)
    apply_cached_segment(clip, restored.segments[0])

    assert clip.safe_cut_gaps == [(0.0, 5.5), (9.4, 11.6)]
