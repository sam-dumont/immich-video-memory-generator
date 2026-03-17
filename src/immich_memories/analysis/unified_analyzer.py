"""Unified video segment analysis with audio-aware boundaries.

This module provides audio-aware video segment analysis that ensures cuts
happen during silence gaps rather than mid-sentence. It combines visual
scene detection with audio analysis to find natural cut points.
"""

from __future__ import annotations

import gc
import logging
import operator
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from immich_memories.analysis.analyzer_factory import (  # noqa: F401
    create_unified_analyzer_from_config,
)
from immich_memories.analysis.analyzer_models import CutPoint, ScoredSegment  # noqa: F401
from immich_memories.analysis.scenes import Scene, SceneDetector, get_video_info
from immich_memories.analysis.scoring import SceneScorer
from immich_memories.analysis.segment_generation import (
    adjust_candidates_for_audio,
    detect_audio_boundaries,
    detect_visual_boundaries,
    generate_candidate_segments,
    generate_fallback_segments,
    merge_boundaries,
    score_segment_audio,
)

if TYPE_CHECKING:
    from immich_memories.analysis.content_analyzer import ContentAnalyzer
    from immich_memories.audio.audio_models import AudioAnalysisResult
    from immich_memories.config_models import AudioContentConfig

logger = logging.getLogger(__name__)


