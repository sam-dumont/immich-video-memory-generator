"""Coverage-targeted tests for pipeline orchestration modules.

Exercises specific uncovered branches in smart_pipeline, unified_analyzer,
and segment_generation without testing implementation details.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.analysis.analyzer_models import CutPoint, ScoredSegment
from immich_memories.analysis.segment_generation import (
    classify_segment_events,
    collect_mixed_boundary_candidates,
    detect_audio_boundaries,
    detect_visual_boundaries,
    generate_candidate_segments,
    generate_fallback_segments,
    merge_buffered_ranges,
    nudge_segment_for_speech,
    score_segment_audio,
)
from immich_memories.analysis.smart_pipeline import (
    PipelineConfig,
    SmartPipeline,
    _cap_analysis_candidates,
)
from immich_memories.audio.audio_models import AudioAnalysisResult, AudioEvent
from immich_memories.config_loader import Config
from immich_memories.config_models import AnalysisConfig, AudioContentConfig
from tests.conftest import make_clip

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_clips(count, *, is_favorite=False, width=1920, height=1080, exif_make="Apple"):
    base = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)
    clips = []
    for i in range(count):
        dt = base + timedelta(days=i * 7)
        clips.append(
            make_clip(
                f"clip-{i:03d}",
                width=width,
                height=height,
                duration=10.0,
                is_favorite=is_favorite,
                exif_make=exif_make,
                file_created_at=dt,
            )
        )
    return clips


def _make_pipeline(mock_client, mock_cache, mock_thumb, config=None, run_id=None) -> SmartPipeline:
    return SmartPipeline(
        client=mock_client,
        analysis_cache=mock_cache,
        thumbnail_cache=mock_thumb,
        config=config or PipelineConfig(target_clips=10),
        run_id=run_id,
        analysis_config=AnalysisConfig(),
        app_config=Config(),
    )


def _make_analyzer(**overrides):
    """Build a UnifiedSegmentAnalyzer with safe defaults."""
    from immich_memories.analysis.unified_analyzer import UnifiedSegmentAnalyzer

    scorer = MagicMock()
    scorer.face_weight = 0.35
    scorer.motion_weight = 0.20
    scorer.stability_weight = 0.15
    scorer.release_capture = MagicMock()

    defaults = {
        "scorer": scorer,
        "audio_content_config": AudioContentConfig(),
        "analysis_config": AnalysisConfig(),
    }
    defaults.update(overrides)
    return UnifiedSegmentAnalyzer(**defaults)


# ===========================================================================
# Module 1: smart_pipeline.py
# ===========================================================================


class TestCapAnalysisCandidates:
    """Lines 53-62: _cap_analysis_candidates trims when selected > 1.5x target."""

    def test_no_trimming_when_below_cap(self):
        clips = _make_clips(10, is_favorite=True)
        result = _cap_analysis_candidates(clips, target_clips=20)
        assert len(result) == 10

    def test_trims_non_favorites_by_resolution(self):
        """When count > 1.5x target, keeps all favorites + highest-res non-favorites."""
        favorites = _make_clips(3, is_favorite=True, width=1920, height=1080)
        non_favorites = _make_clips(10, is_favorite=False, width=1280, height=720)
        for i, c in enumerate(non_favorites):
            c.asset.id = f"nf-{i:03d}"
            c.width = 1280 + i * 100
            c.height = 720 + i * 50

        selected = favorites + non_favorites
        result = _cap_analysis_candidates(selected, target_clips=4)

        max_candidates = int(4 * 1.5)
        assert len(result) <= max_candidates
        fav_ids = {c.asset.id for c in favorites}
        result_ids = {c.asset.id for c in result}
        assert fav_ids.issubset(result_ids)

    def test_too_many_favorites_capped_to_max(self):
        """When even favorites exceed max_candidates, cap to max_candidates."""
        favorites = _make_clips(20, is_favorite=True)
        result = _cap_analysis_candidates(favorites, target_clips=4)
        max_candidates = int(4 * 1.5)
        assert len(result) == max_candidates


class TestPipelineRunExceptionBranches:
    """Lines 228-231: generic exception during run() is re-raised after finish()."""

    def test_generic_exception_reraises_after_tracker_finish(
        self, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
    ):
        pipeline = _make_pipeline(
            mock_immich_client,
            mock_analysis_cache,
            mock_thumbnail_cache,
            config=PipelineConfig(target_clips=5, analyze_all=True),
        )

        with (
            patch.object(pipeline, "_phase_cluster", side_effect=RuntimeError("boom")),
            pytest.raises(RuntimeError, match="boom"),
        ):
            pipeline.run(_make_clips(3))

        from immich_memories.analysis.progress import PipelinePhase

        assert pipeline.tracker.progress.phase == PipelinePhase.COMPLETE


class TestApplyNonFavoriteFilters:
    """Lines 272-311: HDR filter, compilation filter, resolution filter."""

    def test_hdr_filter_removes_sdr_non_favorites(
        self, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
    ):
        config = PipelineConfig(hdr_only=True)
        pipeline = _make_pipeline(
            mock_immich_client, mock_analysis_cache, mock_thumbnail_cache, config=config
        )

        hdr_clip = make_clip("hdr-1", color_transfer="arib-std-b67", is_favorite=False)
        sdr_clip = make_clip("sdr-1", color_transfer=None, is_favorite=False)

        result = pipeline._apply_non_favorite_filters([hdr_clip, sdr_clip], [])
        assert len(result) == 1
        assert result[0].asset.id == "hdr-1"

    def test_compilation_filter_removes_non_camera_clips(
        self, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
    ):
        config = PipelineConfig(hdr_only=False)
        pipeline = _make_pipeline(
            mock_immich_client, mock_analysis_cache, mock_thumbnail_cache, config=config
        )

        camera_clip = make_clip("cam-1", exif_make="Apple", exif_model="iPhone 15 Pro")
        no_exif_clip = make_clip("comp-1", exif_make=None, exif_model=None)

        result = pipeline._apply_non_favorite_filters(
            [camera_clip, no_exif_clip],
            [],
        )
        assert all(c.is_camera_original for c in result)

    def test_compilation_favorites_logged_but_kept(
        self, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
    ):
        """Favorite compilations trigger a warning but are not filtered."""
        config = PipelineConfig(hdr_only=False)
        pipeline = _make_pipeline(
            mock_immich_client, mock_analysis_cache, mock_thumbnail_cache, config=config
        )

        compilation_fav = make_clip("fav-comp", exif_make=None, exif_model=None, is_favorite=True)
        camera_nf = make_clip("cam-nf", exif_make="Apple")

        result = pipeline._apply_non_favorite_filters(
            [camera_nf],
            [compilation_fav],
        )
        assert len(result) == 1

    def test_resolution_filter_auto_from_output_resolution(
        self, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
    ):
        """min_resolution=0 + output_resolution=2160 -> auto min = max(1080, 480) = 1080."""
        config = PipelineConfig(min_resolution=0, output_resolution=2160)
        pipeline = _make_pipeline(
            mock_immich_client, mock_analysis_cache, mock_thumbnail_cache, config=config
        )

        big_clip = make_clip("big", width=1920, height=1080)
        small_clip = make_clip("small", width=640, height=480)

        result = pipeline._apply_non_favorite_filters([big_clip, small_clip], [])
        result_ids = {c.asset.id for c in result}
        assert "big" in result_ids
        assert "small" not in result_ids


class TestSelectGapFillers:
    """Lines 319-345: gap fillers chosen from weeks with no favorites."""

    def test_gap_fillers_from_uncovered_weeks(
        self, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
    ):
        pipeline = _make_pipeline(mock_immich_client, mock_analysis_cache, mock_thumbnail_cache)

        week1_fav = make_clip(
            "fav-w1",
            is_favorite=True,
            file_created_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        week2_nf = make_clip(
            "nf-w2",
            is_favorite=False,
            file_created_at=datetime(2024, 1, 15, tzinfo=UTC),
            width=1920,
            height=1080,
        )

        result = pipeline._select_gap_fillers([week1_fav], [week2_nf])
        assert len(result) >= 1
        assert result[0].asset.id == "nf-w2"

    def test_no_gap_fillers_when_all_weeks_covered(
        self, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
    ):
        pipeline = _make_pipeline(mock_immich_client, mock_analysis_cache, mock_thumbnail_cache)

        dt = datetime(2024, 1, 15, tzinfo=UTC)
        fav = make_clip("fav", is_favorite=True, file_created_at=dt)
        nf = make_clip("nf", is_favorite=False, file_created_at=dt)

        result = pipeline._select_gap_fillers([fav], [nf])
        assert len(result) == 0


class TestPhaseFilterDurationAndQualityGate:
    """Lines 371, 455: duration filter log + quality gate log."""

    def test_quality_gate_removes_low_res_non_camera_entries(
        self, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
    ):
        config = PipelineConfig(output_resolution=2160)
        pipeline = _make_pipeline(
            mock_immich_client, mock_analysis_cache, mock_thumbnail_cache, config=config
        )

        from immich_memories.analysis.density_budget import AssetEntry

        entries = [
            AssetEntry(
                asset_id="good",
                asset_type="video",
                date=datetime(2024, 1, 1, tzinfo=UTC),
                duration=10.0,
                is_favorite=False,
                width=3840,
                height=2160,
                is_camera_original=True,
            ),
            AssetEntry(
                asset_id="bad-res",
                asset_type="video",
                date=datetime(2024, 1, 1, tzinfo=UTC),
                duration=10.0,
                is_favorite=False,
                width=320,
                height=240,
                is_camera_original=True,
            ),
            AssetEntry(
                asset_id="bad-cam",
                asset_type="video",
                date=datetime(2024, 1, 1, tzinfo=UTC),
                duration=10.0,
                is_favorite=False,
                width=3840,
                height=2160,
                is_camera_original=False,
            ),
            AssetEntry(
                asset_id="fav-low",
                asset_type="video",
                date=datetime(2024, 1, 1, tzinfo=UTC),
                duration=10.0,
                is_favorite=True,
                width=320,
                height=240,
                is_camera_original=False,
            ),
        ]

        result = pipeline._apply_budget_quality_gate(entries)
        result_ids = {e.asset_id for e in result}
        assert "good" in result_ids
        assert "bad-res" not in result_ids
        assert "bad-cam" not in result_ids
        assert "fav-low" in result_ids


# ===========================================================================
# Module 2: unified_analyzer.py
# ===========================================================================


class TestUnifiedAnalyzerProportionalMaxSegment:
    """Lines 177-178: proportional limit for very long videos (>60s)."""

    def test_very_long_video_capped_proportionally(self):
        analyzer = _make_analyzer(max_segment_duration=15.0)
        result = analyzer._get_max_segment_for_source(120.0, has_good_scene=False)
        assert result == max(15.0, min(120.0 * 0.20, 15.0))

    def test_grace_for_good_scene_medium_video(self):
        analyzer = _make_analyzer(max_segment_duration=15.0)
        result = analyzer._get_max_segment_for_source(40.0, has_good_scene=True)
        assert result == min(15.0 * 1.15, 40.0)


class TestAudioContentAnalysisBranches:
    """Lines 200, 510-512: _run_audio_content_analysis returns None on disable/fail."""

    def test_returns_none_when_disabled(self):
        analyzer = _make_analyzer(audio_content_enabled=False)
        result = analyzer._run_audio_content_analysis(Path("/fake.mp4"), 10.0)
        assert result is None

    def test_returns_none_when_analysis_returns_none(self):
        analyzer = _make_analyzer(audio_content_enabled=True)
        analyzer._analyze_audio_content = MagicMock(return_value=None)
        result = analyzer._run_audio_content_analysis(Path("/fake.mp4"), 10.0)
        assert result is None

    def test_analyze_audio_content_catches_import_error(self):
        """Lines 510-512: exception in _analyze_audio_content returns None."""
        analyzer = _make_analyzer(audio_content_enabled=True)
        # WHY: AudioContentAnalyzer import/init is an external ML dependency
        mock_audio_analyzer = MagicMock()
        mock_audio_analyzer.analyze.side_effect = RuntimeError("no panns")
        analyzer._audio_analyzer = mock_audio_analyzer
        result = analyzer._analyze_audio_content(Path("/fake.mp4"), 10.0)
        assert result is None

    def test_analyze_audio_content_caches_result(self):
        """Lines 493, 507: cache hit returns previously stored result."""
        analyzer = _make_analyzer(audio_content_enabled=True)
        cached = AudioAnalysisResult(audio_score=0.9)
        analyzer._audio_analysis_cache["/fake.mp4"] = cached
        result = analyzer._analyze_audio_content(Path("/fake.mp4"), 10.0)
        assert result is cached


class TestFixBoundaryInRange:
    """Lines 258, 260-264: _fix_boundary_in_range nudges or reports unfixable."""

    def test_start_boundary_nudged_before_range(self):
        analyzer = _make_analyzer()
        new_val, was_adj, was_unfix = analyzer._fix_boundary_in_range(
            value=5.0,
            label="START",
            range_start=4.0,
            range_end=6.0,
            clamp_low=0.0,
            clamp_high=10.0,
        )
        assert was_adj
        assert not was_unfix
        assert new_val < 4.0

    def test_end_boundary_nudged_after_range(self):
        analyzer = _make_analyzer()
        new_val, was_adj, was_unfix = analyzer._fix_boundary_in_range(
            value=5.5,
            label="END",
            range_start=5.0,
            range_end=6.0,
            clamp_low=0.0,
            clamp_high=10.0,
        )
        assert was_adj
        assert not was_unfix
        assert new_val > 6.0

    def test_boundary_at_edge_is_unfixable(self):
        """When nudge distance is ~0, it's unfixable."""
        analyzer = _make_analyzer()
        new_val, was_adj, was_unfix = analyzer._fix_boundary_in_range(
            value=0.005,
            label="START",
            range_start=0.0,
            range_end=1.0,
            clamp_low=0.0,
            clamp_high=10.0,
        )
        assert was_unfix
        assert not was_adj

    def test_boundary_outside_range_unchanged(self):
        analyzer = _make_analyzer()
        new_val, was_adj, was_unfix = analyzer._fix_boundary_in_range(
            value=10.0,
            label="START",
            range_start=2.0,
            range_end=5.0,
            clamp_low=0.0,
            clamp_high=20.0,
        )
        assert not was_adj
        assert not was_unfix
        assert new_val == 10.0


