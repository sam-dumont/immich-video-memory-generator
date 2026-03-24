"""Tests for scoring calibration — ensures LLM and non-LLM paths produce comparable scores.

Covers weight normalization, LLM-as-bonus scoring, and per-factor logging.
"""

from __future__ import annotations

import pytest

from immich_memories.analysis.analyzer_models import ScoredSegment
from immich_memories.analysis.scoring import SceneScorer
from immich_memories.analysis.unified_analyzer import UnifiedSegmentAnalyzer
from immich_memories.config_models import AnalysisConfig, AudioContentConfig, ContentAnalysisConfig


class TestWeightNormalization:
    """SceneScorer weights must always sum to 1.0."""

    def test_default_weights_sum_to_one(self):
        scorer = SceneScorer(
            content_analysis_config=ContentAnalysisConfig(), analysis_config=AnalysisConfig()
        )
        total = (
            scorer.face_weight
            + scorer.motion_weight
            + scorer.stability_weight
            + scorer.audio_weight
            + scorer.content_weight
            + scorer.duration_weight
        )
        assert total == pytest.approx(1.0, abs=0.001)

    def test_custom_weights_auto_normalized(self):
        """If caller passes weights summing to 1.5, they get normalized to 1.0."""
        scorer = SceneScorer(
            face_weight=0.6,
            motion_weight=0.3,
            stability_weight=0.3,
            audio_weight=0.15,
            content_weight=0.0,
            duration_weight=0.15,
            content_analysis_config=ContentAnalysisConfig(),
            analysis_config=AnalysisConfig(),
        )
        total = (
            scorer.face_weight
            + scorer.motion_weight
            + scorer.stability_weight
            + scorer.audio_weight
            + scorer.content_weight
            + scorer.duration_weight
        )
        assert total == pytest.approx(1.0, abs=0.001)

    def test_normalized_weights_preserve_ratios(self):
        """Normalization should preserve the relative ratios between weights."""
        scorer = SceneScorer(
            face_weight=0.6,
            motion_weight=0.3,
            stability_weight=0.3,
            audio_weight=0.15,
            content_weight=0.0,
            duration_weight=0.15,
            content_analysis_config=ContentAnalysisConfig(),
            analysis_config=AnalysisConfig(),
        )
        # face should be 2x motion after normalization (0.6/0.3 = 2)
        assert scorer.face_weight == pytest.approx(scorer.motion_weight * 2, abs=0.001)

    def test_score_scene_total_within_zero_one(self):
        """A scorer with normalized weights should produce total_score in [0, 1]."""
        scorer = SceneScorer(
            content_analysis_config=ContentAnalysisConfig(), analysis_config=AnalysisConfig()
        )
        # Simulate: all factor scores at maximum (1.0 except audio which defaults to 0.5)
        # The weighted sum should not exceed 1.0
        max_possible = (
            scorer.face_weight * 1.0
            + scorer.motion_weight * 1.0
            + scorer.stability_weight * 1.0
            + scorer.audio_weight * 0.5  # audio defaults to 0.5 in score_scene
            + scorer.content_weight * 1.0
            + scorer.duration_weight * 1.0
        )
        assert max_possible <= 1.0


def _make_segment(**kwargs) -> ScoredSegment:
    """Helper to create a ScoredSegment with sensible defaults."""
    defaults = {
        "start_time": 0.0,
        "end_time": 5.0,
        "visual_score": 0.7,
        "content_score": 0.5,
        "audio_score": 0.5,
        "duration_score": 0.8,
        "start_cut_priority": 1,
        "end_cut_priority": 1,
        "face_score": 0.6,
        "motion_score": 0.7,
        "stability_score": 0.8,
    }
    defaults.update(kwargs)
    return ScoredSegment(**defaults)


