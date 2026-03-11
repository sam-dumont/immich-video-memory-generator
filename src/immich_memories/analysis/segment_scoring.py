"""Segment scoring and ranking logic for video analysis.

This module contains scoring methods for video segments, including
visual scoring, audio content scoring, LLM content scoring, duration
preference scoring, and total score computation.

Extracted from unified_analyzer.py for maintainability.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.analysis.scenes import Scene
from immich_memories.analysis.scoring import SceneScorer

if TYPE_CHECKING:
    from immich_memories.analysis.content_analyzer import ContentAnalyzer
    from immich_memories.audio.content_analyzer import AudioAnalysisResult

logger = logging.getLogger(__name__)


class SegmentScoringMixin:
    """Mixin providing segment scoring and ranking methods.

    This mixin is used by UnifiedSegmentAnalyzer to keep scoring logic
    separate from boundary detection and candidate generation logic.

    Expects the following attributes on the host class:
        scorer: SceneScorer
        content_analyzer: ContentAnalyzer | None
        content_weight: float
        audio_content_enabled: bool
        audio_content_weight: float
        min_segment_duration: float
        max_segment_duration: float
        optimal_clip_duration: float
        max_optimal_duration: float
        target_extraction_ratio: float
        duration_weight: float
    """

    # These are declared for type checking purposes only; actual values
    # are set by the host class __init__.
    scorer: SceneScorer
    content_analyzer: ContentAnalyzer | None
    content_weight: float
    audio_content_enabled: bool
    audio_content_weight: float
    min_segment_duration: float
    max_segment_duration: float
    optimal_clip_duration: float
    max_optimal_duration: float
    target_extraction_ratio: float
    duration_weight: float

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
        import numpy as np

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

    def _score_segment_audio(
        self,
        start_time: float,
        end_time: float,
        audio_result: AudioAnalysisResult,
    ) -> dict:
        """Score a specific segment based on audio content analysis.

        Args:
            start_time: Segment start time.
            end_time: Segment end time.
            audio_result: Full video audio analysis result.

        Returns:
            Dict with score, has_laughter, has_speech, has_music, audio_categories.
        """
        from immich_memories.audio.audio_models import classify_audio_event

        # Find events that overlap with this segment
        segment_events = []
        for event in audio_result.events:
            # Check if event overlaps with segment
            if event.end_time > start_time and event.start_time < end_time:
                # Calculate overlap
                overlap_start = max(event.start_time, start_time)
                overlap_end = min(event.end_time, end_time)
                overlap_duration = overlap_end - overlap_start

                if overlap_duration > 0:
                    segment_events.append((event, overlap_duration))

        if not segment_events:
            return {
                "score": 0.5,  # Neutral score
                "has_laughter": False,
                "has_speech": False,
                "has_music": False,
                "audio_categories": set(),
            }

        # Calculate weighted score based on overlapping events
        total_weighted = 0.0
        total_duration = 0.0
        has_laughter = False
        has_speech = False
        has_music = False
        categories: set[str] = set()

        for event, duration in segment_events:
            weight = event.weight * event.confidence
            total_weighted += weight * duration
            total_duration += duration

            # Classify into high-level category
            cat = classify_audio_event(event.event_class)
            if cat:
                categories.add(cat)

            # Legacy boolean flags
            event_lower = event.event_class.lower()
            if "laugh" in event_lower or "giggle" in event_lower:
                has_laughter = True
            if "speech" in event_lower or "talk" in event_lower:
                has_speech = True
            if "music" in event_lower:
                has_music = True

        segment_duration = end_time - start_time
        if segment_duration > 0 and total_duration > 0:
            # Score based on event coverage and quality
            coverage = total_duration / segment_duration
            quality = total_weighted / total_duration
            score = quality * min(1.0, coverage)
        else:
            score = 0.5

        return {
            "score": min(1.0, max(0.0, score)),
            "has_laughter": has_laughter,
            "has_speech": has_speech,
            "has_music": has_music,
            "audio_categories": categories,
        }

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
        import gc

        from immich_memories.analysis.unified_analyzer import ScoredSegment

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
                audio_score_info = self._score_segment_audio(
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

    def _score_segments(
        self,
        video_path: Path,
        candidates: list[tuple],
        _all_cut_points: list,
    ) -> list:
        """Score candidate segments using visual and optional content analysis.

        DEPRECATED: Use _score_segments_visual_only + separate LLM scoring.

        Args:
            video_path: Path to video file.
            candidates: List of (start, end) cut point pairs.
            _all_cut_points: All available cut points (for context).

        Returns:
            List of ScoredSegment with scores populated.
        """
        from immich_memories.analysis.unified_analyzer import ScoredSegment

        scored = []

        for start_cp, end_cp in candidates:
            segment = ScoredSegment(
                start_time=start_cp.time,
                end_time=end_cp.time,
                start_cut_priority=start_cp.priority,
                end_cut_priority=end_cp.priority,
            )

            # Score using visual analysis
            try:
                visual_scores = self._score_visual(video_path, segment.start_time, segment.end_time)
                segment.face_score = visual_scores.get("face", 0.0)
                segment.motion_score = visual_scores.get("motion", 0.0)
                segment.stability_score = visual_scores.get("stability", 0.0)
                segment.visual_score = visual_scores.get("total", 0.0)
            except Exception as e:
                logger.warning(f"Visual scoring failed: {e}")
                segment.visual_score = 0.5  # Neutral fallback

            # Score using content analysis (if enabled) - SLOW!
            if self.content_analyzer and self.content_weight > 0:
                try:
                    segment.content_score = self._score_content(
                        video_path, segment.start_time, segment.end_time, segment=segment
                    )
                except Exception as e:
                    logger.debug(f"Content scoring skipped: {e}")
                    segment.content_score = 0.5  # Neutral fallback

            # Compute total score
            segment.total_score = self._compute_total_score(segment)
            scored.append(segment)

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