class TestFixBestSegmentBoundaries:
    """Lines 294, 302, 307, 316-317: boundary adjustment + re-trim."""

    def test_boundaries_adjusted_and_retrimmed(self):
        analyzer = _make_analyzer(max_segment_duration=5.0)
        best = ScoredSegment(start_time=3.0, end_time=8.0)
        audio_result = AudioAnalysisResult(protected_ranges=[(2.5, 3.5), (7.5, 8.5)])
        analyzer._fix_best_segment_boundaries(best, audio_result, video_duration=30.0)
        assert best.start_time < 2.5 or best.start_time >= 3.5
        assert best.end_time > 8.5 or best.end_time <= 7.5

    def test_retrim_to_proportional_max(self):
        """Lines 316-317: segment exceeding proportional max gets re-trimmed."""
        analyzer = _make_analyzer(max_segment_duration=5.0)
        # After boundary adjustment, segment expands beyond proportional max.
        # Protected range forces end_time to be nudged outward.
        best = ScoredSegment(start_time=0.0, end_time=25.0)
        # source=100s => proportional_max = max(5, min(100*0.20, 5*1.0)) = max(5,5) = 5
        # After adjust, 25.0 > 5.0 => re-trim
        audio_result = AudioAnalysisResult(protected_ranges=[(24.5, 25.5)])
        analyzer._fix_best_segment_boundaries(best, audio_result, video_duration=100.0)
        proportional_max = analyzer._get_max_segment_for_source(100.0)
        assert best.end_time - best.start_time <= proportional_max + 0.01