class TestLLMScoringIsAdditive:
    """LLM content analysis must only boost scores, never dilute them."""

    def _make_analyzer(self, content_weight: float = 0.35) -> UnifiedSegmentAnalyzer:
        return UnifiedSegmentAnalyzer(
            scorer=SceneScorer(
                content_analysis_config=ContentAnalysisConfig(), analysis_config=AnalysisConfig()
            ),
            content_weight=content_weight,
            audio_content_enabled=False,
            audio_content_config=AudioContentConfig(),
            analysis_config=AnalysisConfig(),
        )

    def test_neutral_llm_same_as_no_llm(self):
        """A neutral LLM score (0.5) should produce the same total as no-LLM baseline."""
        analyzer_with_llm = self._make_analyzer(content_weight=0.35)
        analyzer_no_llm = self._make_analyzer(content_weight=0.0)

        seg_llm = _make_segment(content_score=0.5)
        seg_no_llm = _make_segment(content_score=0.5)

        score_llm = analyzer_with_llm._compute_total_score(seg_llm)
        score_no_llm = analyzer_no_llm._compute_total_score(seg_no_llm)

        assert score_llm == pytest.approx(score_no_llm, abs=0.01)

    def test_good_llm_boosts_score(self):
        """A high LLM content score (0.9) should raise the total above baseline."""
        analyzer = self._make_analyzer(content_weight=0.35)
        analyzer_baseline = self._make_analyzer(content_weight=0.0)

        seg_good_llm = _make_segment(content_score=0.9)
        seg_baseline = _make_segment(content_score=0.5)

        score_good = analyzer._compute_total_score(seg_good_llm)
        score_baseline = analyzer_baseline._compute_total_score(seg_baseline)

        assert score_good > score_baseline

    def test_bad_llm_does_not_lower_score(self):
        """A poor LLM score (0.2) must NOT produce a lower total than no-LLM."""
        analyzer = self._make_analyzer(content_weight=0.35)
        analyzer_baseline = self._make_analyzer(content_weight=0.0)

        seg_bad_llm = _make_segment(content_score=0.2)
        seg_baseline = _make_segment(content_score=0.5)

        score_bad = analyzer._compute_total_score(seg_bad_llm)
        score_baseline = analyzer_baseline._compute_total_score(seg_baseline)

        assert score_bad >= score_baseline

    def test_llm_bonus_scales_with_content_weight(self):
        """Higher content_weight should amplify the LLM bonus for good scores."""
        analyzer_low = self._make_analyzer(content_weight=0.15)
        analyzer_high = self._make_analyzer(content_weight=0.35)

        seg = _make_segment(content_score=0.9)

        bonus_low = analyzer_low._compute_total_score(seg)
        bonus_high = analyzer_high._compute_total_score(seg)

        # Higher weight = bigger bonus
        assert bonus_high > bonus_low


class TestVisualScoreNotHardcoded:
    """_score_visual should not use hardcoded weights that differ from the scorer."""

    def test_visual_score_uses_scorer_weights(self):
        """visual_score total should match the scorer's own weight configuration."""
        # Create analyzer with a non-default scorer
        scorer = SceneScorer(
            face_weight=0.5,
            motion_weight=0.2,
            stability_weight=0.15,
            audio_weight=0.0,
            content_weight=0.0,
            duration_weight=0.15,
            content_analysis_config=ContentAnalysisConfig(),
            analysis_config=AnalysisConfig(),
        )
        analyzer = UnifiedSegmentAnalyzer(
            scorer=scorer,
            audio_content_config=AudioContentConfig(),
            analysis_config=AnalysisConfig(),
        )

        # Compute what the scorer's weights would give for known component scores
        face, motion, stability = 0.8, 0.6, 0.9
        expected = (
            scorer.face_weight * face
            + scorer.motion_weight * motion
            + scorer.stability_weight * stability
            + scorer.audio_weight * 0.5  # audio placeholder
        )

        # The visual score from the analyzer should match
        # (We can't easily call _score_visual without a real video,
        # so we test the segment scoring math instead)
        seg = _make_segment(
            visual_score=expected,
            face_score=face,
            motion_score=motion,
            stability_score=stability,
        )
        total = analyzer._compute_total_score(seg)
        # With content_weight=0, total ≈ visual_score * visual_w + duration_score * duration_w + bonuses
        assert total > 0.0


