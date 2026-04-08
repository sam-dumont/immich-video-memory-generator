"""Unit tests for clip refiner — photo cap scarcity, interleaving."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from immich_memories.analysis.smart_pipeline import ClipWithSegment
from immich_memories.api.models import Asset, AssetType, VideoClipInfo


def _make_clip(
    asset_id: str,
    date: datetime,
    score: float = 0.5,
    duration: float = 5.0,
    is_favorite: bool = False,
    asset_type: AssetType = AssetType.VIDEO,
) -> ClipWithSegment:
    asset = Asset(
        id=asset_id,
        type=asset_type,
        fileCreatedAt=date,
        fileModifiedAt=date,
        updatedAt=date,
        isFavorite=is_favorite,
    )
    clip = VideoClipInfo(
        asset=asset,
        duration_seconds=duration,
        width=1920,
        height=1080,
    )
    return ClipWithSegment(
        clip=clip,
        start_time=0.0,
        end_time=duration,
        score=score,
    )


class TestPhotoCapScarcity:
    """Photo cap should respect video scarcity — let photos fill when needed."""

    def test_cap_enforced_when_videos_plentiful(self):
        from immich_memories.analysis.clip_refiner import enforce_photo_cap

        base = datetime(2021, 7, 22, tzinfo=UTC)
        clips = [
            _make_clip(f"v{i}", base + timedelta(hours=i), asset_type=AssetType.VIDEO, score=0.5)
            for i in range(6)
        ] + [
            _make_clip(
                f"p{i}", base + timedelta(hours=6 + i), asset_type=AssetType.IMAGE, score=0.3
            )
            for i in range(6)
        ]
        result = enforce_photo_cap(clips, max_ratio=0.40, videos_scarce=False)
        photos = [c for c in result if c.clip.asset.type == AssetType.IMAGE]
        assert len(photos) <= int(len(result) * 0.40) + 1

    def test_cap_skipped_when_videos_scarce(self):
        from immich_memories.analysis.clip_refiner import enforce_photo_cap

        base = datetime(2021, 7, 22, tzinfo=UTC)
        clips = [
            _make_clip("v1", base, asset_type=AssetType.VIDEO, score=0.5),
        ] + [
            _make_clip(
                f"p{i}", base + timedelta(hours=i + 1), asset_type=AssetType.IMAGE, score=0.3
            )
            for i in range(8)
        ]
        # With videos_scarce=True, all photos should be kept
        result = enforce_photo_cap(clips, max_ratio=0.40, videos_scarce=True)
        assert len(result) == 9

    def test_no_videos_all_photos_kept(self):
        """All photos, no videos — nothing to cap against."""
        from immich_memories.analysis.clip_refiner import enforce_photo_cap

        base = datetime(2021, 7, 22, tzinfo=UTC)
        clips = [
            _make_clip(f"p{i}", base + timedelta(hours=i), asset_type=AssetType.IMAGE, score=0.3)
            for i in range(5)
        ]
        result = enforce_photo_cap(clips, max_ratio=0.40, videos_scarce=False)
        assert len(result) == 5


class TestSameDayPhotoLimit:
    """Photos from the same day should be limited to avoid one event dominating."""

    def test_six_race_photos_capped_to_two(self):
        """Brussels 20K: 6 photos from race day → at most 2 survive phase_refine."""
        from unittest.mock import MagicMock

        from immich_memories.analysis.clip_refiner import ClipRefiner
        from immich_memories.analysis.clip_scaler import ClipScaler
        from immich_memories.analysis.smart_pipeline import PipelineConfig

        race_day = datetime(2023, 5, 28, tzinfo=UTC)
        clips = [
            # 6 high-scoring race photos from same day
            _make_clip(
                f"race{i}",
                race_day + timedelta(minutes=i * 15),
                score=0.90 - i * 0.02,
                duration=4.0,
                is_favorite=True,
                asset_type=AssetType.IMAGE,
            )
            for i in range(6)
        ] + [
            # Videos from other days
            _make_clip("bike", datetime(2023, 5, 20, tzinfo=UTC), score=0.75, duration=5.0),
            _make_clip("walk", datetime(2023, 6, 10, tzinfo=UTC), score=0.65, duration=5.0),
            _make_clip("park", datetime(2023, 7, 15, tzinfo=UTC), score=0.70, duration=5.0),
            _make_clip("swim", datetime(2023, 8, 5, tzinfo=UTC), score=0.60, duration=5.0),
        ]

        config = PipelineConfig(target_clips=8, avg_clip_duration=4.0)
        refiner = ClipRefiner(config, ClipScaler())

        tracker = MagicMock()
        tracker.progress = MagicMock()
        tracker.progress.errors = []

        result = refiner.phase_refine(clips, tracker)

        race_photos = [c for c in result.selected_clips if c.asset.id.startswith("race")]
        assert len(race_photos) <= 2, (
            f"Too many race photos ({len(race_photos)}): "
            f"{[c.asset.id for c in race_photos]}. Expected ≤2."
        )

    def test_photos_from_different_days_not_limited(self):
        """Photos spread across days should all survive (no false positives)."""
        from unittest.mock import MagicMock

        from immich_memories.analysis.clip_refiner import ClipRefiner
        from immich_memories.analysis.clip_scaler import ClipScaler
        from immich_memories.analysis.smart_pipeline import PipelineConfig

        clips = [
            _make_clip(
                "jan_photo",
                datetime(2023, 1, 15, tzinfo=UTC),
                score=0.7,
                duration=4.0,
                is_favorite=True,
                asset_type=AssetType.IMAGE,
            ),
            _make_clip(
                "mar_photo",
                datetime(2023, 3, 10, tzinfo=UTC),
                score=0.65,
                duration=4.0,
                is_favorite=True,
                asset_type=AssetType.IMAGE,
            ),
            _make_clip(
                "may_photo",
                datetime(2023, 5, 20, tzinfo=UTC),
                score=0.75,
                duration=4.0,
                is_favorite=True,
                asset_type=AssetType.IMAGE,
            ),
            _make_clip(
                "jul_photo",
                datetime(2023, 7, 5, tzinfo=UTC),
                score=0.60,
                duration=4.0,
                is_favorite=True,
                asset_type=AssetType.IMAGE,
            ),
            # Videos
            _make_clip("vid1", datetime(2023, 2, 10, tzinfo=UTC), score=0.7, duration=5.0),
            _make_clip("vid2", datetime(2023, 6, 15, tzinfo=UTC), score=0.65, duration=5.0),
        ]

        config = PipelineConfig(target_clips=6, avg_clip_duration=4.0)
        refiner = ClipRefiner(config, ClipScaler())

        tracker = MagicMock()
        tracker.progress = MagicMock()
        tracker.progress.errors = []

        result = refiner.phase_refine(clips, tracker)

        photo_ids = {c.asset.id for c in result.selected_clips if c.asset.type == AssetType.IMAGE}
        # All 4 photos from different days should survive
        assert len(photo_ids) >= 3, (
            f"Only {len(photo_ids)} photos survived: {photo_ids}. "
            f"Photos from different days should not be capped."
        )


class TestTemporalCoverage:
    """Ensure at least 1 clip per time period across the full date range."""

    def test_year_range_covers_all_quarters(self):
        """Full year with favorites only in Q2+Q4 — Q1+Q3 should still get clips."""
        from immich_memories.analysis.clip_refiner import ClipRefiner
        from immich_memories.analysis.clip_scaler import ClipScaler
        from immich_memories.analysis.smart_pipeline import PipelineConfig

        clips = [
            # Q1: no favorites, but content exists
            _make_clip("jan", datetime(2022, 1, 15, tzinfo=UTC), score=0.5),
            _make_clip("feb", datetime(2022, 2, 10, tzinfo=UTC), score=0.4),
            _make_clip("mar", datetime(2022, 3, 20, tzinfo=UTC), score=0.6),
            # Q2: favorites
            _make_clip("apr", datetime(2022, 4, 10, tzinfo=UTC), score=0.9, is_favorite=True),
            _make_clip("may", datetime(2022, 5, 15, tzinfo=UTC), score=0.85, is_favorite=True),
            # Q3: no favorites, but content exists
            _make_clip("jul", datetime(2022, 7, 20, tzinfo=UTC), score=0.5),
            _make_clip("aug", datetime(2022, 8, 5, tzinfo=UTC), score=0.55),
            _make_clip("sep", datetime(2022, 9, 12, tzinfo=UTC), score=0.45),
            # Q4: favorites
            _make_clip("oct", datetime(2022, 10, 5, tzinfo=UTC), score=0.8, is_favorite=True),
            _make_clip("dec", datetime(2022, 12, 25, tzinfo=UTC), score=0.75, is_favorite=True),
        ]

        config = PipelineConfig(target_clips=10, avg_clip_duration=5.0)
        refiner = ClipRefiner(config, ClipScaler())
        selected = refiner.select_clips_distributed_by_date(clips, target_count=10)

        months = {c.clip.asset.file_created_at.month for c in selected}
        # Q1 and Q3 must have representation
        q1_covered = bool(months & {1, 2, 3})
        q3_covered = bool(months & {7, 8, 9})
        assert q1_covered, f"Q1 has no clips. Months covered: {sorted(months)}"
        assert q3_covered, f"Q3 has no clips. Months covered: {sorted(months)}"

    def test_week_range_covers_most_days(self):
        """1-week trip with favorites clustering on day 1+2 — later days need coverage."""
        from immich_memories.analysis.clip_refiner import ClipRefiner
        from immich_memories.analysis.clip_scaler import ClipScaler
        from immich_memories.analysis.smart_pipeline import PipelineConfig

        base = datetime(2023, 9, 23, tzinfo=UTC)
        clips = [
            # Day 1-2: favorites cluster with high scores
            _make_clip("d1a", base, score=0.9, is_favorite=True),
            _make_clip("d1b", base + timedelta(hours=2), score=0.85, is_favorite=True),
            _make_clip("d1c", base + timedelta(hours=4), score=0.8, is_favorite=True),
            _make_clip("d2a", base + timedelta(days=1), score=0.75, is_favorite=True),
            _make_clip("d2b", base + timedelta(days=1, hours=3), score=0.7, is_favorite=True),
            # Day 3-7: no favorites, lower scores
            _make_clip("d3", base + timedelta(days=2), score=0.4),
            _make_clip("d4", base + timedelta(days=3), score=0.35),
            _make_clip("d5", base + timedelta(days=4), score=0.3),
            _make_clip("d6", base + timedelta(days=5), score=0.45),
            _make_clip("d7", base + timedelta(days=6), score=0.25),
        ]

        config = PipelineConfig(target_clips=7, avg_clip_duration=5.0)
        refiner = ClipRefiner(config, ClipScaler())
        selected = refiner.select_clips_distributed_by_date(clips, target_count=7)

        days = {c.clip.asset.file_created_at.day for c in selected}
        # Should cover at least 5 different days, not just day 1+2
        assert len(days) >= 5, f"Only {len(days)} days covered: {sorted(days)}"

    def test_year_range_high_scoring_nonfavs_dont_starve_empty_months(self):
        """Non-favorites in Apr score 0.9 but Jan/Jul/Sep exist at 0.3-0.4.
        Without temporal coverage, all slots go to Apr. With it, each quarter gets a clip."""
        from immich_memories.analysis.clip_refiner import ClipRefiner
        from immich_memories.analysis.clip_scaler import ClipScaler
        from immich_memories.analysis.smart_pipeline import PipelineConfig

        clips = [
            # Q1: low scores, no favorites
            _make_clip("jan", datetime(2022, 1, 15, tzinfo=UTC), score=0.3),
            # Q2: favorites + high-scoring non-favs (the cluster)
            _make_clip("apr_f1", datetime(2022, 4, 10, tzinfo=UTC), score=0.9, is_favorite=True),
            _make_clip("apr_f2", datetime(2022, 4, 12, tzinfo=UTC), score=0.85, is_favorite=True),
            _make_clip("apr_nf1", datetime(2022, 4, 15, tzinfo=UTC), score=0.8),
            _make_clip("apr_nf2", datetime(2022, 4, 18, tzinfo=UTC), score=0.75),
            _make_clip("apr_nf3", datetime(2022, 4, 20, tzinfo=UTC), score=0.7),
            # Q3: low scores, no favorites
            _make_clip("jul", datetime(2022, 7, 20, tzinfo=UTC), score=0.35),
            _make_clip("sep", datetime(2022, 9, 12, tzinfo=UTC), score=0.32),
            # Q4: one favorite
            _make_clip("dec_f", datetime(2022, 12, 25, tzinfo=UTC), score=0.8, is_favorite=True),
        ]

        config = PipelineConfig(target_clips=6, avg_clip_duration=5.0)
        refiner = ClipRefiner(config, ClipScaler())
        selected = refiner.select_clips_distributed_by_date(clips, target_count=6)

        months = {c.clip.asset.file_created_at.month for c in selected}
        selected_ids = {c.clip.asset.id for c in selected}

        # Jan (Q1) and Jul or Sep (Q3) must be present despite low scores
        assert 1 in months, f"January missing. Selected: {selected_ids}, months: {sorted(months)}"
        q3 = months & {7, 8, 9}
        assert q3, f"Q3 missing. Selected: {selected_ids}, months: {sorted(months)}"

    def test_reserved_slots_survive_duration_scaling(self):
        """Coverage clips must survive the full phase_refine pipeline.

        The real scenario: favorites fill the budget, then scale_to_target_duration
        trims lowest-scored clips — which are the coverage non-favorites.
        Fix: coverage clips need protection through the full pipeline.
        """
        from unittest.mock import MagicMock

        from immich_memories.analysis.clip_refiner import ClipRefiner
        from immich_memories.analysis.clip_scaler import ClipScaler
        from immich_memories.analysis.smart_pipeline import PipelineConfig

        clips = []
        # Favorites in 6 months — 18 clips * 5s = 90s
        for month, day_start in [(1, 5), (4, 10), (5, 15), (7, 20), (9, 1), (12, 20)]:
            for d in range(3):
                clips.append(
                    _make_clip(
                        f"fav_{month}_{d}",
                        datetime(2023, month, day_start + d, tzinfo=UTC),
                        score=0.8 + d * 0.02,
                        is_favorite=True,
                    )
                )

        # Non-favorites in EMPTY months (Mar, Aug, Oct) — these must survive
        clips.append(_make_clip("mar", datetime(2023, 3, 15, tzinfo=UTC), score=0.3))
        clips.append(_make_clip("aug", datetime(2023, 8, 10, tzinfo=UTC), score=0.35))
        clips.append(_make_clip("oct", datetime(2023, 10, 5, tzinfo=UTC), score=0.32))

        # Target: 12 clips * 5s = 60s budget. 18 favorites exceed it.
        config = PipelineConfig(target_clips=12, avg_clip_duration=5.0)
        refiner = ClipRefiner(config, ClipScaler())

        # WHY: mock tracker — we're testing selection logic, not progress tracking
        tracker = MagicMock()
        tracker.progress = MagicMock()
        tracker.progress.errors = []

        result = refiner.phase_refine(clips, tracker)

        months = {c.asset.file_created_at.month for c in result.selected_clips}

        # Mar, Aug, Oct must survive the full pipeline
        assert 3 in months, f"March missing. Months: {sorted(months)}"
        assert 8 in months, f"August missing. Months: {sorted(months)}"
        assert 10 in months, f"October missing. Months: {sorted(months)}"

    def test_no_favorites_still_gets_temporal_coverage(self):
        """Zero favorites — selection should still spread across time periods."""
        from immich_memories.analysis.clip_refiner import ClipRefiner
        from immich_memories.analysis.clip_scaler import ClipScaler
        from immich_memories.analysis.smart_pipeline import PipelineConfig

        clips = [
            _make_clip("jan", datetime(2022, 1, 15, tzinfo=UTC), score=0.3),
            _make_clip("apr", datetime(2022, 4, 10, tzinfo=UTC), score=0.9),
            _make_clip("apr2", datetime(2022, 4, 11, tzinfo=UTC), score=0.85),
            _make_clip("apr3", datetime(2022, 4, 12, tzinfo=UTC), score=0.8),
            _make_clip("jul", datetime(2022, 7, 20, tzinfo=UTC), score=0.4),
            _make_clip("oct", datetime(2022, 10, 5, tzinfo=UTC), score=0.35),
        ]

        config = PipelineConfig(target_clips=6, avg_clip_duration=5.0)
        refiner = ClipRefiner(config, ClipScaler())
        selected = refiner.select_clips_distributed_by_date(clips, target_count=5)

        months = {c.clip.asset.file_created_at.month for c in selected}
        # Despite Apr dominating by score, Jan, Jul, Oct should be represented
        assert len(months) >= 3, f"Only {len(months)} months: {sorted(months)}"