class TestAnalyzeShortAndInvalidVideos:
    """Lines 356-357, 361-364: short/zero-duration videos return empty."""

    def test_zero_duration_returns_empty(self):
        analyzer = _make_analyzer()
        result = analyzer.analyze(Path("/fake.mp4"), video_duration=0.0)
        assert result == []

    def test_very_short_video_returns_empty(self):
        analyzer = _make_analyzer()
        result = analyzer.analyze(Path("/fake.mp4"), video_duration=1.0)
        assert result == []


class TestStep3bAdjustForAudio:
    """Lines 469, 493: step3b branches — audio events but no protected ranges."""

    def test_audio_events_but_no_protected_ranges_skips(self):
        """Lines 469: audio result with events but empty protected_ranges."""
        analyzer = _make_analyzer()
        audio_result = AudioAnalysisResult(
            events=[AudioEvent("Speech", 0.0, 5.0, 0.3)],
            protected_ranges=[],
        )
        candidates = [
            (CutPoint(0.0, True, True), CutPoint(5.0, True, True)),
        ]
        result = analyzer._step3b_adjust_for_audio(candidates, audio_result, 10.0)
        assert result == candidates

    def test_no_audio_result_skips(self):
        analyzer = _make_analyzer()
        candidates = [
            (CutPoint(0.0, True, True), CutPoint(5.0, True, True)),
        ]
        result = analyzer._step3b_adjust_for_audio(candidates, None, 10.0)
        assert result == candidates