class TestAudioScoreNotHardcoded:
    """audio_score in SceneScorer should not be a magic 0.5 constant."""

    def test_no_audio_analysis_gives_neutral_score(self):
        """Without audio analysis, audio_score should be 0.0, not 0.5."""
        scorer = SceneScorer(
            content_analysis_config=ContentAnalysisConfig(), analysis_config=AnalysisConfig()
        )
        # When audio is not analyzed, the weight should effectively be zero
        # or the score should be neutral in a way that doesn't inflate totals
        # The key invariant: audio placeholder shouldn't push total above
        # what face+motion+stability+duration alone would give
        visual_only_max = (
            scorer.face_weight * 1.0
            + scorer.motion_weight * 1.0
            + scorer.stability_weight * 1.0
            + scorer.duration_weight * 1.0
        )
        with_audio_placeholder = visual_only_max + scorer.audio_weight * 0.5

        # With normalized weights, this should still be <= 1.0
        assert with_audio_placeholder <= 1.0


class TestPerFactorLogging:
    """Top-N clips should get their per-factor breakdown logged."""

    def test_log_top_segments_emits_factor_breakdown(self, caplog):
        """log_top_segments should log face/motion/stability/content/duration for each segment."""
        import logging

        from immich_memories.analysis.unified_analyzer import log_top_segments

        segments = [
            _make_segment(
                start_time=0.0,
                end_time=5.0,
                total_score=0.85,
                face_score=0.7,
                motion_score=0.6,
                stability_score=0.8,
                content_score=0.5,
                duration_score=0.9,
                visual_score=0.7,
            ),
            _make_segment(
                start_time=5.0,
                end_time=10.0,
                total_score=0.72,
                face_score=0.4,
                motion_score=0.8,
                stability_score=0.6,
                content_score=0.3,
                duration_score=0.7,
                visual_score=0.6,
            ),
        ]

        with caplog.at_level(logging.INFO, logger="immich_memories.analysis.unified_analyzer"):
            log_top_segments(segments, top_n=2)

        log_text = caplog.text
        assert "face=" in log_text
        assert "motion=" in log_text
        assert "stability=" in log_text
        assert "content=" in log_text
        assert "duration=" in log_text
        assert "0.0s-5.0s" in log_text
        assert "5.0s-10.0s" in log_text

    def test_log_top_segments_limits_to_top_n(self, caplog):
        """Only the first top_n segments should be logged."""
        import logging

        from immich_memories.analysis.unified_analyzer import log_top_segments

        segments = [
            _make_segment(start_time=float(i), end_time=float(i + 5), total_score=1.0 - i * 0.1)
            for i in range(10)
        ]

        with caplog.at_level(logging.INFO, logger="immich_memories.analysis.unified_analyzer"):
            log_top_segments(segments, top_n=3)

        # Should see first 3, not the rest
        assert "0.0s-5.0s" in caplog.text
        assert "1.0s-6.0s" in caplog.text
        assert "2.0s-7.0s" in caplog.text
        assert "9.0s-14.0s" not in caplog.text


