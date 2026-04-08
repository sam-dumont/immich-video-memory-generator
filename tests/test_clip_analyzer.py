"""Tests for ClipAnalyzer orchestration logic, cache paths, and error handling."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from immich_memories.analysis.clip_analyzer import ClipAnalyzer
from immich_memories.analysis.smart_pipeline import ClipWithSegment, PipelineConfig
from immich_memories.config_loader import Config
from tests.conftest import make_clip

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_analyzer(
    *,
    analysis_depth: str = "thorough",
    avg_clip_duration: float = 5.0,
    app_config: Config | None = None,
) -> tuple[ClipAnalyzer, MagicMock, MagicMock, MagicMock]:
    """Build a ClipAnalyzer with mock dependencies.

    Returns (analyzer, mock_client, mock_cache, mock_preview_builder).
    """
    pipeline_config = PipelineConfig(
        avg_clip_duration=avg_clip_duration,
        analysis_depth=analysis_depth,
    )

    # WHY: Replaces Immich API client (network I/O)
    mock_client = MagicMock()

    # WHY: Replaces SQLite cache database
    mock_cache = MagicMock()
    mock_cache.get_analysis.return_value = None

    # WHY: Replaces FFmpeg preview extraction
    mock_preview = MagicMock()
    mock_preview.find_cached_preview.return_value = None

    config = app_config or Config()

    analyzer = ClipAnalyzer(
        config=pipeline_config,
        client=mock_client,
        analysis_cache=mock_cache,
        preview_builder=mock_preview,
        app_config=config,
    )
    return analyzer, mock_client, mock_cache, mock_preview


def _make_cached_segment(
    *,
    start: float = 1.0,
    end: float = 4.0,
    score: float = 0.75,
    llm_description: str | None = None,
    llm_emotion: str | None = None,
    llm_setting: str | None = None,
    llm_activities: list[str] | None = None,
    llm_subjects: list[str] | None = None,
    llm_interestingness: float | None = None,
    llm_quality: float | None = None,
    audio_categories: list[str] | None = None,
) -> MagicMock:
    """Build a mock CachedSegment."""
    seg = MagicMock()
    seg.start_time = start
    seg.end_time = end
    seg.total_score = score
    seg.llm_description = llm_description
    seg.llm_emotion = llm_emotion
    seg.llm_setting = llm_setting
    seg.llm_activities = llm_activities
    seg.llm_subjects = llm_subjects
    seg.llm_interestingness = llm_interestingness
    seg.llm_quality = llm_quality
    seg.audio_categories = audio_categories
    return seg


def _make_cached_analysis(segment: MagicMock | None = None) -> MagicMock:
    """Build a mock CachedVideoAnalysis wrapping a segment."""
    analysis = MagicMock()
    analysis.segments = [segment] if segment else []
    return analysis


def _make_tracker() -> MagicMock:
    """Build a mock ProgressTracker."""
    tracker = MagicMock()
    return tracker


# ---------------------------------------------------------------------------
# _check_analysis_cache
# ---------------------------------------------------------------------------


class TestCheckAnalysisCache:
    def test_no_cached_analysis_returns_none(self):
        analyzer, _, mock_cache, _ = _make_analyzer()
        mock_cache.get_analysis.return_value = None

        clip = make_clip("asset-1", duration=10.0)
        result = analyzer._check_analysis_cache(clip)

        assert result is None

    def test_cached_analysis_with_empty_segments_returns_none(self):
        analyzer, _, mock_cache, _ = _make_analyzer()
        mock_cache.get_analysis.return_value = _make_cached_analysis(segment=None)

        clip = make_clip("asset-2", duration=10.0)
        result = analyzer._check_analysis_cache(clip)

        assert result is None

    def test_cached_analysis_returns_segment_data(self):
        analyzer, _, mock_cache, mock_preview = _make_analyzer()
        seg = _make_cached_segment(start=2.0, end=5.0, score=0.8)
        mock_cache.get_analysis.return_value = _make_cached_analysis(seg)
        mock_preview.find_cached_preview.return_value = "/tmp/preview.mp4"

        clip = make_clip("asset-3", duration=10.0)
        result = analyzer._check_analysis_cache(clip)

        assert result is not None
        start, end, score, preview_path, llm_analysis = result
        assert start == 2.0
        assert end == 5.0
        assert score == 0.8
        assert preview_path == "/tmp/preview.mp4"
        assert llm_analysis is None

    def test_cached_analysis_with_llm_data(self):
        analyzer, _, mock_cache, mock_preview = _make_analyzer()
        seg = _make_cached_segment(
            start=1.0,
            end=3.5,
            score=0.9,
            llm_description="Kids playing",
            llm_emotion="joyful",
            llm_setting="park",
            llm_activities=["running", "laughing"],
            llm_subjects=["children"],
            llm_interestingness=0.85,
            llm_quality=0.7,
        )
        mock_cache.get_analysis.return_value = _make_cached_analysis(seg)
        mock_preview.find_cached_preview.return_value = None

        clip = make_clip("asset-4", duration=10.0)
        result = analyzer._check_analysis_cache(clip)

        assert result is not None
        _, _, _, _, llm_analysis = result
        assert llm_analysis is not None
        assert llm_analysis["description"] == "Kids playing"
        assert llm_analysis["emotion"] == "joyful"
        assert llm_analysis["setting"] == "park"
        assert llm_analysis["activities"] == ["running", "laughing"]
        assert llm_analysis["subjects"] == ["children"]
        assert llm_analysis["interestingness"] == 0.85
        assert llm_analysis["quality"] == 0.7

    def test_cached_analysis_with_audio_categories_populates_clip(self):
        analyzer, _, mock_cache, _ = _make_analyzer()
        seg = _make_cached_segment(audio_categories=["laughter", "speech"])
        mock_cache.get_analysis.return_value = _make_cached_analysis(seg)

        clip = make_clip("asset-5", duration=10.0)
        assert clip.audio_categories is None

        analyzer._check_analysis_cache(clip)

        assert clip.audio_categories == ["laughter", "speech"]

    def test_picks_segment_with_highest_score(self):
        analyzer, _, mock_cache, _ = _make_analyzer()
        low = _make_cached_segment(start=0.0, end=2.0, score=0.3)
        high = _make_cached_segment(start=5.0, end=8.0, score=0.95)
        analysis = MagicMock()
        analysis.segments = [low, high]
        mock_cache.get_analysis.return_value = analysis

        clip = make_clip("asset-6", duration=10.0)
        result = analyzer._check_analysis_cache(clip)

        assert result is not None
        start, end, score, _, _ = result
        assert start == 5.0
        assert end == 8.0
        assert score == 0.95


# ---------------------------------------------------------------------------
# phase_analyze — short clip filtering
# ---------------------------------------------------------------------------


class TestPhaseAnalyzeFiltering:
    @patch("immich_memories.analysis.content_analyzer.ContentAnalyzer")
    def test_clips_shorter_than_min_duration_are_skipped(self, mock_ca_cls):
        analyzer, _, _, _ = _make_analyzer()
        short_clip = make_clip("short", duration=1.0)
        valid_clip = make_clip("valid", duration=5.0)
        tracker = _make_tracker()

        # WHY: _analyze_clip_with_preview hits many subsystems — mock it directly
        analyzer._analyze_clip_with_preview = MagicMock(
            return_value=(0.0, 3.0, 0.7, "/tmp/p.mp4", None)
        )

        results = analyzer.phase_analyze([short_clip, valid_clip], tracker)

        assert len(results) == 1
        assert results[0].clip.asset.id == "valid"

    @patch("immich_memories.analysis.content_analyzer.ContentAnalyzer")
    def test_tracker_receives_count_of_valid_clips_only(self, mock_ca_cls):
        analyzer, _, _, _ = _make_analyzer()
        clips = [
            make_clip("a", duration=0.5),
            make_clip("b", duration=1.4),
            make_clip("c", duration=5.0),
            make_clip("d", duration=8.0),
        ]
        tracker = _make_tracker()
        analyzer._analyze_clip_with_preview = MagicMock(return_value=(0.0, 3.0, 0.5, None, None))

        analyzer.phase_analyze(clips, tracker)

        # start_phase should be called with PipelinePhase.ANALYZING and 2 valid clips
        call_args = tracker.start_phase.call_args
        assert call_args[0][1] == 2  # total_items = 2 valid clips

    @patch("immich_memories.analysis.content_analyzer.ContentAnalyzer")
    def test_all_short_clips_returns_empty(self, mock_ca_cls):
        analyzer, _, _, _ = _make_analyzer()
        clips = [make_clip("tiny", duration=0.3)]
        tracker = _make_tracker()

        results = analyzer.phase_analyze(clips, tracker)

        assert results == []

    @patch("immich_memories.analysis.content_analyzer.ContentAnalyzer")
    def test_boundary_duration_exactly_at_threshold(self, mock_ca_cls):
        """Clips at exactly 1.5s should be included."""
        analyzer, _, _, _ = _make_analyzer()
        clip = make_clip("boundary", duration=1.5)
        tracker = _make_tracker()
        analyzer._analyze_clip_with_preview = MagicMock(return_value=(0.0, 1.5, 0.4, None, None))

        results = analyzer.phase_analyze([clip], tracker)

        assert len(results) == 1


# ---------------------------------------------------------------------------
# phase_analyze — error fallback
# ---------------------------------------------------------------------------


class TestPhaseAnalyzeErrorFallback:
    @patch("immich_memories.analysis.content_analyzer.ContentAnalyzer")
    def test_error_produces_fallback_segment(self, mock_ca_cls):
        analyzer, _, _, _ = _make_analyzer(avg_clip_duration=4.0)
        clip = make_clip("fail", duration=10.0)
        tracker = _make_tracker()

        analyzer._analyze_clip_with_preview = MagicMock(side_effect=RuntimeError("FFmpeg crashed"))

        results = analyzer.phase_analyze([clip], tracker)

        assert len(results) == 1
        r = results[0]
        assert r.start_time == 0.0
        assert r.end_time == 4.0  # min(10.0, avg_clip_duration=4.0)
        assert r.score == 0.0

    @patch("immich_memories.analysis.content_analyzer.ContentAnalyzer")
    def test_fallback_caps_at_duration_for_short_clips(self, mock_ca_cls):
        analyzer, _, _, _ = _make_analyzer(avg_clip_duration=10.0)
        clip = make_clip("short-fail", duration=3.0)
        tracker = _make_tracker()

        analyzer._analyze_clip_with_preview = MagicMock(side_effect=ValueError("bad video"))

        results = analyzer.phase_analyze([clip], tracker)

        assert results[0].end_time == 3.0  # min(3.0, avg_clip_duration=10.0)

    @patch("immich_memories.analysis.content_analyzer.ContentAnalyzer")
    def test_error_reports_to_tracker(self, mock_ca_cls):
        analyzer, _, _, _ = _make_analyzer()
        clip = make_clip("err", duration=5.0)
        tracker = _make_tracker()

        analyzer._analyze_clip_with_preview = MagicMock(side_effect=RuntimeError("disk full"))

        results = analyzer.phase_analyze([clip], tracker)

        # Verify fallback segment was still produced despite the error
        assert len(results) == 1
        assert results[0].score == 0.0
        # Verify error details were reported to the tracker
        call_kwargs = tracker.complete_item.call_args[1]
        assert call_kwargs["success"] is False
        assert "disk full" in call_kwargs["error"]

    @patch("immich_memories.analysis.content_analyzer.ContentAnalyzer")
    def test_fallback_uses_default_duration_when_falsy(self, mock_ca_cls):
        """When clip.duration_seconds is falsy (0), fallback uses 10 as default."""
        analyzer, _, _, _ = _make_analyzer(avg_clip_duration=5.0)
        clip = make_clip("no-dur", duration=3.0)
        tracker = _make_tracker()

        analyzer._analyze_clip_with_preview = MagicMock(side_effect=RuntimeError("crash"))

        results = analyzer.phase_analyze([clip], tracker)

        # duration=3.0, avg=5.0 → min(3.0, 5.0) = 3.0
        assert results[0].end_time == 3.0


# ---------------------------------------------------------------------------
# phase_analyze — LLM data mapping
# ---------------------------------------------------------------------------


class TestPhaseAnalyzeLLMMapping:
    @patch("immich_memories.analysis.content_analyzer.ContentAnalyzer")
    def test_llm_analysis_populates_clip_fields(self, mock_ca_cls):
        analyzer, _, _, _ = _make_analyzer()
        clip = make_clip("llm-clip", duration=8.0)
        tracker = _make_tracker()

        llm_data = {
            "description": "Sunset over the ocean",
            "emotion": "calm",
            "setting": "beach",
            "activities": ["watching"],
            "subjects": ["sky", "water"],
            "interestingness": 0.9,
            "quality": 0.8,
        }
        analyzer._analyze_clip_with_preview = MagicMock(
            return_value=(1.0, 6.0, 0.85, "/tmp/preview.mp4", llm_data)
        )

        results = analyzer.phase_analyze([clip], tracker)

        result_clip = results[0].clip
        assert result_clip.llm_description == "Sunset over the ocean"
        assert result_clip.llm_emotion == "calm"
        assert result_clip.llm_setting == "beach"
        assert result_clip.llm_activities == ["watching"]
        assert result_clip.llm_subjects == ["sky", "water"]
        assert result_clip.llm_interestingness == 0.9
        assert result_clip.llm_quality == 0.8

    @patch("immich_memories.analysis.content_analyzer.ContentAnalyzer")
    def test_no_llm_analysis_leaves_clip_fields_none(self, mock_ca_cls):
        analyzer, _, _, _ = _make_analyzer()
        clip = make_clip("no-llm", duration=5.0)
        tracker = _make_tracker()

        analyzer._analyze_clip_with_preview = MagicMock(return_value=(0.0, 3.0, 0.5, None, None))

        results = analyzer.phase_analyze([clip], tracker)

        result_clip = results[0].clip
        assert result_clip.llm_description is None
        assert result_clip.llm_emotion is None
        assert result_clip.llm_setting is None

    @patch("immich_memories.analysis.content_analyzer.ContentAnalyzer")
    def test_llm_data_passed_to_tracker(self, mock_ca_cls):
        analyzer, _, _, _ = _make_analyzer()
        clip = make_clip("llm-tracked", duration=5.0)
        tracker = _make_tracker()

        llm_data = {
            "description": "People dancing",
            "emotion": "happy",
            "interestingness": 0.7,
            "quality": 0.6,
        }
        analyzer._analyze_clip_with_preview = MagicMock(
            return_value=(0.5, 4.0, 0.65, "/tmp/p.mp4", llm_data)
        )

        analyzer.phase_analyze([clip], tracker)

        call_kwargs = tracker.complete_item.call_args[1]
        assert call_kwargs["llm_description"] == "People dancing"
        assert call_kwargs["llm_emotion"] == "happy"
        assert call_kwargs["llm_interestingness"] == 0.7
        assert call_kwargs["llm_quality"] == 0.6


# ---------------------------------------------------------------------------
# _analyze_clip_with_preview — fast mode
# ---------------------------------------------------------------------------


class TestAnalyzeClipFastMode:
    def test_fast_mode_non_favorite_skips_unified(self):
        analyzer, _, mock_cache, mock_preview = _make_analyzer(analysis_depth="fast")

        clip = make_clip("non-fav", duration=10.0, is_favorite=False)
        mock_cache.get_analysis.return_value = None

        # WHY: _download_analysis_video and _run_analysis_with_fallback
        #       are deep orchestration — mock them to test fast-mode branching
        analyzer._download_analysis_video = MagicMock(return_value=(MagicMock(), MagicMock(), None))
        analyzer._run_analysis_with_fallback = MagicMock(return_value=(1.0, 4.0, 0.5, None))
        mock_preview.extract_and_log_preview.return_value = "/tmp/p.mp4"

        analyzer._analyze_clip_with_preview(clip)

        call_kwargs = analyzer._run_analysis_with_fallback.call_args[1]
        assert call_kwargs["use_unified"] is False

    def test_fast_mode_favorite_uses_unified(self):
        config = Config()
        config.analysis.use_unified_analysis = True
        analyzer, _, mock_cache, mock_preview = _make_analyzer(
            analysis_depth="fast", app_config=config
        )

        clip = make_clip("fav", duration=10.0, is_favorite=True)
        mock_cache.get_analysis.return_value = None

        analyzer._download_analysis_video = MagicMock(return_value=(MagicMock(), MagicMock(), None))
        analyzer._run_analysis_with_fallback = MagicMock(
            return_value=(1.0, 4.0, 0.6, {"description": "Great moment"})
        )
        mock_preview.extract_and_log_preview.return_value = "/tmp/p.mp4"

        analyzer._analyze_clip_with_preview(clip)

        call_kwargs = analyzer._run_analysis_with_fallback.call_args[1]
        assert call_kwargs["use_unified"] is True

    def test_thorough_mode_uses_unified_for_all(self):
        config = Config()
        config.analysis.use_unified_analysis = True
        analyzer, _, mock_cache, mock_preview = _make_analyzer(
            analysis_depth="thorough", app_config=config
        )

        clip = make_clip("non-fav-thorough", duration=10.0, is_favorite=False)
        mock_cache.get_analysis.return_value = None

        analyzer._download_analysis_video = MagicMock(return_value=(MagicMock(), MagicMock(), None))
        analyzer._run_analysis_with_fallback = MagicMock(return_value=(1.0, 4.0, 0.6, None))
        mock_preview.extract_and_log_preview.return_value = None

        analyzer._analyze_clip_with_preview(clip)

        call_kwargs = analyzer._run_analysis_with_fallback.call_args[1]
        assert call_kwargs["use_unified"] is True


# ---------------------------------------------------------------------------
# _analyze_clip_with_preview — cache hit short-circuits
# ---------------------------------------------------------------------------


class TestAnalyzeClipCacheHit:
    def test_cache_hit_returns_without_download(self):
        analyzer, mock_client, mock_cache, mock_preview = _make_analyzer()
        seg = _make_cached_segment(start=2.0, end=5.0, score=0.8)
        mock_cache.get_analysis.return_value = _make_cached_analysis(seg)
        mock_preview.find_cached_preview.return_value = "/tmp/cached.mp4"

        clip = make_clip("cached", duration=10.0)
        result = analyzer._analyze_clip_with_preview(clip)

        assert result == (2.0, 5.0, 0.8, "/tmp/cached.mp4", None)
        # No download should have happened
        mock_client.download_asset.assert_not_called()


# ---------------------------------------------------------------------------
# _analyze_clip_with_preview — zero-score passthrough
# ---------------------------------------------------------------------------


class TestAnalyzeClipZeroScore:
    def test_zero_score_analysis_returns_early_without_preview(self):
        analyzer, _, mock_cache, mock_preview = _make_analyzer()
        mock_cache.get_analysis.return_value = None

        clip = make_clip("zero", duration=10.0)

        analyzer._download_analysis_video = MagicMock(return_value=(MagicMock(), MagicMock(), None))
        # start=0, end>0, score=0 triggers early return
        analyzer._run_analysis_with_fallback = MagicMock(return_value=(0.0, 5.0, 0.0, None))

        result = analyzer._analyze_clip_with_preview(clip)

        assert result == (0.0, 5.0, 0.0, None, None)
        mock_preview.extract_and_log_preview.assert_not_called()


# ---------------------------------------------------------------------------
# _cleanup_pipeline_resources
# ---------------------------------------------------------------------------


class TestCleanupPipelineResources:
    def test_cleans_up_content_analyzer(self):
        analyzer, _, _, _ = _make_analyzer()
        mock_content = MagicMock()
        mock_content.close = MagicMock()
        analyzer._cached_content_analyzer = mock_content

        analyzer._cleanup_pipeline_resources()

        mock_content.close.assert_called_once()
        assert analyzer._cached_content_analyzer is None

    def test_cleans_up_audio_analyzer(self):
        analyzer, _, _, _ = _make_analyzer()
        mock_audio = MagicMock()
        mock_audio.cleanup = MagicMock()
        analyzer._cached_audio_analyzer = mock_audio

        analyzer._cleanup_pipeline_resources()

        mock_audio.cleanup.assert_called_once()
        assert analyzer._cached_audio_analyzer is None

    def test_cleanup_both_at_once(self):
        analyzer, _, _, _ = _make_analyzer()
        mock_content = MagicMock()
        mock_audio = MagicMock()
        analyzer._cached_content_analyzer = mock_content
        analyzer._cached_audio_analyzer = mock_audio

        analyzer._cleanup_pipeline_resources()

        assert analyzer._cached_content_analyzer is None
        assert analyzer._cached_audio_analyzer is None

    def test_cleanup_when_nothing_cached(self):
        """Should not raise even when no analyzers are cached."""
        analyzer, _, _, _ = _make_analyzer()

        analyzer._cleanup_pipeline_resources()

        assert analyzer._cached_content_analyzer is None
        assert analyzer._cached_audio_analyzer is None


# ---------------------------------------------------------------------------
# _run_analysis_with_fallback
# ---------------------------------------------------------------------------


class TestRunAnalysisWithFallback:
    def test_unified_success_returns_result(self):
        analyzer, _, _, mock_preview = _make_analyzer()
        clip = make_clip("unified-ok", duration=10.0)
        video = MagicMock()

        analyzer._run_unified_analysis = MagicMock(
            return_value=(2.0, 6.0, 0.9, {"description": "test"})
        )

        result = analyzer._run_analysis_with_fallback(clip, video, video, 10.0, use_unified=True)

        assert result == (2.0, 6.0, 0.9, {"description": "test"})
        mock_preview.run_legacy_analysis.assert_not_called()

    def test_unified_failure_falls_back_to_legacy(self):
        analyzer, _, _, mock_preview = _make_analyzer()
        clip = make_clip("fallback", duration=10.0)
        video = MagicMock()

        analyzer._run_unified_analysis = MagicMock(side_effect=RuntimeError("model OOM"))
        mock_preview.run_legacy_analysis.return_value = (1.0, 5.0, 0.4)

        result = analyzer._run_analysis_with_fallback(clip, video, video, 10.0, use_unified=True)

        assert result == (1.0, 5.0, 0.4, None)
        mock_preview.run_legacy_analysis.assert_called_once()

    def test_unified_returns_zero_score_triggers_legacy(self):
        analyzer, _, _, mock_preview = _make_analyzer()
        clip = make_clip("zero-unified", duration=10.0)
        video = MagicMock()

        # Unified returns 0 score (no segments found)
        analyzer._run_unified_analysis = MagicMock(return_value=(0.0, 0.0, 0.0, None))
        mock_preview.run_legacy_analysis.return_value = (0.5, 4.0, 0.3)

        result = analyzer._run_analysis_with_fallback(clip, video, video, 10.0, use_unified=True)

        assert result == (0.5, 4.0, 0.3, None)

    def test_no_unified_goes_straight_to_legacy(self):
        analyzer, _, _, mock_preview = _make_analyzer()
        clip = make_clip("legacy-only", duration=10.0)
        video = MagicMock()

        mock_preview.run_legacy_analysis.return_value = (0.0, 3.0, 0.2)

        result = analyzer._run_analysis_with_fallback(clip, video, video, 10.0, use_unified=False)

        assert result == (0.0, 3.0, 0.2, None)


# ---------------------------------------------------------------------------
# phase_analyze — end-to-end orchestration
# ---------------------------------------------------------------------------


class TestPhaseAnalyzeOrchestration:
    @patch("immich_memories.analysis.content_analyzer.ContentAnalyzer")
    def test_results_contain_correct_clip_with_segment(self, mock_ca_cls):
        analyzer, _, _, _ = _make_analyzer()
        clip = make_clip("orch", duration=12.0)
        tracker = _make_tracker()

        analyzer._analyze_clip_with_preview = MagicMock(
            return_value=(2.0, 7.0, 0.85, "/tmp/p.mp4", None)
        )

        results = analyzer.phase_analyze([clip], tracker)

        assert len(results) == 1
        assert isinstance(results[0], ClipWithSegment)
        assert results[0].start_time == 2.0
        assert results[0].end_time == 7.0
        assert results[0].score == 0.85
        assert results[0].clip is clip

    @patch("immich_memories.analysis.content_analyzer.ContentAnalyzer")
    def test_mixed_success_and_failure(self, mock_ca_cls):
        analyzer, _, _, _ = _make_analyzer(avg_clip_duration=5.0)
        ok_clip = make_clip("ok", duration=8.0)
        fail_clip = make_clip("fail", duration=6.0)
        tracker = _make_tracker()

        def side_effect(c):
            if c.asset.id == "ok":
                return (1.0, 5.0, 0.7, None, None)
            raise RuntimeError("bad")

        analyzer._analyze_clip_with_preview = MagicMock(side_effect=side_effect)

        results = analyzer.phase_analyze([ok_clip, fail_clip], tracker)

        assert len(results) == 2
        # Successful clip
        assert results[0].score == 0.7
        # Failed clip gets fallback
        assert results[1].score == 0.0
        assert results[1].start_time == 0.0
        assert results[1].end_time == 5.0  # min(6.0, 5.0)

    @patch("immich_memories.analysis.content_analyzer.ContentAnalyzer")
    def test_phase_completes_with_all_results(self, mock_ca_cls):
        analyzer, _, _, _ = _make_analyzer()
        tracker = _make_tracker()

        analyzer._analyze_clip_with_preview = MagicMock(return_value=(0.0, 3.0, 0.5, None, None))

        results = analyzer.phase_analyze([make_clip("x", duration=5.0)], tracker)

        # Phase produced the expected result count
        assert len(results) == 1
        assert results[0].score == 0.5
        # Tracker was notified the phase finished
        tracker.complete_phase.assert_called_once()

    @patch("immich_memories.analysis.content_analyzer.ContentAnalyzer")
    def test_cleanup_runs_after_phase(self, mock_ca_cls):
        analyzer, _, _, _ = _make_analyzer()
        tracker = _make_tracker()

        # Pre-seed cached analyzers to verify cleanup clears them
        mock_content = MagicMock()
        mock_audio = MagicMock()
        analyzer._cached_content_analyzer = mock_content
        analyzer._cached_audio_analyzer = mock_audio

        analyzer._analyze_clip_with_preview = MagicMock(return_value=(0.0, 3.0, 0.5, None, None))

        analyzer.phase_analyze([make_clip("y", duration=5.0)], tracker)

        # Verify cleanup actually cleared the cached analyzers
        assert analyzer._cached_content_analyzer is None
        assert analyzer._cached_audio_analyzer is None