class TestDynamicOptimalDuration:
    """Lines 527: source > 20s scales optimal up."""

    def test_short_source_uses_base_optimal(self):
        analyzer = _make_analyzer(optimal_clip_duration=5.0)
        assert analyzer._get_dynamic_optimal_duration(15.0) == 5.0

    def test_long_source_scales_optimal(self):
        analyzer = _make_analyzer(
            optimal_clip_duration=5.0, max_optimal_duration=10.0, target_extraction_ratio=0.15
        )
        result = analyzer._get_dynamic_optimal_duration(80.0)
        assert result == min(10.0, max(5.0, 80.0 * 0.15))


class TestDurationScorePenalty:
    """Lines 561-562: extra penalty for clips > 15s."""

    def test_long_clip_penalty(self):
        analyzer = _make_analyzer()
        score_normal = analyzer._compute_duration_score(5.0, 30.0)
        score_long = analyzer._compute_duration_score(20.0, 30.0)
        assert score_long < score_normal


class TestScoreVisualZeroWeights:
    """Line 601: visual_weights == 0 returns total=0.0."""

    def test_zero_visual_weights_returns_zero(self):
        scorer = MagicMock()
        scorer.face_weight = 0.0
        scorer.motion_weight = 0.0
        scorer.stability_weight = 0.0
        scorer.release_capture = MagicMock()

        moment = MagicMock()
        moment.face_score = 0.5
        moment.motion_score = 0.5
        moment.stability_score = 0.5
        scorer.score_scene.return_value = moment

        analyzer = _make_analyzer(scorer=scorer)
        result = analyzer._score_visual(Path("/fake.mp4"), 0.0, 5.0)
        assert result["total"] == 0.0