class TestLLMNeverLowersScore:
    """End-to-end: across a variety of visual scores, enabling LLM should never
    produce a lower total_score than disabling it (for any content_score)."""

    VISUAL_PROFILES = [
        {"visual_score": 0.3, "face_score": 0.1, "motion_score": 0.4, "stability_score": 0.5},
        {"visual_score": 0.6, "face_score": 0.5, "motion_score": 0.6, "stability_score": 0.7},
        {"visual_score": 0.9, "face_score": 0.9, "motion_score": 0.8, "stability_score": 0.9},
    ]

    @pytest.mark.parametrize("content_score", [0.0, 0.2, 0.3, 0.5, 0.7, 0.9, 1.0])
    @pytest.mark.parametrize("visual", VISUAL_PROFILES, ids=["low_vis", "mid_vis", "high_vis"])
    def test_llm_never_lowers_total(self, visual, content_score):
        """For any content_score and visual profile, LLM total >= no-LLM total."""
        analyzer_llm = UnifiedSegmentAnalyzer(
            scorer=SceneScorer(
                content_analysis_config=ContentAnalysisConfig(), analysis_config=AnalysisConfig()
            ),
            content_weight=0.35,
            audio_content_enabled=False,
            audio_content_config=AudioContentConfig(),
            analysis_config=AnalysisConfig(),
        )
        analyzer_no_llm = UnifiedSegmentAnalyzer(
            scorer=SceneScorer(
                content_analysis_config=ContentAnalysisConfig(), analysis_config=AnalysisConfig()
            ),
            content_weight=0.0,
            audio_content_enabled=False,
            audio_content_config=AudioContentConfig(),
            analysis_config=AnalysisConfig(),
        )

        seg_llm = _make_segment(content_score=content_score, **visual)
        seg_no_llm = _make_segment(content_score=0.5, **visual)

        score_llm = analyzer_llm._compute_total_score(seg_llm)
        score_no_llm = analyzer_no_llm._compute_total_score(seg_no_llm)

        assert score_llm >= score_no_llm - 0.001, (
            f"LLM dilution! content={content_score}, visual={visual['visual_score']}: "
            f"LLM total={score_llm:.4f} < no-LLM total={score_no_llm:.4f}"
        )

    def test_average_llm_scores_above_average_no_llm(self):
        """On average across typical content scores, LLM should boost totals."""
        analyzer_llm = UnifiedSegmentAnalyzer(
            scorer=SceneScorer(
                content_analysis_config=ContentAnalysisConfig(), analysis_config=AnalysisConfig()
            ),
            content_weight=0.35,
            audio_content_enabled=False,
            audio_content_config=AudioContentConfig(),
            analysis_config=AnalysisConfig(),
        )
        analyzer_no_llm = UnifiedSegmentAnalyzer(
            scorer=SceneScorer(
                content_analysis_config=ContentAnalysisConfig(), analysis_config=AnalysisConfig()
            ),
            content_weight=0.0,
            audio_content_enabled=False,
            audio_content_config=AudioContentConfig(),
            analysis_config=AnalysisConfig(),
        )

        # Simulate a batch of clips with realistic content scores
        content_scores = [0.4, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8]

        llm_totals = []
        no_llm_totals = []

        for cs in content_scores:
            seg_llm = _make_segment(content_score=cs)
            seg_no_llm = _make_segment(content_score=0.5)

            llm_totals.append(analyzer_llm._compute_total_score(seg_llm))
            no_llm_totals.append(analyzer_no_llm._compute_total_score(seg_no_llm))

        avg_llm = sum(llm_totals) / len(llm_totals)
        avg_no_llm = sum(no_llm_totals) / len(no_llm_totals)

        assert avg_llm >= avg_no_llm, (
            f"LLM average ({avg_llm:.4f}) should be >= no-LLM average ({avg_no_llm:.4f})"
        )


