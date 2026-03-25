"""Tests for clip selection — pure logic, no external deps."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from immich_memories.analysis.clip_selection import (
    _allocate_day_slots,
    _clip_quality_key,
    _enforce_non_favorite_ratio,
    _select_from_day,
    smart_select_clips,
)
from tests.conftest import make_clip

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clip_on_day(
    day_offset: int,
    *,
    asset_id: str = "",
    is_favorite: bool = False,
    width: int = 1920,
    height: int = 1080,
    bitrate: int = 10_000_000,
    duration: float = 5.0,
):
    """Create a clip on a specific day (offset from 2024-01-01)."""
    base = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    ts = base + timedelta(days=day_offset)
    aid = asset_id or f"clip-day{day_offset}-{id(ts) % 10000}"
    return make_clip(
        aid,
        width=width,
        height=height,
        bitrate=bitrate,
        duration=duration,
        is_favorite=is_favorite,
        file_created_at=ts,
    )


# ---------------------------------------------------------------------------
# _clip_quality_key
# ---------------------------------------------------------------------------


class TestClipQualityKey:
    def test_higher_resolution_ranks_higher(self):
        low = make_clip("low", width=1280, height=720)
        high = make_clip("high", width=1920, height=1080)
        assert _clip_quality_key(high) > _clip_quality_key(low)

    def test_same_resolution_bitrate_breaks_tie(self):
        low_br = make_clip("lo", bitrate=5_000_000)
        hi_br = make_clip("hi", bitrate=20_000_000)
        assert _clip_quality_key(hi_br) > _clip_quality_key(low_br)

    def test_same_res_and_bitrate_duration_breaks_tie(self):
        short = make_clip("s", duration=3.0)
        long = make_clip("l", duration=10.0)
        assert _clip_quality_key(long) > _clip_quality_key(short)

    def test_zero_dimensions_produces_zero_resolution(self):
        clip = make_clip("z", width=0, height=0)
        key = _clip_quality_key(clip)
        assert key[0] == 0


# ---------------------------------------------------------------------------
# _allocate_day_slots
# ---------------------------------------------------------------------------


class TestAllocateDaySlots:
    def test_single_day_gets_all_slots(self):
        clips_by_day = {"2024-01-01": [_clip_on_day(0)] * 5}
        slots = _allocate_day_slots(clips_by_day, 3)
        assert slots["2024-01-01"] == 3

    def test_even_distribution_across_days(self):
        clips_by_day = {
            "2024-01-01": [_clip_on_day(0)] * 4,
            "2024-01-02": [_clip_on_day(1)] * 4,
        }
        slots = _allocate_day_slots(clips_by_day, 4)
        assert sum(slots.values()) == 4
        # Each day should get ~2 slots
        assert slots["2024-01-01"] == 2
        assert slots["2024-01-02"] == 2

    def test_special_day_gets_more_slots(self):
        """A day with 3x average clips is 'special' and gets up to 2x base slots."""
        clips_by_day = {
            "2024-01-01": [_clip_on_day(0)] * 2,
            "2024-01-02": [_clip_on_day(1)] * 10,  # special day
        }
        slots = _allocate_day_slots(clips_by_day, 6)
        # Day 2 has many more clips so gets extra allocation
        assert slots["2024-01-02"] > slots["2024-01-01"]

    def test_remaining_slots_distributed_to_richest_day(self):
        clips_by_day = {
            "2024-01-01": [_clip_on_day(0)] * 1,
            "2024-01-02": [_clip_on_day(1)] * 10,
        }
        slots = _allocate_day_slots(clips_by_day, 8)
        total = sum(slots.values())
        assert total == 8

    def test_more_needed_than_available(self):
        clips_by_day = {
            "2024-01-01": [_clip_on_day(0)] * 2,
        }
        slots = _allocate_day_slots(clips_by_day, 10)
        # Can't exceed available clips
        assert slots["2024-01-01"] <= 2

    def test_zero_clips_needed(self):
        clips_by_day = {
            "2024-01-01": [_clip_on_day(0)] * 5,
        }
        slots = _allocate_day_slots(clips_by_day, 0)
        assert slots["2024-01-01"] == 0


# ---------------------------------------------------------------------------
# _select_from_day
# ---------------------------------------------------------------------------


class TestSelectFromDay:
    def test_selects_highest_quality_clips(self):
        clips = [
            _clip_on_day(0, asset_id="lo", width=640, height=480),
            _clip_on_day(0, asset_id="hi", width=3840, height=2160),
            _clip_on_day(0, asset_id="mid", width=1920, height=1080),
        ]
        selected = _select_from_day(clips, slots=2, prioritize_favorites=False)
        assert len(selected) == 2
        # 4K should be first (highest quality)
        assert selected[0].asset.id == "hi"

    def test_favorites_first_when_prioritized(self):
        fav = _clip_on_day(0, asset_id="fav", is_favorite=True, width=640, height=480)
        nonfav = _clip_on_day(0, asset_id="nonfav", width=3840, height=2160)
        selected = _select_from_day([fav, nonfav], slots=1, prioritize_favorites=True)
        assert len(selected) == 1
        assert selected[0].asset.id == "fav"

    def test_fill_remaining_with_non_favorites(self):
        fav = _clip_on_day(0, asset_id="fav", is_favorite=True)
        nf1 = _clip_on_day(0, asset_id="nf1")
        nf2 = _clip_on_day(0, asset_id="nf2")
        selected = _select_from_day([fav, nf1, nf2], slots=3, prioritize_favorites=True)
        assert len(selected) == 3
        # Favorite should be first
        assert selected[0].asset.is_favorite

    def test_no_prioritization_ignores_favorites(self):
        fav = _clip_on_day(0, asset_id="fav", is_favorite=True, width=640, height=480)
        nonfav = _clip_on_day(0, asset_id="nonfav", width=3840, height=2160)
        selected = _select_from_day([fav, nonfav], slots=1, prioritize_favorites=False)
        # Higher quality non-fav should win
        assert selected[0].asset.id == "nonfav"

    def test_zero_slots_returns_empty(self):
        clips = [_clip_on_day(0)]
        assert _select_from_day(clips, slots=0, prioritize_favorites=True) == []


# ---------------------------------------------------------------------------
# _enforce_non_favorite_ratio
# ---------------------------------------------------------------------------


class TestEnforceNonFavoriteRatio:
    def test_under_limit_unchanged(self):
        clips = [
            _clip_on_day(0, asset_id="f1", is_favorite=True),
            _clip_on_day(0, asset_id="nf1"),
        ]
        result = _enforce_non_favorite_ratio(clips, max_non_favorite_ratio=0.8)
        assert len(result) == 2

    def test_over_limit_trims_non_favorites(self):
        favs = [_clip_on_day(0, asset_id=f"f{i}", is_favorite=True) for i in range(2)]
        non_favs = [_clip_on_day(0, asset_id=f"nf{i}") for i in range(8)]
        all_clips = favs + non_favs  # 10 total, 80% non-fav
        result = _enforce_non_favorite_ratio(all_clips, max_non_favorite_ratio=0.5)
        favorites_in_result = [c for c in result if c.asset.is_favorite]
        non_favorites_in_result = [c for c in result if not c.asset.is_favorite]
        # Max 50% non-favorites of 10 total = 5 non-favorites
        assert len(non_favorites_in_result) <= 5
        assert len(favorites_in_result) == 2

    def test_all_favorites_unaffected(self):
        favs = [_clip_on_day(0, asset_id=f"f{i}", is_favorite=True) for i in range(5)]
        result = _enforce_non_favorite_ratio(favs, max_non_favorite_ratio=0.3)
        assert len(result) == 5

    def test_keeps_highest_quality_non_favorites(self):
        fav = _clip_on_day(0, asset_id="f1", is_favorite=True)
        lo = _clip_on_day(0, asset_id="lo", width=640, height=480)
        hi = _clip_on_day(0, asset_id="hi", width=3840, height=2160)
        mid = _clip_on_day(0, asset_id="mid", width=1920, height=1080)
        # 4 total, max 25% non-fav = 1 non-fav
        result = _enforce_non_favorite_ratio([fav, lo, hi, mid], max_non_favorite_ratio=0.25)
        non_fav_result = [c for c in result if not c.asset.is_favorite]
        assert len(non_fav_result) == 1
        assert non_fav_result[0].asset.id == "hi"


# ---------------------------------------------------------------------------
# smart_select_clips (integration of all sub-functions)
# ---------------------------------------------------------------------------


class TestSmartSelectClips:
    def test_empty_input_returns_empty(self):
        assert smart_select_clips([], clips_needed=5) == []

    def test_selects_up_to_needed(self):
        clips = [_clip_on_day(i, asset_id=f"c{i}") for i in range(10)]
        result = smart_select_clips(clips, clips_needed=5)
        assert len(result) <= 5

    def test_result_sorted_chronologically(self):
        clips = [_clip_on_day(i, asset_id=f"c{i}") for i in range(5)]
        result = smart_select_clips(clips, clips_needed=5)
        dates = [c.asset.file_created_at for c in result]
        assert dates == sorted(dates)

    def test_hdr_filter(self):
        sdr = make_clip(
            "sdr", color_transfer="bt709", file_created_at=datetime(2024, 1, 1, tzinfo=UTC)
        )
        hdr = make_clip(
            "hdr", color_transfer="smpte2084", file_created_at=datetime(2024, 1, 2, tzinfo=UTC)
        )
        result = smart_select_clips([sdr, hdr], clips_needed=2, hdr_only=True)
        assert len(result) == 1
        assert result[0].asset.id == "hdr"

    def test_hdr_filter_empty_when_no_hdr(self):
        clips = [make_clip("sdr", file_created_at=datetime(2024, 1, 1, tzinfo=UTC))]
        result = smart_select_clips(clips, clips_needed=5, hdr_only=True)
        assert result == []

    def test_favorites_prioritized_by_default(self):
        fav = _clip_on_day(0, asset_id="fav", is_favorite=True, width=640, height=480)
        nonfav = _clip_on_day(0, asset_id="nonfav", width=3840, height=2160)
        result = smart_select_clips([fav, nonfav], clips_needed=1, prioritize_favorites=True)
        assert result[0].asset.id == "fav"

    def test_no_prioritize_favorites_uses_quality(self):
        fav = _clip_on_day(0, asset_id="fav", is_favorite=True, width=640, height=480)
        nonfav = _clip_on_day(0, asset_id="nonfav", width=3840, height=2160)
        result = smart_select_clips([fav, nonfav], clips_needed=1, prioritize_favorites=False)
        assert result[0].asset.id == "nonfav"

    def test_non_favorite_ratio_applied(self):
        """With ratio enforcement, non-favorites get trimmed."""
        # All clips on same day to avoid day-allocation complexity
        favs = [_clip_on_day(0, asset_id=f"f{i}", is_favorite=True) for i in range(3)]
        non_favs = [_clip_on_day(0, asset_id=f"nf{i}") for i in range(7)]
        result_no_limit = smart_select_clips(
            favs + non_favs,
            clips_needed=10,
            max_non_favorite_ratio=1.0,
        )
        result_limited = smart_select_clips(
            favs + non_favs,
            clips_needed=10,
            max_non_favorite_ratio=0.3,
        )
        nf_no_limit = sum(1 for c in result_no_limit if not c.asset.is_favorite)
        nf_limited = sum(1 for c in result_limited if not c.asset.is_favorite)
        # Enforcement should reduce non-favorites
        assert nf_limited <= nf_no_limit

    def test_ratio_not_enforced_when_1(self):
        """max_non_favorite_ratio=1.0 means no enforcement (default)."""
        non_favs = [_clip_on_day(i, asset_id=f"nf{i}") for i in range(5)]
        result = smart_select_clips(non_favs, clips_needed=5, max_non_favorite_ratio=1.0)
        assert len(result) == 5

    def test_multi_day_distribution(self):
        """Clips spread across multiple days should be distributed."""
        day1 = [_clip_on_day(0, asset_id=f"d1-{i}") for i in range(5)]
        day2 = [_clip_on_day(1, asset_id=f"d2-{i}") for i in range(5)]
        day3 = [_clip_on_day(2, asset_id=f"d3-{i}") for i in range(5)]
        result = smart_select_clips(day1 + day2 + day3, clips_needed=6)
        # Should have clips from multiple days
        days_represented = {c.asset.file_created_at.strftime("%Y-%m-%d") for c in result}
        assert len(days_represented) >= 2

    def test_fewer_clips_than_needed(self):
        clips = [_clip_on_day(0, asset_id="only")]
        result = smart_select_clips(clips, clips_needed=10)
        assert len(result) == 1

    def test_clips_needed_zero(self):
        clips = [_clip_on_day(0)]
        result = smart_select_clips(clips, clips_needed=0)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# analyze_clip_for_highlight
# ---------------------------------------------------------------------------


class TestAnalyzeClipForHighlight:
    def test_returns_best_segment_from_unified_analyzer(self):
        from pathlib import Path
        from unittest.mock import patch

        from immich_memories.analysis.analyzer_models import ScoredSegment
        from immich_memories.analysis.clip_selection import analyze_clip_for_highlight
        from immich_memories.config_models import AnalysisConfig, ContentAnalysisConfig

        seg = ScoredSegment(
            start_time=1.0,
            end_time=4.0,
            total_score=0.8,
            visual_score=0.7,
            audio_score=0.6,
            duration_score=0.5,
            face_score=0.8,
            motion_score=0.5,
            stability_score=0.6,
        )

        # WHY: mock at source module — import is inside function body
        with patch(
            "immich_memories.analysis.unified_analyzer.UnifiedSegmentAnalyzer.analyze",
            return_value=[seg],
        ):
            start, end, score = analyze_clip_for_highlight(
                Path("/fake.mp4"),
                content_analysis_config=ContentAnalysisConfig(),
                analysis_config=AnalysisConfig(),
            )

        assert score == 0.8
        assert start == 1.0

    def test_empty_segments_returns_fallback(self):
        from pathlib import Path
        from unittest.mock import patch

        from immich_memories.analysis.clip_selection import analyze_clip_for_highlight
        from immich_memories.config_models import AnalysisConfig, ContentAnalysisConfig

        # WHY: mock at source module — import is inside function body
        with patch(
            "immich_memories.analysis.unified_analyzer.UnifiedSegmentAnalyzer.analyze",
            return_value=[],
        ):
            start, end, score = analyze_clip_for_highlight(
                Path("/fake.mp4"),
                target_duration=5.0,
                content_analysis_config=ContentAnalysisConfig(),
                analysis_config=AnalysisConfig(),
            )

        assert start == 0.0
        assert end == 5.0
        assert score == 0.0