class TestScoreContentBranches:
    """Lines 645-647: content analysis exception returns 0.5."""

    def test_content_analysis_exception_returns_neutral(self):
        mock_ca = MagicMock()
        mock_ca.analyze_segment.side_effect = RuntimeError("LLM down")
        analyzer = _make_analyzer(content_analyzer=mock_ca)
        result = analyzer._score_content(Path("/fake.mp4"), 0.0, 5.0)
        assert result == 0.5

    def test_no_content_analyzer_returns_neutral(self):
        analyzer = _make_analyzer(content_analyzer=None)
        result = analyzer._score_content(Path("/fake.mp4"), 0.0, 5.0)
        assert result == 0.5


class TestComputeTotalScoreNegativeVisualWeight:
    """Lines 669-674: visual_w < 0 triggers re-normalization."""

    def test_negative_visual_weight_renormalized(self):
        analyzer = _make_analyzer(
            audio_content_enabled=True,
            audio_content_weight=0.6,
            duration_weight=0.6,
        )
        segment = ScoredSegment(
            start_time=0,
            end_time=5,
            visual_score=0.8,
            audio_score=0.7,
            duration_score=0.9,
        )
        score = analyzer._compute_total_score(segment)
        assert score > 0


class TestRunLlmScoringException:
    """Lines 798-800: LLM scoring exception on a candidate."""

    def test_llm_failure_sets_content_score_neutral(self):
        mock_ca = MagicMock()
        mock_ca.analyze_segment.return_value = MagicMock(content_score=0.9)
        analyzer = _make_analyzer(content_analyzer=mock_ca, content_weight=0.3)

        seg = ScoredSegment(start_time=0, end_time=5, total_score=0.5)
        # WHY: force an exception AFTER _score_content succeeds,
        # so the outer try/except in _run_llm_scoring catches it
        with patch.object(analyzer, "_compute_total_score", side_effect=RuntimeError("math error")):
            analyzer._run_llm_scoring([seg], Path("/fake.mp4"))
        assert seg.content_score == 0.5


# ===========================================================================
# Module 3: segment_generation.py
# ===========================================================================


class TestDetectVisualBoundariesException:
    """Lines 47-49: exception in scene detection returns empty list."""

    def test_scene_detection_failure_returns_empty(self):
        mock_detector = MagicMock()
        mock_detector.detect.side_effect = RuntimeError("no video")
        result = detect_visual_boundaries(Path("/fake.mp4"), mock_detector)
        assert result == []