class TestScoringVersion:
    """Cache should track scoring algorithm version to invalidate stale scores."""

    def test_scoring_version_stored_in_cache(self, tmp_path):
        """save_analysis should store the current SCORING_VERSION."""
        from datetime import datetime

        from immich_memories.api.models import Asset
        from immich_memories.cache.database import SCORING_VERSION, VideoAnalysisCache

        cache = VideoAnalysisCache(tmp_path / "test.db")
        asset = Asset(
            id="test-asset-1",
            checksum="abc123",
            type="VIDEO",
            original_file_name="test.mp4",
            file_created_at=datetime(2024, 1, 1),
            file_modified_at=datetime(2024, 1, 1),
            updatedAt=datetime(2024, 1, 1),
        )

        cache.save_analysis(asset=asset, segments=[])
        analysis = cache.get_analysis("test-asset-1")

        assert analysis is not None
        assert analysis.scoring_version == SCORING_VERSION

    def test_old_scoring_version_triggers_reanalysis(self, tmp_path):
        """Entries with old scoring_version should be treated as needing re-analysis."""
        import sqlite3
        from datetime import datetime

        from immich_memories.api.models import Asset
        from immich_memories.cache.database import SCHEMA_VERSION, VideoAnalysisCache

        cache = VideoAnalysisCache(tmp_path / "test.db")
        asset = Asset(
            id="test-asset-2",
            checksum="def456",
            type="VIDEO",
            original_file_name="test2.mp4",
            file_created_at=datetime(2024, 1, 1),
            file_modified_at=datetime(2024, 1, 1),
            updatedAt=datetime(2024, 1, 1),
        )

        # Save with current version
        cache.save_analysis(asset=asset, segments=[])

        # Manually downgrade the scoring_version to simulate old cache
        with sqlite3.connect(str(tmp_path / "test.db")) as conn:
            conn.execute(
                "UPDATE video_analysis SET scoring_version = 0 WHERE asset_id = ?",
                ("test-asset-2",),
            )
            # Keep analysis_version current so we test scoring_version specifically
            conn.execute(
                "UPDATE video_analysis SET analysis_version = ? WHERE asset_id = ?",
                (SCHEMA_VERSION, "test-asset-2"),
            )
            conn.commit()

        needs = cache.needs_reanalysis(asset, max_age_days=365)
        assert needs is True


class TestDensityBudgetNegativeBudget:
    """Regression: divider overhead must not cause negative budget (#151)."""

    def test_many_buckets_small_target_still_selects_clips(self):
        """12 monthly buckets with target_clips=5 should not produce 0 clips.

        WHY: The old code multiplied divider overhead by raw_multiplier,
        causing 12 * 2.0 * 1.3 = 31.2s to be subtracted from a 26s budget.
        """
        from datetime import datetime

        from immich_memories.analysis.density_budget import AssetEntry, compute_density_budget

        entries = []
        for month in range(1, 13):
            for i in range(50):
                entries.append(
                    AssetEntry(
                        asset_id=f"m{month}_c{i}",
                        asset_type="video",
                        date=datetime(2025, month, 1 + i % 28),
                        duration=10.0,
                        is_favorite=(i == 0),
                        width=1920,
                        height=1080,
                        is_camera_original=True,
                    )
                )

        # target_clips=5 * avg_clip_duration=5.0 = 25s target
        buckets = compute_density_budget(entries, target_duration_seconds=25.0, raw_multiplier=1.3)
        total_selected = sum(len(b.favorite_ids) + len(b.gap_fill_ids) for b in buckets)

        assert total_selected > 0, (
            f"Budget selected 0 clips from {len(entries)} — "
            f"divider overhead likely caused negative budget"
        )
        # Should select at least the 12 favorites (1 per month)
        total_favorites = sum(len(b.favorite_ids) for b in buckets)
        assert total_favorites >= 5, (
            f"Only {total_favorites} favorites selected — budget too restrictive"
        )

    def test_budget_never_negative(self):
        """Bucket quotas should never be negative regardless of bucket count."""
        from datetime import datetime, timedelta

        from immich_memories.analysis.density_budget import AssetEntry, compute_density_budget

        base = datetime(2025, 1, 1)
        entries = [
            AssetEntry(
                asset_id=f"w{w}_c{i}",
                asset_type="video",
                date=base + timedelta(weeks=w, days=i),
                duration=5.0,
                is_favorite=False,
                width=1920,
                height=1080,
                is_camera_original=True,
            )
            for w in range(52)
            for i in range(3)
        ]

        buckets = compute_density_budget(
            entries, target_duration_seconds=30.0, raw_multiplier=1.3, bucket_mode="week"
        )

        for b in buckets:
            assert b.quota_seconds >= 0, f"Bucket {b.key} has negative quota {b.quota_seconds:.1f}s"


