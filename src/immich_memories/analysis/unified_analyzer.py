"""Unified video segment analysis with audio-aware boundaries.

This module provides audio-aware video segment analysis that ensures cuts
happen during silence gaps rather than mid-sentence. It combines visual
scene detection with audio analysis to find natural cut points.
"""

from __future__ import annotations

import logging
import operator
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.analysis.analyzer_factory import (  # noqa: F401
    create_unified_analyzer_from_config,
)
from immich_memories.analysis.analyzer_models import CutPoint, ScoredSegment  # noqa: F401
from immich_memories.analysis.candidate_generation import CandidateGenerationMixin
from immich_memories.analysis.scenes import SceneDetector, get_video_info
from immich_memories.analysis.scoring import SceneScorer
from immich_memories.analysis.segment_scoring import SegmentScoringMixin

if TYPE_CHECKING:
    from immich_memories.analysis.content_analyzer import ContentAnalyzer
    from immich_memories.audio.content_analyzer import AudioAnalysisResult

logger = logging.getLogger(__name__)


class UnifiedSegmentAnalyzer(SegmentScoringMixin, CandidateGenerationMixin):
    """Unified video segment analysis with audio-aware boundaries.

    This analyzer ensures video segments start and end during silence gaps
    to avoid cutting mid-sentence. It combines visual scene detection with
    audio analysis to find natural cut points.

    The analysis process:
    1. Detect all visual boundaries (PySceneDetect)
    2. Detect all audio boundaries (silence gaps)
    3. Merge into unified cut points with priority
    4. Generate candidate segments that respect audio boundaries
    5. Score each candidate using visual + optional content analysis
    6. Return segments sorted by score
    """

    def __init__(
        self,
        scorer: SceneScorer | None = None,
        content_analyzer: ContentAnalyzer | None = None,
        min_segment_duration: float = 2.0,
        max_segment_duration: float = 15.0,
        silence_threshold_db: float = -30.0,
        min_silence_duration: float = 0.3,
        cut_point_merge_tolerance: float = 0.5,
        content_weight: float = 0.0,
        audio_content_enabled: bool = False,
        audio_content_weight: float = 0.15,
        optimal_clip_duration: float = 5.0,
        max_optimal_duration: float = 10.0,
        target_extraction_ratio: float = 0.15,
        duration_weight: float = 0.15,
        audio_analyzer: object | None = None,
    ):
        """Initialize the unified analyzer.

        Args:
            scorer: SceneScorer for visual analysis. Created if not provided.
            content_analyzer: Optional ContentAnalyzer for LLM analysis.
            min_segment_duration: Minimum segment duration in seconds.
            max_segment_duration: Maximum segment duration in seconds.
            silence_threshold_db: Audio level threshold for silence detection.
            min_silence_duration: Minimum silence gap duration to detect.
            cut_point_merge_tolerance: Time window for merging nearby cut points.
            content_weight: Weight for content analysis score (0-1).
            audio_content_enabled: Enable audio content analysis (laughter detection).
            audio_content_weight: Weight for audio content score (0-1).
            optimal_clip_duration: Base sweet spot duration for clips (default 5s).
            max_optimal_duration: Max optimal duration for long sources (default 10s).
            target_extraction_ratio: Target ratio of clip to source (default 0.15).
            duration_weight: Weight for duration preference score (default 0.15).
        """
        self.scorer = scorer or SceneScorer()
        self.content_analyzer = content_analyzer
        self.min_segment_duration = min_segment_duration
        self.max_segment_duration = max_segment_duration
        self.silence_threshold_db = silence_threshold_db
        self.min_silence_duration = min_silence_duration
        self.cut_point_merge_tolerance = cut_point_merge_tolerance
        self.content_weight = content_weight
        self.audio_content_enabled = audio_content_enabled
        self.audio_content_weight = audio_content_weight
        self.optimal_clip_duration = optimal_clip_duration
        self.max_optimal_duration = max_optimal_duration
        self.target_extraction_ratio = target_extraction_ratio
        self.duration_weight = duration_weight

        self._scene_detector = SceneDetector()
        self._audio_analyzer = audio_analyzer  # Injected or lazy-created
        self._audio_analysis_cache: dict[str, AudioAnalysisResult] = {}

    def clear_cache(self, release_audio_analyzer: bool = False):
        """Clear internal caches to free memory.

        Args:
            release_audio_analyzer: If True, also release the audio analyzer.
                Usually False because the analyzer is shared across clips.
        """
        self._audio_analysis_cache.clear()
        if release_audio_analyzer and self._audio_analyzer is not None:
            if hasattr(self._audio_analyzer, "cleanup"):
                self._audio_analyzer.cleanup()
            self._audio_analyzer = None

    def _get_max_segment_for_source(
        self, source_duration: float, has_good_scene: bool = False
    ) -> float:
        """Calculate maximum segment duration based on source video length.

        Logic:
        - If source <= max_segment_duration: allow full source (no trimming needed)
        - If source > max_segment_duration: cap at max (with 15% grace if good scene)
        - For very long videos (>60s): apply proportional limit (20% of source)

        Args:
            source_duration: Source video duration in seconds.
            has_good_scene: If True, allow 15% grace over max_segment_duration.

        Returns:
            Maximum segment duration for this source.
        """
        # Grace multiplier for good scenes (15% extra allowed)
        grace = 1.15 if has_good_scene else 1.0
        max_with_grace = self.max_segment_duration * grace

        # Short videos: allow using all of it (no trimming needed)
        if source_duration <= self.max_segment_duration:
            return source_duration

        # Medium videos (up to 60s): use max_segment_duration (with grace if good scene)
        if source_duration <= 60:
            return min(max_with_grace, source_duration)

        # Very long videos (>60s): apply proportional limit (20% of source)
        # but never less than max_segment_duration
        proportional = source_duration * 0.20
        return max(self.max_segment_duration, min(proportional, max_with_grace))

    def _run_audio_content_analysis(
        self,
        audio_video: Path,
        video_duration: float,
    ) -> AudioAnalysisResult | None:
        """Run audio content analysis and log results.

        Args:
            audio_video: Path to video for audio analysis.
            video_duration: Total video duration.

        Returns:
            AudioAnalysisResult or None if disabled/failed.
        """
        if not self.audio_content_enabled:
            return None

        logger.info("Step 1c: Analyzing audio content (laughter, speech, etc.)")
        result = self._analyze_audio_content(audio_video, video_duration)
        if not result:
            return None

        logger.info(
            f"  -> Audio score: {result.audio_score:.2f}, "
            f"laughter: {result.has_laughter}, "
            f"speech: {result.has_speech}, "
            f"protected_ranges: {len(result.protected_ranges)}"
        )

        for i, (start, end) in enumerate(result.protected_ranges[:5]):
            logger.info(
                f"     Protected range {i + 1}: {start:.2f}s - {end:.2f}s (duration: {end - start:.2f}s)"
            )

        total_protected = sum(end - start for start, end in result.protected_ranges)
        speech_coverage = total_protected / video_duration if video_duration > 0 else 0
        if speech_coverage > 0.8:
            logger.warning(
                f"  ⚠️ High speech coverage: {speech_coverage:.0%} of video is speech/laughter. "
                "May be difficult to find clean cut points."
            )

        self._log_speech_at_video_end(result, video_duration)
        return result

    def _log_speech_at_video_end(self, result: AudioAnalysisResult, video_duration: float) -> None:
        """Log informational note if speech extends to video end."""
        if not result.protected_ranges:
            return

        last_range_end = max(end for _, end in result.protected_ranges)
        if abs(last_range_end - video_duration) < 0.1:
            last_range = max(result.protected_ranges, key=operator.itemgetter(1))
            if last_range[1] - last_range[0] > 1.0:
                logger.info(
                    f"  ℹ️ Speech detected at video end ({last_range[0]:.1f}s-{last_range_end:.1f}s). "
                    "Segment boundaries will be adjusted."
                )

    def _fix_boundary_in_range(
        self,
        value: float,
        label: str,
        range_start: float,
        range_end: float,
        clamp_low: float,
        clamp_high: float,
        nudge: float = 0.05,
    ) -> tuple[float, bool, bool]:
        """Nudge a boundary value out of a protected range.

        Returns (new_value, was_adjusted, was_unfixable).
        """
        if not (range_start <= value < range_end):
            return value, False, False
        if label == "START":
            candidate = max(clamp_low, range_start - nudge)
        else:
            candidate = min(clamp_high, range_end + nudge)
        if abs(candidate - value) > 0.01:
            logger.warning(
                f"  Fixed: Segment {label} {value:.2f}s was cutting through "
                f"protected range {range_start:.2f}s-{range_end:.2f}s, moved to {candidate:.2f}s"
            )
            return candidate, True, False
        logger.warning(
            f"  Cannot fix: Segment {label} {value:.2f}s cuts through "
            f"protected range {range_start:.2f}s-{range_end:.2f}s (at video boundary)"
        )
        return value, False, True

    def _fix_best_segment_boundaries(
        self,
        best: ScoredSegment,
        audio_content_result: AudioAnalysisResult,
        video_duration: float,
    ) -> None:
        """Fix best segment boundaries that cut through protected audio ranges.

        Modifies the segment in place.

        Args:
            best: Best segment to fix.
            audio_content_result: Audio analysis results.
            video_duration: Total video duration.
        """
        adjusted = False
        unfixable = False

        for range_start, range_end in audio_content_result.protected_ranges:
            new_start, adj, unfix = self._fix_boundary_in_range(
                best.start_time, "START", range_start, range_end, 0, video_duration
            )
            if adj:
                best.start_time = new_start
            adjusted = adjusted or adj
            unfixable = unfixable or unfix

            new_end, adj, unfix = self._fix_boundary_in_range(
                best.end_time, "END", range_start, range_end, 0, video_duration
            )
            if adj:
                best.end_time = new_end
            adjusted = adjusted or adj
            unfixable = unfixable or unfix

        if adjusted:
            logger.info(f"  -> Adjusted best segment: {best.start_time:.1f}s-{best.end_time:.1f}s")
        if unfixable:
            logger.warning(
                "  -> Some cuts through speech could not be fixed (segment at video boundary)"
            )

        proportional_max = self._get_max_segment_for_source(video_duration)
        final_duration = best.end_time - best.start_time
        if final_duration > proportional_max:
            best.end_time = best.start_time + proportional_max
            logger.info(
                f"  -> Re-trimmed to proportional max: {best.start_time:.1f}s-{best.end_time:.1f}s "
                f"(was {final_duration:.1f}s, max={proportional_max:.1f}s for {video_duration:.1f}s source)"
            )

    def analyze(
        self,
        video_path: Path,
        video_duration: float | None = None,
        audio_video_path: Path | None = None,
    ) -> list[ScoredSegment]:
        """Analyze a video and return scored segments.

        This is the main entry point. It detects boundaries, generates
        candidate segments, scores them, and returns sorted by score.

        Args:
            video_path: Path to the video file (can be downscaled for visual analysis).
            video_duration: Optional video duration (detected if not provided).
            audio_video_path: Optional separate path for audio analysis (original video).
                             If not provided, uses video_path for both.

        Returns:
            List of ScoredSegment sorted by total_score (best first).
            Empty list if analysis fails.
        """
        video_path = Path(video_path)
        if not video_path.exists():
            logger.error(f"Video not found: {video_path}")
            return []

        visual_video = video_path
        audio_video = Path(audio_video_path) if audio_video_path else video_path

        if video_duration is None:
            video_info = get_video_info(video_path)
            video_duration = video_info.get("duration", 0)

        if video_duration <= 0:
            logger.error(f"Invalid video duration: {video_duration}")
            return []

        MIN_VIDEO_DURATION = 1.5
        if video_duration < MIN_VIDEO_DURATION:
            logger.warning(
                f"Video too short ({video_duration:.1f}s < {MIN_VIDEO_DURATION}s), skipping"
            )
            return []

        dynamic_optimal = self._get_dynamic_optimal_duration(video_duration)
        logger.info(
            f"Duration scoring: source={video_duration:.1f}s → "
            f"optimal clip={dynamic_optimal:.1f}s "
            f"(target {self.target_extraction_ratio * 100:.0f}% of source, "
            f"range {self.min_segment_duration:.1f}s-{self.max_segment_duration:.1f}s)"
        )

        # Step 1: Detect boundaries
        logger.info(f"Step 1a: Detecting visual scene boundaries from {visual_video.name}")
        visual_boundaries = self._detect_visual_boundaries(visual_video)
        logger.info(f"  -> Found {len(visual_boundaries)} visual boundaries")

        logger.info(f"Step 1b: Detecting audio/silence boundaries from {audio_video.name}")
        audio_boundaries = self._detect_audio_boundaries(audio_video)
        logger.info(f"  -> Found {len(audio_boundaries)} audio boundaries (silence gaps)")

        audio_content_result = self._run_audio_content_analysis(audio_video, video_duration)

        # Step 2: Merge boundaries
        logger.info("Step 2: Merging visual + audio boundaries into cut points")
        cut_points = self._merge_boundaries(visual_boundaries, audio_boundaries, video_duration)
        priority_2_count = sum(1 for cp in cut_points if cp.priority == 2)
        logger.info(
            f"  -> {len(cut_points)} cut points ({priority_2_count} ideal = both visual+audio)"
        )

        # Step 3: Generate candidates
        logger.info("Step 3: Generating candidate segments (must start/end on silence)")
        candidates = self._generate_candidate_segments(cut_points, video_duration)
        if not candidates:
            logger.warning("No valid segments found, using fallback (visual-only)")
            candidates = self._generate_fallback_segments(video_duration, cut_points)
        logger.info(f"  -> Generated {len(candidates)} candidate segments")

        # Step 3b: Adjust for audio
        candidates = self._step3b_adjust_for_audio(candidates, audio_content_result, video_duration)

        # Step 4: Score
        logger.info(
            f"Step 4a: Visual scoring {len(candidates)} candidates (faces, motion, stability, duration)"
        )
        scored_segments = self._score_segments_visual_only(
            visual_video, candidates, cut_points, audio_content_result, video_duration
        )
        scored_segments.sort(key=lambda s: s.total_score, reverse=True)

        self._run_llm_scoring(scored_segments, audio_video)

        # Step 5: Fix best segment
        if scored_segments:
            best = scored_segments[0]
            logger.info(
                f"Step 5: Best segment {best.start_time:.1f}s-{best.end_time:.1f}s "
                f"(score={best.total_score:.2f}, cut_quality={best.cut_quality:.0%})"
            )
            if audio_content_result and audio_content_result.protected_ranges:
                self._fix_best_segment_boundaries(best, audio_content_result, video_duration)

        return scored_segments

    def _step3b_adjust_for_audio(
        self,
        candidates: list,
        audio_content_result: AudioAnalysisResult | None,
        video_duration: float,
    ) -> list:
        """Run step 3b: adjust candidate boundaries to avoid protected audio ranges."""
        if audio_content_result and audio_content_result.protected_ranges:
            logger.info("Step 3b: Adjusting boundaries to avoid cutting mid-laugh/speech")
            original_count = len(candidates)
            candidates = self._adjust_candidates_for_audio(
                candidates, audio_content_result, video_duration
            )
            logger.info(
                f"  -> Adjusted {original_count} candidates to {len(candidates)} candidates"
            )
            if candidates:
                sample = candidates[0]
                logger.info(f"     Example segment: {sample[0].time:.2f}s - {sample[1].time:.2f}s")
        elif audio_content_result:
            logger.info(
                "Step 3b: SKIPPED - no protected ranges to avoid "
                f"(detected {len(audio_content_result.events)} audio events, "
                f"but none were speech/laughter above confidence threshold)"
            )
        else:
            logger.debug("Step 3b: SKIPPED - audio content analysis not enabled/available")
        return candidates

    def _analyze_audio_content(
        self, video_path: Path, video_duration: float | None = None
    ) -> AudioAnalysisResult | None:
        """Analyze audio content (laughter, speech, etc.) in a video.

        Args:
            video_path: Path to video file.
            video_duration: Video duration to clamp audio timestamps.

        Returns:
            AudioAnalysisResult or None if analysis fails.
        """
        # Check cache first
        cache_key = str(video_path)
        if cache_key in self._audio_analysis_cache:
            return self._audio_analysis_cache[cache_key]

        try:
            from immich_memories.audio.content_analyzer import AudioContentAnalyzer
            from immich_memories.config import get_config

            if self._audio_analyzer is None:
                config = get_config()
                self._audio_analyzer = AudioContentAnalyzer(
                    use_panns=config.audio_content.use_panns,
                    min_confidence=config.audio_content.min_confidence,
                    laughter_confidence=config.audio_content.laughter_confidence,
                )

            result = self._audio_analyzer.analyze(video_path, video_duration)
            self._audio_analysis_cache[cache_key] = result
            return result

        except Exception as e:
            logger.warning(f"Audio content analysis failed: {e}")
            return None