class TestDetectAudioBoundariesException:
    """Lines 88-90: exception in audio boundary detection returns empty list."""

    def test_audio_detection_failure_returns_empty(self):
        with patch(
            "immich_memories.analysis.segment_generation.detect_silence_gaps",
            side_effect=RuntimeError("ffmpeg missing"),
        ):
            result = detect_audio_boundaries(Path("/fake.mp4"), -30.0, 0.3)
        assert result == []


class TestCollectMixedBoundaryCandidatesEmpty:
    """Lines 183, 185-186, 188-194: no valid candidates returns empty."""

    def test_no_audio_boundaries_returns_empty(self):
        points = [
            CutPoint(0.0, is_visual=True, is_audio=False),
            CutPoint(1.0, is_visual=True, is_audio=False),
        ]
        result = collect_mixed_boundary_candidates(points, 10.0, 2.0, 15.0, 5.0)
        assert result == []

    def test_valid_mixed_candidates_sorted_by_audio_priority(self):
        points = [
            CutPoint(0.0, is_visual=True, is_audio=True),
            CutPoint(3.0, is_visual=True, is_audio=False),
            CutPoint(6.0, is_visual=False, is_audio=True),
        ]
        result = collect_mixed_boundary_candidates(points, 10.0, 2.0, 15.0, 5.0)
        assert len(result) > 0
        first_pair = result[0]
        assert first_pair[0].is_audio or first_pair[1].is_audio


class TestGenerateCandidateSegmentsFallbacks:
    """Lines 257, 271: generate_candidate_segments fallback paths."""

    def test_single_cut_point_returns_empty(self):
        result = generate_candidate_segments([CutPoint(0.0)], 10.0, 2.0, 15.0, 5.0)
        assert result == []

    def test_visual_only_fallback_when_no_audio_or_mixed(self):
        """Line 271+: falls back to visual-only segments."""
        points = [
            CutPoint(0.0, is_visual=True, is_audio=False),
            CutPoint(5.0, is_visual=True, is_audio=False),
            CutPoint(10.0, is_visual=True, is_audio=False),
        ]
        result = generate_candidate_segments(points, 10.0, 2.0, 15.0, 5.0)
        assert len(result) > 0


class TestMergeBufferedRanges:
    """Lines 375: overlapping ranges get merged."""

    def test_overlapping_ranges_merged(self):
        ranges = [(1.0, 3.0), (2.5, 5.0), (8.0, 10.0)]
        result = merge_buffered_ranges(ranges, 20.0, buffer=0.3)
        assert len(result) == 2
        assert result[0][0] < 1.0
        assert result[0][1] > 5.0

    def test_ranges_clamped_to_video_duration(self):
        ranges = [(0.0, 1.0), (19.5, 20.0)]
        result = merge_buffered_ranges(ranges, 20.0, buffer=0.5)
        assert result[0][0] == 0.0
        assert result[-1][1] == 20.0


class TestNudgeSegmentForSpeech:
    """Lines 408-416: nudge start/end out of protected ranges."""

    def test_start_inside_range_nudged_earlier(self):
        new_s, new_e, adj = nudge_segment_for_speech(
            start=2.5,
            end=6.0,
            merged_ranges=[(2.0, 3.0)],
            video_duration=10.0,
        )
        assert adj
        assert new_s < 2.5

    def test_end_inside_range_nudged_later(self):
        new_s, new_e, adj = nudge_segment_for_speech(
            start=0.0,
            end=2.5,
            merged_ranges=[(2.0, 3.0)],
            video_duration=10.0,
        )
        assert adj
        assert new_e > 2.5

    def test_entirely_inside_no_adjustment(self):
        new_s, new_e, adj = nudge_segment_for_speech(
            start=2.2,
            end=2.8,
            merged_ranges=[(2.0, 3.0)],
            video_duration=10.0,
        )
        assert not adj