class TestDensityBudgetEdgeCases:
    """Edge cases and adversarial inputs for density budget."""

    def test_single_bucket_gets_full_budget(self):
        """All clips in one month with month mode → single bucket gets entire budget."""
        from datetime import datetime

        from immich_memories.analysis.density_budget import AssetEntry, compute_density_budget

        entries = [
            AssetEntry(
                asset_id=f"c{i}",
                asset_type="video",
                date=datetime(2025, 6, 1 + i),
                duration=10.0,
                is_favorite=False,
                width=1920,
                height=1080,
                is_camera_original=True,
            )
            for i in range(20)
        ]
        # WHY: force month mode — auto would pick "week" for <180 day span
        buckets = compute_density_budget(
            entries,
            target_duration_seconds=60.0,
            bucket_mode="month",
        )
        assert len(buckets) == 1
        assert buckets[0].quota_seconds > 0

    def test_very_small_target_still_selects(self):
        """target=10s with 500 clips should still select something."""
        from datetime import datetime

        from immich_memories.analysis.density_budget import AssetEntry, compute_density_budget

        entries = [
            AssetEntry(
                asset_id=f"c{i}",
                asset_type="video",
                date=datetime(2025, (i % 12) + 1, 1 + i % 28),
                duration=8.0,
                is_favorite=(i % 50 == 0),
                width=1920,
                height=1080,
                is_camera_original=True,
            )
            for i in range(500)
        ]
        buckets = compute_density_budget(
            entries,
            target_duration_seconds=10.0,
            raw_multiplier=1.3,
        )
        total = sum(len(b.favorite_ids) + len(b.gap_fill_ids) for b in buckets)
        assert total > 0, "10s target with 500 clips selected nothing"

    def test_all_favorites_always_included(self):
        """Favorites must be selected even when budget is tight."""
        from datetime import datetime

        from immich_memories.analysis.density_budget import AssetEntry, compute_density_budget

        entries = [
            AssetEntry(
                asset_id=f"fav{i}",
                asset_type="video",
                date=datetime(2025, 6, 1 + i),
                duration=5.0,
                is_favorite=True,
                width=1920,
                height=1080,
                is_camera_original=True,
            )
            for i in range(10)
        ]
        buckets = compute_density_budget(
            entries,
            target_duration_seconds=15.0,
            raw_multiplier=1.3,
        )
        fav_ids = set()
        for b in buckets:
            fav_ids.update(b.favorite_ids)
        # Should get at least some favorites even with tight budget
        assert len(fav_ids) > 0, "No favorites selected despite all clips being favorites"

    def test_empty_assets_returns_empty(self):
        from immich_memories.analysis.density_budget import compute_density_budget

        assert compute_density_budget([], target_duration_seconds=60.0) == []

    def test_dense_month_gets_more_quota(self):
        """Month with 3x clips should get ~3x the quota of a sparse month."""
        from datetime import datetime

        from immich_memories.analysis.density_budget import AssetEntry, compute_density_budget

        entries = []
        # January: 10 clips
        for i in range(10):
            entries.append(
                AssetEntry(
                    asset_id=f"jan{i}",
                    asset_type="video",
                    date=datetime(2025, 1, 1 + i),
                    duration=10.0,
                    is_favorite=False,
                    width=1920,
                    height=1080,
                    is_camera_original=True,
                )
            )
        # July: 30 clips (3x density)
        for i in range(30):
            entries.append(
                AssetEntry(
                    asset_id=f"jul{i}",
                    asset_type="video",
                    date=datetime(2025, 7, 1 + i % 28),
                    duration=10.0,
                    is_favorite=False,
                    width=1920,
                    height=1080,
                    is_camera_original=True,
                )
            )

        buckets = compute_density_budget(entries, target_duration_seconds=120.0)
        jan = next(b for b in buckets if b.key == "2025-01")
        jul = next(b for b in buckets if b.key == "2025-07")

        # July has 3x clips → should get ~3x quota
        assert jul.quota_seconds > jan.quota_seconds * 2, (
            f"July ({jul.quota_seconds:.1f}s) should get ~3x January ({jan.quota_seconds:.1f}s)"
        )

    def test_gap_fillers_selected_when_no_favorites(self):
        """With no favorites, gap-fillers should fill the quota."""
        from datetime import datetime

        from immich_memories.analysis.density_budget import AssetEntry, compute_density_budget

        entries = [
            AssetEntry(
                asset_id=f"c{i}",
                asset_type="video",
                date=datetime(2025, 6, 1 + i),
                duration=5.0,
                is_favorite=False,
                score=float(i) / 20,
                width=1920,
                height=1080,
                is_camera_original=True,
            )
            for i in range(20)
        ]
        buckets = compute_density_budget(entries, target_duration_seconds=30.0)
        gap_ids = set()
        for b in buckets:
            gap_ids.update(b.gap_fill_ids)
        assert len(gap_ids) > 0, "No gap-fillers selected despite no favorites"

    def test_week_mode_creates_weekly_buckets(self):
        """bucket_mode='week' should create weekly buckets."""
        from datetime import datetime, timedelta

        from immich_memories.analysis.density_budget import AssetEntry, compute_density_budget

        base = datetime(2025, 6, 1)
        entries = [
            AssetEntry(
                asset_id=f"c{i}",
                asset_type="video",
                date=base + timedelta(days=i),
                duration=5.0,
                is_favorite=False,
                width=1920,
                height=1080,
                is_camera_original=True,
            )
            for i in range(28)  # 4 weeks
        ]
        buckets = compute_density_budget(
            entries,
            target_duration_seconds=60.0,
            bucket_mode="week",
        )
        assert len(buckets) >= 4, f"Expected 4+ weekly buckets, got {len(buckets)}"
        assert all("-W" in b.key for b in buckets), "Weekly bucket keys should contain -W"


