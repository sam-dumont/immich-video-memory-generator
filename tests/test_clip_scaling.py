"""Tests for clip scaling and temporal deduplication — pure logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from immich_memories.analysis.clip_scaling import (
    _resolve_cluster,
    deduplicate_temporal_clusters,
    scale_to_target_duration,
)
from immich_memories.analysis.smart_pipeline import ClipWithSegment
from tests.conftest import make_clip

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cws(
    *,
    score: float = 0.5,
    start: float = 0.0,
    end: float = 5.0,
    is_favorite: bool = False,
    day_offset: int = 0,
    asset_id: str = "",
) -> ClipWithSegment:
    """Build a ClipWithSegment for testing."""
    base = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    ts = base + timedelta(days=day_offset)
    aid = asset_id or f"clip-{day_offset}-{score}"
    clip = make_clip(
        aid,
        is_favorite=is_favorite,
        file_created_at=ts,
        duration=end,
    )
    return ClipWithSegment(clip=clip, start_time=start, end_time=end, score=score)


# ---------------------------------------------------------------------------
# scale_to_target_duration
# ---------------------------------------------------------------------------


class TestScaleToTargetDuration:
    def test_empty_list_returns_empty(self):
        assert scale_to_target_duration([], 60.0) == []

    def test_under_budget_returns_unchanged(self):
        clips = [_cws(end=5.0), _cws(end=5.0, day_offset=1)]
        result = scale_to_target_duration(clips, 60.0)
        assert len(result) == 2

    def test_exactly_at_budget_returns_unchanged(self):
        clips = [_cws(end=30.0, day_offset=i) for i in range(2)]
        # total = 60s, target = 60s, within 10%
        result = scale_to_target_duration(clips, 60.0)
        assert len(result) == 2

    def test_at_110_percent_still_within_allowance(self):
        # 66s total vs 60s target => 110% exactly => should keep all
        clips = [_cws(end=33.0, day_offset=i) for i in range(2)]
        result = scale_to_target_duration(clips, 60.0)
        assert len(result) == 2

    def test_over_budget_removes_clips(self):
        # 4 clips x 20s = 80s total, target = 30s (max_allowed = 33s)
        clips = [
            _cws(end=20.0, score=0.9, day_offset=0),
            _cws(end=20.0, score=0.1, day_offset=1),
            _cws(end=20.0, score=0.5, day_offset=2),
            _cws(end=20.0, score=0.8, day_offset=3),
        ]
        result = scale_to_target_duration(clips, 30.0)
        assert len(result) < 4
        total = sum(c.end_time - c.start_time for c in result)
        assert total <= 33.0  # max_allowed = 30 * 1.1

    def test_non_favorites_removed_before_favorites(self):
        clips = [
            _cws(end=10.0, score=0.9, is_favorite=False, day_offset=0),
            _cws(end=10.0, score=0.1, is_favorite=True, day_offset=1),
        ]
        # total=20s, target=10s (max_allowed=11.0) — room for one 10s clip
        # Algorithm keeps least-removable first: fav wins even with lower score
        result = scale_to_target_duration(clips, 10.0)
        assert len(result) == 1
        assert result[0].clip.asset.is_favorite

    def test_result_sorted_chronologically(self):
        # Over budget so removal triggers, then result is sorted by date
        clips = [
            _cws(end=10.0, score=0.1, day_offset=3),
            _cws(end=10.0, score=0.9, day_offset=0),
            _cws(end=10.0, score=0.5, day_offset=1),
            _cws(end=10.0, score=0.8, day_offset=2),
        ]
        # total=40s, target=25s (max_allowed=27.5) — room for ~2 clips
        result = scale_to_target_duration(clips, 25.0)
        assert len(result) < 4
        dates = [c.clip.asset.file_created_at for c in result]
        assert dates == sorted(dates)


# ---------------------------------------------------------------------------
# _resolve_cluster
# ---------------------------------------------------------------------------


class TestResolveCluster:
    def test_single_clip_returned_as_is(self):
        c = _cws(score=0.8)
        best, removed = _resolve_cluster("2024-06-01_5", [c])
        assert best is c
        assert removed == 0

    def test_favorites_preferred_over_non_favorites(self):
        fav = _cws(score=0.3, is_favorite=True, asset_id="fav")
        nonfav = _cws(score=0.9, is_favorite=False, asset_id="nonfav")
        best, removed = _resolve_cluster("key", [fav, nonfav])
        assert best.clip.asset.is_favorite
        assert removed == 1

    def test_highest_score_favorite_wins(self):
        fav_low = _cws(score=0.3, is_favorite=True, asset_id="fav-lo")
        fav_high = _cws(score=0.9, is_favorite=True, asset_id="fav-hi")
        nonfav = _cws(score=0.5, is_favorite=False, asset_id="nonfav")
        best, removed = _resolve_cluster("key", [fav_low, fav_high, nonfav])
        assert best.score == 0.9
        assert removed == 2

    def test_no_favorites_keeps_highest_score(self):
        low = _cws(score=0.2, asset_id="low")
        high = _cws(score=0.8, asset_id="high")
        best, removed = _resolve_cluster("key", [low, high])
        assert best.score == 0.8
        assert removed == 1


# ---------------------------------------------------------------------------
# deduplicate_temporal_clusters
# ---------------------------------------------------------------------------


class TestDeduplicateTemporalClusters:
    def test_empty_returns_empty(self):
        assert deduplicate_temporal_clusters([]) == []

    def test_no_duplicates_returns_all(self):
        # Clips on different days — never in the same time bucket
        clips = [_cws(day_offset=i, score=float(i) / 10) for i in range(3)]
        result = deduplicate_temporal_clusters(clips, time_window_minutes=10.0)
        assert len(result) == 3

    def test_same_moment_keeps_best(self):
        base = datetime(2024, 6, 1, 14, 0, 0, tzinfo=UTC)
        c1 = ClipWithSegment(
            clip=make_clip("a", file_created_at=base, is_favorite=True),
            start_time=0,
            end_time=5,
            score=0.3,
        )
        c2 = ClipWithSegment(
            clip=make_clip("b", file_created_at=base + timedelta(minutes=2), is_favorite=True),
            start_time=0,
            end_time=5,
            score=0.9,
        )
        result = deduplicate_temporal_clusters([c1, c2], time_window_minutes=10.0)
        assert len(result) == 1
        assert result[0].score == 0.9

    def test_result_sorted_chronologically(self):
        base = datetime(2024, 6, 1, 14, 0, 0, tzinfo=UTC)
        clips = [
            ClipWithSegment(
                clip=make_clip(f"c{i}", file_created_at=base + timedelta(days=2 - i)),
                start_time=0,
                end_time=5,
                score=0.5,
            )
            for i in range(3)
        ]
        result = deduplicate_temporal_clusters(clips, time_window_minutes=10.0)
        dates = [c.clip.asset.file_created_at for c in result]
        assert dates == sorted(dates)