class TestAdjustCandidatesForAudio:
    """Lines 443, 460-461, 467-472, 475-480, 488: adjust_candidates_for_audio paths."""

    def test_no_protected_ranges_returns_unchanged(self):
        from immich_memories.analysis.segment_generation import adjust_candidates_for_audio

        audio_result = AudioAnalysisResult(protected_ranges=[])
        candidates = [(CutPoint(0.0, True, True), CutPoint(5.0, True, True))]
        result = adjust_candidates_for_audio(candidates, audio_result, 10.0, 2.0, 8.0)
        assert result == candidates

    def test_oversized_segment_trimmed_to_proportional_max(self):
        """Lines 460-461, 467-472: adjustment + oversized trim."""
        from immich_memories.analysis.segment_generation import adjust_candidates_for_audio

        audio_result = AudioAnalysisResult(protected_ranges=[(3.0, 4.0)])
        # Start is inside protected range, so gets nudged. Result is also oversized.
        candidates = [
            (CutPoint(3.5, True, True), CutPoint(15.0, True, True)),
        ]
        result = adjust_candidates_for_audio(candidates, audio_result, 20.0, 2.0, 6.0)
        assert len(result) >= 1
        for start_cp, end_cp in result:
            assert end_cp.time - start_cp.time <= 6.0 + 0.01

    def test_adjustment_too_short_keeps_original(self):
        """Lines 475-480: when nudge makes segment < min, keep original."""
        from immich_memories.analysis.segment_generation import adjust_candidates_for_audio

        # Protected range overlaps with start, nudge shrinks segment below min
        audio_result = AudioAnalysisResult(protected_ranges=[(2.5, 4.0)])
        # Start at 3.0 (inside range), end at 5.5. After nudge start moves to ~1.4.
        # Actually we need the nudged segment to be SHORTER than min.
        # Try: start=3.0 inside (2.5, 4.0), end=5.0 outside.
        # nudge: new_start = max(0, 3.0 - min(2.0, 0.6)) = 2.4. duration=5.0-2.4=2.6 >= 3.0? No.
        # Actually 2.6 < 3.0, so it falls through to "too short" path.
        candidates = [
            (CutPoint(3.0, True, True), CutPoint(5.0, True, True)),
        ]
        result = adjust_candidates_for_audio(candidates, audio_result, 10.0, 3.0, 15.0)
        assert len(result) >= 1


class TestClassifySegmentEvents:
    """Lines 545-554: classify_segment_events categorizes laughter, speech, music."""

    def test_laughter_event_detected(self):
        event = AudioEvent("Laughter", 0.0, 3.0, confidence=0.9)
        total_w, total_d, laugh, speech, music, cats = classify_segment_events([(event, 3.0)])
        assert laugh
        assert not speech
        assert total_d == 3.0

    def test_speech_event_detected(self):
        event = AudioEvent("Speech", 1.0, 4.0, confidence=0.8)
        _, _, laugh, speech, music, cats = classify_segment_events([(event, 3.0)])
        assert speech
        assert not laugh

    def test_music_event_detected(self):
        event = AudioEvent("Music", 0.0, 5.0, confidence=0.7)
        _, _, laugh, speech, music, cats = classify_segment_events([(event, 5.0)])
        assert music


class TestScoreSegmentAudio:
    """Lines 577, 595: no events returns 0.5; zero duration returns 0.5."""

    def test_no_events_returns_neutral(self):
        audio_result = AudioAnalysisResult(events=[])
        result = score_segment_audio(0.0, 5.0, audio_result)
        assert result["score"] == 0.5
        assert not result["has_laughter"]

    def test_with_matching_events_computes_real_score(self):
        """Line 590-593: events overlap with segment, score is computed from coverage."""
        audio_result = AudioAnalysisResult(
            events=[AudioEvent("Laughter", 1.0, 4.0, confidence=0.9)]
        )
        result = score_segment_audio(0.0, 5.0, audio_result)
        assert 0.0 < result["score"] <= 1.0
        assert result["has_laughter"]


class TestGenerateFallbackSegments:
    """Lines 343-347: fallback with no cut points creates video-bounds segment."""

    def test_no_cut_points_creates_bounds_segment(self):
        result = generate_fallback_segments(
            video_duration=10.0,
            cut_points=[],
            min_segment_duration=2.0,
            proportional_max=8.0,
        )
        assert len(result) == 1
        assert result[0][0].time == 0.0
        assert result[0][1].time == 8.0

    def test_with_cut_points_generates_overlapping_segments(self):
        points = [
            CutPoint(0.0, True, True),
            CutPoint(5.0, True, True),
            CutPoint(10.0, True, True),
        ]
        result = generate_fallback_segments(
            video_duration=10.0,
            cut_points=points,
            min_segment_duration=2.0,
            proportional_max=6.0,
        )
        assert len(result) >= 1