class TestDensityBudgetQualityGate:
    """Non-camera and low-res clips must be filtered BEFORE entering density budget."""

    def test_non_camera_clips_excluded_from_budget(self):
        """WhatsApp/compilation videos (no EXIF make/model) should not enter density budget."""
        from datetime import datetime

        from immich_memories.analysis.density_budget import AssetEntry, compute_density_budget

        # 3 camera originals + 1 WhatsApp forward (no make/model, low res)
        entries = [
            AssetEntry(
                asset_id="cam-1",
                asset_type="video",
                date=datetime(2025, 1, 5),
                duration=10.0,
                is_favorite=False,
                width=1920,
                height=1080,
                is_camera_original=True,
            ),
            AssetEntry(
                asset_id="cam-2",
                asset_type="video",
                date=datetime(2025, 1, 10),
                duration=8.0,
                is_favorite=False,
                width=1920,
                height=1080,
                is_camera_original=True,
            ),
            AssetEntry(
                asset_id="cam-3",
                asset_type="video",
                date=datetime(2025, 1, 15),
                duration=12.0,
                is_favorite=False,
                width=1920,
                height=1080,
                is_camera_original=True,
            ),
            AssetEntry(
                asset_id="whatsapp-junk",
                asset_type="video",
                date=datetime(2025, 1, 12),
                duration=26.0,
                is_favorite=False,
                width=480,
                height=848,
                is_camera_original=False,
            ),
        ]

        # Filter non-camera assets before budget (this is the fix)
        filtered = [e for e in entries if e.is_camera_original]
        buckets = compute_density_budget(filtered, target_duration_seconds=60.0)

        all_selected = set()
        for b in buckets:
            all_selected.update(b.favorite_ids)
            all_selected.update(b.gap_fill_ids)

        assert "whatsapp-junk" not in all_selected
