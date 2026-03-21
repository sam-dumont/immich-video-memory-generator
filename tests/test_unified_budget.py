"""Tests for unified photo+video budget selection."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from immich_memories.analysis.unified_budget import (
    BudgetCandidate,
    estimate_title_overhead,
    select_within_budget,
)
from immich_memories.api.models import Asset
from immich_memories.config_models import PhotoConfig
from immich_memories.photos.photo_pipeline import score_photos
from immich_memories.processing.assembly_config import TitleScreenSettings


def _make_title_settings(**overrides) -> TitleScreenSettings:
    """Factory for TitleScreenSettings with sane defaults."""
    defaults = {
        "enabled": True,
        "title_duration": 3.5,
        "month_divider_duration": 2.0,
        "ending_duration": 7.0,
        "show_month_dividers": False,
        "divider_mode": "none",
        "month_divider_threshold": 2,
    }
    defaults.update(overrides)
    return TitleScreenSettings(**defaults)


class TestEstimateTitleOverhead:
    """Title overhead estimation with memory-type-aware caps."""

    def test_no_title_settings_returns_zero(self):
        overhead = estimate_title_overhead(
            clip_dates=["2025-07-01", "2025-07-15"],
            title_settings=None,
            target_duration=300.0,
        )
        assert overhead == 0.0

    def test_basic_overhead_title_plus_ending(self):
        settings = _make_title_settings()
        overhead = estimate_title_overhead(
            clip_dates=["2025-07-01", "2025-07-15"],
            title_settings=settings,
            target_duration=300.0,
        )
        # 3.5 (title) + 7.0 (ending) = 10.5
        assert overhead == 10.5

    def test_crossfade_compensation_reduces_overhead(self):
        """Crossfade transitions compress the timeline — compensate."""
        settings = _make_title_settings()
        # 12 clips → ~13 transitions (title+clips+ending) × 0.5s = 6.5s saved
        overhead = estimate_title_overhead(
            clip_dates=["2025-07-01"] * 12,
            title_settings=settings,
            target_duration=60.0,
            num_clips=12,
            transition_duration=0.5,
        )
        # Raw = 10.5, crossfade savings = 13 * 0.5 * 0.5 = 3.25
        # Net = 10.5 - 3.25 = 7.25
        assert overhead < 10.5
        assert overhead > 0

    def test_overhead_with_month_dividers(self):
        settings = _make_title_settings(show_month_dividers=True, divider_mode="month")
        # 3 clips in July, 3 in August, 1 in September (below threshold=2)
        dates = [
            "2025-07-01",
            "2025-07-10",
            "2025-07-20",
            "2025-08-01",
            "2025-08-15",
            "2025-08-25",
            "2025-09-05",
        ]
        overhead = estimate_title_overhead(
            clip_dates=dates,
            title_settings=settings,
            target_duration=300.0,
        )
        # 3.5 + 7.0 + 2 dividers (July, August meet threshold=2; Sep doesn't) × 2.0
        assert overhead == 3.5 + 7.0 + 2 * 2.0  # 14.5

    def test_overhead_capped_at_20_percent(self):
        """Regular memories cap overhead at 20% of target."""
        settings = _make_title_settings(
            show_month_dividers=True,
            divider_mode="month",
            month_divider_threshold=1,
        )
        # 12 months × 2.0 = 24 dividers + 10.5 base = 34.5
        # But 20% of 60 = 12.0, so capped
        dates = [f"2025-{m:02d}-15" for m in range(1, 13)]
        overhead = estimate_title_overhead(
            clip_dates=dates,
            title_settings=settings,
            target_duration=60.0,
        )
        assert overhead == 60.0 * 0.20  # 12.0

    def test_trip_overhead_capped_at_30_percent(self):
        """Trip memories allow up to 30% for location cards."""
        settings = _make_title_settings(
            show_month_dividers=True,
            divider_mode="month",
            month_divider_threshold=1,
        )
        # Same 12-month setup, raw overhead 34.5
        # Trip cap: 30% of 300 = 90 → raw 34.5 < 90, so use raw
        dates = [f"2025-{m:02d}-15" for m in range(1, 13)]
        overhead = estimate_title_overhead(
            clip_dates=dates,
            title_settings=settings,
            target_duration=300.0,
            memory_type="trip",
        )
        assert overhead == 3.5 + 7.0 + 12 * 2.0  # 34.5 (uncapped, fits in 30%)

    def test_trip_overhead_floor_10_percent(self):
        """Trip always reserves at least 10% for title narrative."""
        settings = _make_title_settings()  # No dividers → raw = 10.5
        overhead = estimate_title_overhead(
            clip_dates=["2025-07-01"],
            title_settings=settings,
            target_duration=300.0,
            memory_type="trip",
        )
        # Raw = 10.5, but floor = 30.0 (10% of 300) → use floor
        assert overhead == 300.0 * 0.10  # 30.0

    def test_trip_10_stops_within_cap(self):
        """A 10-stop trip overhead fits under 30% of a 5-min video."""
        settings = _make_title_settings(
            show_month_dividers=True,
            divider_mode="month",
            month_divider_threshold=1,
        )
        # 10-stop trip: locations change across 10 different days in 3 months
        # Dividers from months: 3 months × 2.0 = 6.0
        # Total raw: 3.5 + 7.0 + 6.0 = 16.5
        # 30% of 300 = 90, so 16.5 fits easily
        dates = [
            "2025-07-01",
            "2025-07-05",
            "2025-07-10",
            "2025-08-01",
            "2025-08-05",
            "2025-08-10",
            "2025-09-01",
            "2025-09-05",
            "2025-09-10",
            "2025-09-15",
        ]
        overhead = estimate_title_overhead(
            clip_dates=dates,
            title_settings=settings,
            target_duration=300.0,
            memory_type="trip",
        )
        # Raw = 16.5, floor = 30.0 → floor wins
        assert overhead == 300.0 * 0.10  # 30.0


def _make_video(
    asset_id: str,
    duration: float,
    score: float,
    date: datetime | None = None,
    is_favorite: bool = False,
) -> BudgetCandidate:
    return BudgetCandidate(
        asset_id=asset_id,
        duration=duration,
        score=score,
        candidate_type="video",
        date=date or datetime(2025, 7, 15, tzinfo=UTC),
        is_favorite=is_favorite,
    )


def _make_photo(
    asset_id: str,
    duration: float,
    score: float,
    date: datetime | None = None,
    is_favorite: bool = False,
) -> BudgetCandidate:
    return BudgetCandidate(
        asset_id=asset_id,
        duration=duration,
        score=score,
        candidate_type="photo",
        date=date or datetime(2025, 7, 15, tzinfo=UTC),
        is_favorite=is_favorite,
    )


class TestSelectWithinBudgetVideosOnly:
    """Budget selection with only videos."""

    def test_videos_under_budget_all_kept(self):
        videos = [
            _make_video("v1", 5.0, 0.8),
            _make_video("v2", 5.0, 0.6),
            _make_video("v3", 5.0, 0.7),
        ]
        result = select_within_budget(videos, [], content_budget=30.0)
        assert result.kept_video_ids == {"v1", "v2", "v3"}
        assert result.selected_photo_ids == []
        assert result.content_duration <= 30.0

    def test_videos_over_budget_drops_lowest_scored(self):
        videos = [
            _make_video("v1", 10.0, 0.9),
            _make_video("v2", 10.0, 0.3),
            _make_video("v3", 10.0, 0.7),
        ]
        # Budget 20s, have 30s → must drop 10s
        result = select_within_budget(videos, [], content_budget=20.0)
        assert "v2" not in result.kept_video_ids  # Lowest scored dropped
        assert "v1" in result.kept_video_ids
        assert result.content_duration <= 20.0

    def test_protected_temporal_sole_representative(self):
        """Only clip in a month survives trim even if lowest scored."""
        videos = [
            _make_video("jul1", 10.0, 0.9, date=datetime(2025, 7, 1, tzinfo=UTC)),
            _make_video("jul2", 10.0, 0.7, date=datetime(2025, 7, 15, tzinfo=UTC)),
            _make_video("aug1", 10.0, 0.3, date=datetime(2025, 8, 1, tzinfo=UTC)),
        ]
        # Budget 20s → must drop one. aug1 is sole August representative → protected
        result = select_within_budget(videos, [], content_budget=20.0)
        assert "aug1" in result.kept_video_ids  # Protected: sole in August
        assert "jul1" in result.kept_video_ids  # Highest score
        assert "jul2" not in result.kept_video_ids  # July has 2 clips, this one dropped


class TestSelectWithinBudgetMixed:
    """Budget selection with videos and photos competing."""

    def test_photos_fill_remaining_budget(self):
        videos = [_make_video("v1", 10.0, 0.8)]
        photos = [
            _make_photo("p1", 4.0, 0.6, date=datetime(2025, 7, 20, tzinfo=UTC)),
            _make_photo("p2", 4.0, 0.5, date=datetime(2025, 7, 25, tzinfo=UTC)),
        ]
        # Budget 18s, video uses 10s, 8s left → both photos fit (ratio uncapped)
        result = select_within_budget(videos, photos, content_budget=18.0, max_photo_ratio=1.0)
        assert result.kept_video_ids == {"v1"}
        assert set(result.selected_photo_ids) == {"p1", "p2"}
        assert result.content_duration <= 18.0

    def test_high_scoring_photo_beats_low_video(self):
        """A high-scoring photo displaces a low-scoring video when over budget."""
        videos = [
            _make_video("v1", 10.0, 0.9, date=datetime(2025, 7, 1, tzinfo=UTC)),
            _make_video("v_weak", 10.0, 0.3, date=datetime(2025, 7, 10, tzinfo=UTC)),
        ]
        photos = [
            _make_photo("p_strong", 4.0, 0.7, date=datetime(2025, 7, 15, tzinfo=UTC)),
        ]
        # Budget 15s: can't fit both videos (20s). Must drop v_weak (0.3).
        # Then p_strong (0.7) fits in remaining 5s.
        result = select_within_budget(videos, photos, content_budget=15.0)
        assert "v1" in result.kept_video_ids
        assert "v_weak" not in result.kept_video_ids
        assert "p_strong" in result.selected_photo_ids

    def test_max_photo_ratio_enforced(self):
        """Photos capped at max_photo_ratio of total selected items."""
        videos = [_make_video("v1", 5.0, 0.6)]
        photos = [
            _make_photo(f"p{i}", 4.0, 0.5 + i * 0.01, date=datetime(2025, 7, i + 1, tzinfo=UTC))
            for i in range(10)
        ]
        # Budget 50s: 1 video (5s) + up to ~11 photos (44s) would fit
        # But max_photo_ratio=0.25 → photos can be at most 25% of total count
        result = select_within_budget(videos, photos, content_budget=50.0, max_photo_ratio=0.25)
        total = len(result.kept_video_ids) + len(result.selected_photo_ids)
        photo_ratio = len(result.selected_photo_ids) / total if total else 0
        assert photo_ratio <= 0.25 + 0.01  # Allow rounding

    def test_temporal_distribution_across_months(self):
        """Selected items spread across date range, not clustered."""
        videos = [
            _make_video("v_jul", 5.0, 0.8, date=datetime(2025, 7, 15, tzinfo=UTC)),
            _make_video("v_aug", 5.0, 0.7, date=datetime(2025, 8, 15, tzinfo=UTC)),
        ]
        photos = [
            _make_photo("p_jul", 4.0, 0.6, date=datetime(2025, 7, 20, tzinfo=UTC)),
            _make_photo("p_sep", 4.0, 0.5, date=datetime(2025, 9, 10, tzinfo=UTC)),
        ]
        # Budget 18s: can fit 2 videos (10s) + 2 photos (8s) = 18s exactly
        result = select_within_budget(videos, photos, content_budget=18.0)
        all_ids = result.kept_video_ids | set(result.selected_photo_ids)
        # September should be represented via p_sep
        assert "p_sep" in all_ids


class TestSelectWithinBudgetAdaptive:
    """Edge cases testing adaptive behavior."""

    def test_month_with_only_photos_represented(self):
        """A month with no videos but good photos still gets representation."""
        videos = [
            _make_video("v_jul", 10.0, 0.8, date=datetime(2025, 7, 15, tzinfo=UTC)),
        ]
        photos = [
            _make_photo("p_aug", 4.0, 0.6, date=datetime(2025, 8, 10, tzinfo=UTC)),
        ]
        result = select_within_budget(videos, photos, content_budget=20.0, max_photo_ratio=1.0)
        # August only has a photo — it should be selected
        assert "p_aug" in result.selected_photo_ids

    def test_no_photos_returns_all_videos(self):
        """When there are no photos, all fitting videos are kept."""
        videos = [
            _make_video("v1", 5.0, 0.8),
            _make_video("v2", 5.0, 0.6),
        ]
        result = select_within_budget(videos, [], content_budget=20.0)
        assert result.kept_video_ids == {"v1", "v2"}
        assert result.selected_photo_ids == []

    def test_no_videos_returns_photos_only(self):
        """All-photo selection works when there are no videos."""
        photos = [
            _make_photo("p1", 4.0, 0.7, date=datetime(2025, 7, 1, tzinfo=UTC)),
            _make_photo("p2", 4.0, 0.5, date=datetime(2025, 7, 15, tzinfo=UTC)),
        ]
        result = select_within_budget([], photos, content_budget=10.0)
        assert result.kept_video_ids == set()
        assert set(result.selected_photo_ids) == {"p1", "p2"}
        assert result.content_duration == 8.0

    def test_min_photo_ratio_reserves_budget_for_photos(self):
        """10% min_photo_ratio reserves budget so photos get in."""
        videos = [
            _make_video("v1", 10.0, 0.9),
            _make_video("v2", 10.0, 0.8),
            _make_video("v3", 10.0, 0.7),
        ]
        photos = [
            _make_photo("p1", 4.0, 0.6, date=datetime(2025, 7, 5, tzinfo=UTC)),
            _make_photo("p2", 4.0, 0.5, date=datetime(2025, 7, 20, tzinfo=UTC)),
        ]
        # Budget 30s, videos = 30s (exactly fills it).
        # min_photo_ratio=0.10 → reserve 3s for photos.
        # Video budget = 27s → can fit v1+v2 (20s), not v3.
        # Remaining = 30 - 20 = 10s → both photos fit.
        result = select_within_budget(videos, photos, content_budget=30.0, min_photo_ratio=0.10)
        assert len(result.selected_photo_ids) >= 1  # At least one photo

    def test_min_photo_ratio_zero_allows_no_photos(self):
        """min_photo_ratio=0 means no budget reserved for photos."""
        videos = [_make_video("v1", 10.0, 0.9)]
        photos = [_make_photo("p1", 4.0, 0.5)]
        result = select_within_budget(videos, photos, content_budget=10.0, min_photo_ratio=0.0)
        assert result.selected_photo_ids == []

    def test_min_photo_ratio_doesnt_force_when_no_photos_available(self):
        """Ratio reservation does nothing when there are no photo candidates."""
        videos = [_make_video("v1", 10.0, 0.9)]
        result = select_within_budget(videos, [], content_budget=20.0, min_photo_ratio=0.10)
        assert result.kept_video_ids == {"v1"}
        assert result.selected_photo_ids == []


class TestDetectPhotoResolution:
    """Photo resolution must match the dominant clip orientation."""

    def test_portrait_clips_swap_resolution(self):
        from immich_memories.generate import GenerationParams, _detect_photo_resolution

        # WHY: mock VideoClipInfo — we're testing orientation detection, not Immich
        portrait_clip = MagicMock(width=1080, height=1920)
        landscape_clip = MagicMock(width=1920, height=1080)

        config = MagicMock()
        config.output.resolution_tuple = (1920, 1080)

        params = GenerationParams(
            clips=[portrait_clip, portrait_clip, landscape_clip],
            output_path=MagicMock(),
            config=config,
        )
        w, h = _detect_photo_resolution(params)
        # 2/3 portrait → swap
        assert w == 1080
        assert h == 1920

    def test_landscape_clips_keep_resolution(self):
        from immich_memories.generate import GenerationParams, _detect_photo_resolution

        landscape_clip = MagicMock(width=1920, height=1080)

        config = MagicMock()
        config.output.resolution_tuple = (1920, 1080)

        params = GenerationParams(
            clips=[landscape_clip, landscape_clip],
            output_path=MagicMock(),
            config=config,
        )
        w, h = _detect_photo_resolution(params)
        assert w == 1920
        assert h == 1080


class TestGenerationParamsTargetDuration:
    """GenerationParams has target_duration_seconds field."""

    def test_generate_params_has_target_duration(self):
        from immich_memories.generate import GenerationParams

        params = GenerationParams(
            clips=[],
            output_path=MagicMock(),
            config=MagicMock(),
            target_duration_seconds=60.0,
        )
        assert params.target_duration_seconds == 60.0

    def test_generate_params_target_duration_defaults_none(self):
        from immich_memories.generate import GenerationParams

        params = GenerationParams(
            clips=[],
            output_path=MagicMock(),
            config=MagicMock(),
        )
        assert params.target_duration_seconds is None


class TestScorePhotos:
    """Tests for the extracted score_photos() function."""

    def test_score_photos_returns_scored_list(self, tmp_path):
        now = datetime.now(tz=UTC)
        assets = [
            Asset(
                id="photo1",
                type="IMAGE",
                fileCreatedAt=now,
                fileModifiedAt=now,
                updatedAt=now,
                isFavorite=True,
            ),
            Asset(
                id="photo2",
                type="IMAGE",
                fileCreatedAt=now,
                fileModifiedAt=now,
                updatedAt=now,
                isFavorite=False,
            ),
        ]
        config = PhotoConfig()
        # WHY: mock download_fn — we're testing scoring, not I/O
        download_fn = MagicMock()
        result = score_photos(
            assets=assets,
            config=config,
            video_clip_count=5,
            work_dir=tmp_path,
            download_fn=download_fn,
        )
        assert len(result) == 2
        # Each entry is (Asset, float)
        assert all(isinstance(score, float) for _, score in result)
        # Favorite should score higher than non-favorite
        scores_by_id = {a.id: s for a, s in result}
        assert scores_by_id["photo1"] > scores_by_id["photo2"]