class UnifiedSegmentAnalyzer:
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
        audio_content_config: AudioContentConfig | None = None,
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
            audio_content_config: AudioContentConfig for lazy audio analyzer init.
                                  Falls back to get_config().
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
        self._audio_content_config = audio_content_config

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
        visual_boundaries = detect_visual_boundaries(visual_video, self._scene_detector)
        logger.info(f"  -> Found {len(visual_boundaries)} visual boundaries")

        logger.info(f"Step 1b: Detecting audio/silence boundaries from {audio_video.name}")
        audio_boundaries = detect_audio_boundaries(
            audio_video, self.silence_threshold_db, self.min_silence_duration
        )
        logger.info(f"  -> Found {len(audio_boundaries)} audio boundaries (silence gaps)")

        audio_content_result = self._run_audio_content_analysis(audio_video, video_duration)

        # Step 2: Merge boundaries
        logger.info("Step 2: Merging visual + audio boundaries into cut points")
        cut_points = merge_boundaries(
            visual_boundaries, audio_boundaries, video_duration, self.cut_point_merge_tolerance
        )
        priority_2_count = sum(1 for cp in cut_points if cp.priority == 2)
        logger.info(
            f"  -> {len(cut_points)} cut points ({priority_2_count} ideal = both visual+audio)"
        )

        # Step 3: Generate candidates
        logger.info("Step 3: Generating candidate segments (must start/end on silence)")
        dynamic_optimal = self._get_dynamic_optimal_duration(video_duration)
        candidates = generate_candidate_segments(
            cut_points,
            video_duration,
            self.min_segment_duration,
            self.max_segment_duration,
            dynamic_optimal,
        )
        if not candidates:
            logger.warning("No valid segments found, using fallback (visual-only)")
            proportional_max = self._get_max_segment_for_source(video_duration)
            candidates = generate_fallback_segments(
                video_duration, cut_points, self.min_segment_duration, proportional_max
            )
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
            proportional_max = self._get_max_segment_for_source(video_duration)
            candidates = adjust_candidates_for_audio(
                candidates,
                audio_content_result,
                video_duration,
                self.min_segment_duration,
                proportional_max,
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

            if self._audio_analyzer is None:
                ac_config = self._audio_content_config
                if ac_config is None:
                    from immich_memories.config import get_config

                    ac_config = get_config().audio_content
                self._audio_analyzer = AudioContentAnalyzer(
                    use_panns=ac_config.use_panns,
                    min_confidence=ac_config.min_confidence,
                    laughter_confidence=ac_config.laughter_confidence,
                )

            result = self._audio_analyzer.analyze(video_path, video_duration)
            self._audio_analysis_cache[cache_key] = result
            return result

        except Exception as e:
            logger.warning(f"Audio content analysis failed: {e}")
            return None

    def _get_dynamic_optimal_duration(self, source_duration: float) -> float:
        """Calculate the optimal clip duration based on source video length.

        For short sources (< 20s): optimal stays at base (5s)
        For longer sources: optimal scales up to max_optimal (10s)

        Args:
            source_duration: Total source video duration in seconds.

        Returns:
            Dynamic optimal clip duration in seconds.
        """
        if source_duration > 20.0:
            return min(
                self.max_optimal_duration,
                max(self.optimal_clip_duration, source_duration * self.target_extraction_ratio),
            )
        return self.optimal_clip_duration

    def _compute_duration_score(self, clip_duration: float, source_duration: float) -> float:
        """Compute duration preference score using a Gaussian curve.

        The score peaks at the dynamic optimal duration and falls off
        smoothly for shorter or longer clips.

        Args:
            clip_duration: Duration of the clip in seconds.
            source_duration: Total source video duration in seconds.

        Returns:
            Score between 0.0 and 1.0, with 1.0 at optimal duration.
        """
        # Clips below minimum duration get heavy penalty
        if clip_duration < self.min_segment_duration:
            return max(0.0, 0.3 * (clip_duration / self.min_segment_duration))

        # Get dynamic optimal for this source
        dynamic_optimal = self._get_dynamic_optimal_duration(source_duration)

        # Gaussian curve centered at dynamic optimal duration
        # sigma scales with optimal to keep curve proportional
        sigma = max(3.0, dynamic_optimal * 0.6)
        diff = clip_duration - dynamic_optimal
        score = float(np.exp(-(diff * diff) / (2 * sigma * sigma)))

        # For very long clips (>15s), add extra penalty
        if clip_duration > 15.0:
            long_penalty = (clip_duration - 15.0) * 0.05
            score = max(0.2, score - long_penalty)

        return score

    def _score_visual(
        self, video_path: Path, start_time: float, end_time: float
    ) -> dict[str, float]:
        """Score a segment using visual analysis (faces, motion, stability).

        Args:
            video_path: Path to video file.
            start_time: Segment start time.
            end_time: Segment end time.

        Returns:
            Dictionary with face, motion, stability, and total scores.
        """
        # Create a temporary Scene object for the scorer
        scene = Scene(
            start_time=start_time,
            end_time=end_time,
            start_frame=0,  # Will be recalculated
            end_frame=0,
        )

        # Use the SceneScorer to get component scores
        moment = self.scorer.score_scene(video_path, scene, sample_frames=5)

        return {
            "face": moment.face_score,
            "motion": moment.motion_score,
            "stability": moment.stability_score,
            "total": (
                moment.face_score * 0.4
                + moment.motion_score * 0.25
                + moment.stability_score * 0.2
                + 0.5 * 0.15  # Audio placeholder
            ),
        }

    def _score_content(
        self,
        video_path: Path,
        start_time: float,
        end_time: float,
        segment=None,
    ) -> float:
        """Score a segment using LLM content analysis.

        Args:
            video_path: Path to video file.
            start_time: Segment start time.
            end_time: Segment end time.
            segment: Optional ScoredSegment to update with full LLM analysis.

        Returns:
            Content score from 0.0 to 1.0.
        """
        if not self.content_analyzer:
            return 0.5

        try:
            analysis = self.content_analyzer.analyze_segment(video_path, start_time, end_time)

            # If segment provided, store full LLM analysis results
            if segment is not None:
                segment.llm_description = analysis.description
                segment.llm_emotion = analysis.emotion
                segment.llm_setting = analysis.setting
                segment.llm_activities = analysis.activities
                segment.llm_subjects = analysis.subjects
                segment.llm_interestingness = analysis.interestingness
                segment.llm_quality = analysis.quality

            return analysis.content_score
        except Exception as e:
            logger.debug(f"Content analysis failed: {e}")
            return 0.5

    def _compute_total_score(self, segment) -> float:
        """Compute the total score for a segment.

        Args:
            segment: ScoredSegment with component scores.

        Returns:
            Total score from 0.0 to ~1.15 (includes cut quality bonus).
        """
        # Distribute weights between visual, content, audio, and duration
        # Total should be 1.0 before bonuses
        total_weight = 1.0
        content_w = self.content_weight
        audio_w = self.audio_content_weight if self.audio_content_enabled else 0.0
        duration_w = self.duration_weight
        visual_w = total_weight - content_w - audio_w - duration_w

        # Ensure weights are non-negative
        if visual_w < 0:
            # Reduce other weights proportionally to make room
            other_total = content_w + audio_w + duration_w
            if other_total > 0:
                scale = 1.0 / other_total
                content_w *= scale
                audio_w *= scale
                duration_w *= scale
            visual_w = 0.0

        base_score = (
            segment.visual_score * visual_w
            + segment.content_score * content_w
            + segment.audio_score * audio_w
            + segment.duration_score * duration_w
        )

        # Significant bonus for high-quality cut points (max 0.15)
        # This ensures we prefer clean audio boundaries over mid-speech cuts
        cut_bonus = segment.cut_quality * 0.15

        # Extra bonus for segments with laughter (highly desirable for memories)
        laughter_bonus = 0.1 if segment.has_laughter else 0.0

        return base_score + cut_bonus + laughter_bonus

    def _score_segments_visual_only(
        self,
        video_path: Path,
        candidates: list[tuple],
        _all_cut_points: list,
        audio_content_result: AudioAnalysisResult | None = None,
        video_duration: float | None = None,
    ) -> list:
        """Score candidate segments using visual analysis only (fast).

        LLM content analysis is done separately on top candidates only.

        Args:
            video_path: Path to video file.
            candidates: List of (start, end) cut point pairs.
            _all_cut_points: All available cut points (for context).
            audio_content_result: Optional audio content analysis results.
            video_duration: Total video duration for duration scoring.

        Returns:
            List of ScoredSegment with visual scores populated.
        """
        scored = []

        for i, (start_cp, end_cp) in enumerate(candidates):
            segment = ScoredSegment(
                start_time=start_cp.time,
                end_time=end_cp.time,
                start_cut_priority=start_cp.priority,
                end_cut_priority=end_cp.priority,
            )

            # Score using visual analysis only
            try:
                visual_scores = self._score_visual(video_path, segment.start_time, segment.end_time)
                segment.face_score = visual_scores.get("face", 0.0)
                segment.motion_score = visual_scores.get("motion", 0.0)
                segment.stability_score = visual_scores.get("stability", 0.0)
                segment.visual_score = visual_scores.get("total", 0.0)
            except Exception as e:
                logger.warning(f"Visual scoring failed: {e}")
                segment.visual_score = 0.5  # Neutral fallback

            # Score using audio content analysis (if available)
            if audio_content_result and self.audio_content_enabled:
                audio_score_info = score_segment_audio(
                    segment.start_time, segment.end_time, audio_content_result
                )
                segment.audio_score = audio_score_info["score"]
                segment.has_laughter = audio_score_info["has_laughter"]
                segment.has_speech = audio_score_info["has_speech"]
                segment.has_music = audio_score_info["has_music"]
                segment.audio_categories = audio_score_info["audio_categories"]

            # Score duration preference (clips closer to optimal get higher scores)
            if video_duration:
                segment.duration_score = self._compute_duration_score(
                    segment.duration, video_duration
                )

            # Compute total score (visual + audio + duration at this stage)
            segment.total_score = self._compute_total_score(segment)
            scored.append(segment)

            # Memory cleanup every 5 candidates to prevent OOM on long videos
            if (i + 1) % 5 == 0:
                gc.collect()
                logger.debug(f"Memory cleanup after {i + 1}/{len(candidates)} candidates")

        # Final cleanup after all candidates
        # Release cached video capture to free memory
        self.scorer.release_capture()
        gc.collect()
        return scored

    def _run_llm_scoring(
        self,
        scored_segments: list,
        audio_video: Path,
    ) -> None:
        """Run LLM content analysis on top candidates (in-place).

        Args:
            scored_segments: Segments to score (modified in place).
            audio_video: Path to video for content analysis.
        """
        if not (self.content_analyzer and self.content_weight > 0):
            logger.info("  -> LLM content analysis DISABLED")
            return

        top_n = min(5, len(scored_segments))
        logger.info(f"Step 4b: LLM content analysis on TOP {top_n} candidates only")
        for i, segment in enumerate(scored_segments[:top_n]):
            try:
                logger.info(
                    f"  -> Analyzing candidate {i + 1}/{top_n}: {segment.start_time:.1f}s-{segment.end_time:.1f}s"
                )
                segment.content_score = self._score_content(
                    audio_video, segment.start_time, segment.end_time, segment=segment
                )
                segment.total_score = self._compute_total_score(segment)
            except Exception as e:
                logger.warning(f"  -> LLM analysis failed: {e}")
                segment.content_score = 0.5

        scored_segments.sort(key=lambda s: s.total_score, reverse=True)
