"""Unified video segment analysis with audio-aware boundaries.

This module provides audio-aware video segment analysis that ensures cuts
happen during silence gaps rather than mid-sentence. It combines visual
scene detection with audio analysis to find natural cut points.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.analysis.analyzer_factory import (  # noqa: F401
    create_unified_analyzer_from_config,
)
from immich_memories.analysis.analyzer_models import CutPoint, ScoredSegment  # noqa: F401
from immich_memories.analysis.scenes import Scene, SceneDetector, get_video_info
from immich_memories.analysis.scoring import SceneScorer
from immich_memories.analysis.segment_extents import (
    adjust_candidates_for_protected_audio,
    dynamic_optimal_duration,
    max_segment_for_source,
    repair_best_segment,
    safe_cut_gaps,
)
from immich_memories.analysis.segment_generation import (
    detect_audio_boundaries,
    detect_visual_boundaries,
    generate_candidate_segments,
    generate_fallback_segments,
    merge_boundaries,
    score_segment_audio,
)
from immich_memories.analysis.segment_transcription import transcribe_top_segments
from immich_memories.analysis.speech_analysis import AudioAnalyzer, SpeechAnalysisService

if TYPE_CHECKING:
    from immich_memories.analysis.content_analyzer import ContentAnalyzer
    from immich_memories.audio.audio_models import AudioAnalysisResult
    from immich_memories.config_models import (
        AnalysisConfig,
        AudioContentConfig,
        SpeechConfig,
        TranscriptionConfig,
    )

logger = logging.getLogger(__name__)


def log_top_segments(segments: list[ScoredSegment], top_n: int = 5) -> None:
    """Log per-factor score breakdown for the top-N segments."""
    for i, seg in enumerate(segments[:top_n]):
        has_llm = "LLM" if seg.llm_description else "no-LLM"
        logger.info(
            f"  #{i + 1} {seg.start_time:.1f}s-{seg.end_time:.1f}s "
            f"total={seg.total_score:.3f} ({has_llm}) | "
            f"face={seg.face_score:.2f} motion={seg.motion_score:.2f} "
            f"stability={seg.stability_score:.2f} content={seg.content_score:.2f} "
            f"duration={seg.duration_score:.2f} visual={seg.visual_score:.2f} "
            f"cut_q={seg.cut_quality:.2f}"
        )


@dataclass(frozen=True)
class _VisualScores:
    """One segment's visual scores plus the face geometry behind them."""

    face: float
    motion: float
    stability: float
    total: float
    face_positions: list[tuple[float, float]] | None = None


# What a flawless photo scores once the default photo penalty is applied.
_PHOTO_CEILING = 0.8
# The base a video must reach before its bonuses may carry it past that.
_VIDEO_ADVANTAGE_BASE = 0.7


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
        scorer: SceneScorer,
        content_analyzer: ContentAnalyzer | None = None,
        min_segment_duration: float = 2.0,
        max_segment_duration: float = 15.0,
        # -40.0, not -30.0: this argument was always passed from config, so -40.0
        # is the threshold that runs. The constructor default was stale.
        silence_threshold_db: float = -40.0,
        min_silence_duration: float = 0.3,
        cut_point_merge_tolerance: float = 0.5,
        content_weight: float = 0.0,
        audio_content_enabled: bool = False,
        audio_content_weight: float = 0.15,
        optimal_clip_duration: float = 5.0,
        max_optimal_duration: float = 10.0,
        target_extraction_ratio: float = 0.15,
        duration_weight: float = 0.15,
        audio_analyzer: AudioAnalyzer | None = None,
        *,
        audio_content_config: AudioContentConfig,
        analysis_config: AnalysisConfig,
        speech_config: SpeechConfig | None = None,
        speech_analysis: SpeechAnalysisService | None = None,
        transcription_config: TranscriptionConfig | None = None,
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
            speech_config: SpeechConfig controlling VAD-derived protected ranges.
            speech_analysis: Injected SpeechAnalysisService, or None to build one
                from audio_content_config/speech_config/audio_content_enabled/audio_analyzer.
        """
        self.scorer = scorer
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
        self._analysis_config = analysis_config
        self._laughter_bonus = audio_content_config.laughter_bonus
        self._speech_analysis = speech_analysis or SpeechAnalysisService(
            audio_content_config=audio_content_config,
            speech_config=speech_config,
            audio_content_enabled=audio_content_enabled,
            audio_analyzer=audio_analyzer,
            transcription_config=transcription_config,
        )

        self._scene_detector = SceneDetector(analysis_config=analysis_config)

    def clear_cache(self, release_audio_analyzer: bool = False):
        """Clear internal caches to free memory.

        Args:
            release_audio_analyzer: If True, also release the audio analyzer.
                Usually False because the analyzer is shared across clips.
        """
        self._speech_analysis.clear_cache(release_audio_analyzer=release_audio_analyzer)

    def reset_for_video(self) -> None:
        """Release current-video state while retaining reusable configuration and models."""
        self._speech_analysis.reset_for_video()
        self.scorer.release_capture()

    def analyze(
        self,
        video_path: Path,
        video_duration: float | None = None,
        audio_video_path: Path | None = None,
        *,
        enable_content_analysis: bool = True,
        enable_audio_content_analysis: bool = True,
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

        dynamic_optimal = dynamic_optimal_duration(
            video_duration,
            self.optimal_clip_duration,
            self.max_optimal_duration,
            self.target_extraction_ratio,
        )
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

        audio_content_enabled, audio_content_result = (
            self._speech_analysis.get_audio_content_result(
                audio_video,
                video_duration,
                enable_audio_content_analysis,
            )
        )

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
        dynamic_optimal = dynamic_optimal_duration(
            video_duration,
            self.optimal_clip_duration,
            self.max_optimal_duration,
            self.target_extraction_ratio,
        )
        candidates = generate_candidate_segments(
            cut_points,
            video_duration,
            self.min_segment_duration,
            self.max_segment_duration,
            dynamic_optimal,
        )
        if not candidates:
            logger.warning("No valid segments found, using fallback (visual-only)")
            proportional_max = max_segment_for_source(video_duration, self.max_segment_duration)
            candidates = generate_fallback_segments(
                video_duration, cut_points, self.min_segment_duration, proportional_max
            )
        logger.info(f"  -> Generated {len(candidates)} candidate segments")

        # Step 3b: Adjust for audio
        candidates = adjust_candidates_for_protected_audio(
            candidates,
            audio_content_result,
            video_duration,
            self.min_segment_duration,
            self.max_segment_duration,
            self._speech_analysis.speech_config.min_silence_ms,
        )

        # Step 4: Score
        logger.info(
            f"Step 4a: Visual scoring {len(candidates)} candidates (faces, motion, stability, duration)"
        )
        # One decision for the whole scoring flow. Audio participates only when
        # analysis actually produced something; enabled-but-absent is the same as
        # off, and must stay the same in both the initial pass and the LLM
        # rescoring of the top candidates, or the top 5 get scored on a different
        # basis from everything they are ranked against.
        audio_available = audio_content_enabled and bool(audio_content_result)

        scored_segments = self._score_segments_visual_only(
            visual_video,
            candidates,
            cut_points,
            audio_content_result,
            video_duration,
            enable_content_analysis=enable_content_analysis,
            enable_audio_content_analysis=audio_available,
        )
        scored_segments.sort(key=lambda s: s.total_score, reverse=True)

        # Step 4a-half: independent of content analysis by design.
        transcribe_top_segments(self._speech_analysis, scored_segments, audio_video)

        if enable_content_analysis:
            self._run_llm_scoring(
                scored_segments,
                visual_video,
                enable_audio_content_analysis=audio_available,
            )

        if scored_segments:
            log_top_segments(scored_segments, top_n=min(5, len(scored_segments)))

        # Step 5: Fix best segment
        if scored_segments:
            best = scored_segments[0]
            logger.info(
                f"Step 5: Best segment {best.start_time:.1f}s-{best.end_time:.1f}s "
                f"(score={best.total_score:.2f}, cut_quality={best.cut_quality:.0%})"
            )
            min_silence_ms = self._speech_analysis.speech_config.min_silence_ms
            if audio_content_result and audio_content_result.protected_ranges:
                repair_best_segment(
                    best,
                    audio_content_result,
                    video_duration,
                    self.min_segment_duration,
                    self.max_segment_duration,
                    min_silence_ms,
                )
            elif audio_content_result:
                best.safe_cut_gaps = safe_cut_gaps(
                    audio_content_result, video_duration, min_silence_ms
                )

        return scored_segments

    def _compute_duration_score(self, clip_duration: float, source_duration: float) -> float:
        """How well this clip length suits this source, on the shared curve.

        The rule lives in analysis.scoring and is called from there by the
        other scoring path too. It was written out a second time here, and the
        two agreed exactly — which is luck, not a guarantee: tuning one would
        have left the other scoring the old way, and which rule a clip met
        would have depended on the path it arrived by.
        """
        from immich_memories.analysis.scoring import compute_duration_score

        return compute_duration_score(
            clip_duration,
            source_duration,
            self.optimal_clip_duration,
            self.max_optimal_duration,
            self.target_extraction_ratio,
            self.min_segment_duration,
        )

    def _score_visual(self, video_path: Path, start_time: float, end_time: float) -> _VisualScores:
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

        # Use scorer's own weights for visual sub-components (exclude audio —
        # audio scoring is handled independently by PANNs via audio_content_weight)
        s = self.scorer
        visual_weights = s.face_weight + s.motion_weight + s.stability_weight
        if visual_weights > 0:
            total = (
                moment.face_score * s.face_weight
                + moment.motion_score * s.motion_weight
                + moment.stability_score * s.stability_weight
            ) / visual_weights
        else:
            total = 0.0

        return _VisualScores(
            face=moment.face_score,
            motion=moment.motion_score,
            stability=moment.stability_score,
            total=total,
            # WHY carried through: score_scene computes face geometry and this
            # returned only the floats, so processing/transforms.py — which
            # takes face_positions for framing — was fed nothing (#483).
            face_positions=moment.face_positions,
        )

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
            analysis = self.content_analyzer.analyze_segment(
                video_path,
                start_time,
                end_time,
                transcript=getattr(segment, "transcript", None),
            )

            raw_confidence = getattr(analysis, "confidence", 0.0)
            confidence = float(raw_confidence) if isinstance(raw_confidence, (int, float)) else 0.0
            if segment is not None:
                segment.llm_confidence = confidence

            raw_min_confidence = getattr(self.scorer, "content_min_confidence", 0.5)
            min_confidence = (
                float(raw_min_confidence) if isinstance(raw_min_confidence, (int, float)) else 0.5
            )
            if confidence < min_confidence:
                return 0.5

            # If segment provided, store full LLM analysis results
            if segment is not None:
                segment.llm_description = analysis.description
                segment.llm_category = analysis.category
                segment.llm_emotion = analysis.emotion
                segment.llm_setting = analysis.setting
                segment.llm_subjects = analysis.subjects
                segment.llm_activities = analysis.activities
                segment.llm_interestingness = analysis.interestingness
                segment.llm_quality = analysis.quality

            return analysis.content_score
        except (RuntimeError, ValueError, OSError) as e:
            logger.debug(f"Content analysis failed: {e}")
            return 0.5

    def _compute_total_score(
        self,
        segment,
        *,
        enable_content_analysis: bool = True,
        enable_audio_content_analysis: bool | None = None,
    ) -> float:
        """Compute the total score for a segment.

        LLM content analysis is additive: it can only boost the base score,
        never dilute it. A neutral LLM score (0.5) adds nothing. Scores above
        0.5 add a bonus proportional to content_weight.

        Args:
            segment: ScoredSegment with component scores.

        Returns:
            Total score (base 0-1 + bonuses up to ~0.25).
        """
        # Base score: visual + audio + duration always get the full 1.0 budget
        # LLM content does NOT compete for weight — it's a bonus on top
        audio_enabled = (
            self.audio_content_enabled
            if enable_audio_content_analysis is None
            else enable_audio_content_analysis
        )
        audio_w = self.audio_content_weight if audio_enabled else 0.0
        duration_w = self.duration_weight
        visual_w = 1.0 - audio_w - duration_w

        if visual_w < 0:
            total = audio_w + duration_w
            if total > 0:
                scale = 1.0 / total
                audio_w *= scale
                duration_w *= scale
            visual_w = 0.0

        base_score = (
            segment.visual_score * visual_w
            + segment.audio_score * audio_w
            + segment.duration_score * duration_w
        )

        # LLM bonus: only scores above neutral (0.5) add signal.
        # content_score=0.5 → +0.0, content_score=1.0 → +content_weight
        #
        # The one-way clamp looks like it suppresses negative evidence, and it does
        # not: measured over 1879 scored segments, exactly 3 sit below 0.5. The
        # model effectively never says a clip is bad. Making this two-sided would
        # move three clips and nothing else.
        #
        # The signal itself is what is weak. Across the same segments the range is
        # 0.38-0.92 with a 0.76 median, and by the model's own category, objects
        # score *higher* than people (0.72 vs 0.70). Ranking on it cannot separate
        # a memory from a lawnmower, which is why subject_policy classifies rather
        # than scores. Do not "fix" the clamp expecting selection to improve.
        llm_bonus = 0.0
        if enable_content_analysis and self.content_weight > 0:
            llm_bonus = max(0.0, (segment.content_score - 0.5)) * self.content_weight * 2

        # Significant bonus for high-quality cut points (max 0.15)
        cut_bonus = segment.cut_quality * 0.15

        # Extra bonus for segments with laughter (highly desirable for memories)
        laughter_bonus = self._laughter_bonus if segment.has_laughter else 0.0

        total = base_score + llm_bonus + cut_bonus + laughter_bonus

        # Video is meant to outrank photo — a perfect photo reaches 0.80 after
        # its penalty, and video is allowed past 1.0 so an equally good video
        # wins. The advantage has to be earned by the footage itself, though.
        # Paid flat, a mediocre clip collected the same bonuses as a great one:
        # a 0.65 video reached 0.90 and beat a 0.85 photo that was plainly
        # better. Below the bar, bonuses may still order videos against each
        # other but cannot lift one past what a photo can reach.
        if base_score < _VIDEO_ADVANTAGE_BASE:
            return min(total, _PHOTO_CEILING)
        return total

    def _score_segments_visual_only(
        self,
        video_path: Path,
        candidates: list[tuple],
        _all_cut_points: list,
        audio_content_result: AudioAnalysisResult | None = None,
        video_duration: float | None = None,
        *,
        enable_content_analysis: bool = True,
        enable_audio_content_analysis: bool = True,
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

        for start_cp, end_cp in candidates:
            segment = ScoredSegment(
                start_time=start_cp.time,
                end_time=end_cp.time,
                start_cut_priority=start_cp.priority,
                end_cut_priority=end_cp.priority,
            )

            # Score using visual analysis only
            try:
                visual = self._score_visual(video_path, segment.start_time, segment.end_time)
                segment.face_score = visual.face
                segment.motion_score = visual.motion
                segment.stability_score = visual.stability
                segment.visual_score = visual.total
                segment.face_positions = visual.face_positions
            except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, TypeError) as e:
                logger.warning(f"Visual scoring failed: {e}")
                # Not 0.5: the ceiling for a segment with no faces is exactly
                # (motion_weight + stability_weight) / visual_weight = 0.500, so a
                # neutral fallback outranked every genuinely-scored landscape shot.
                segment.visual_score = 0.0

            # Audio only votes when it actually has something to say. Leaving the
            # ScoredSegment default of 0.5 in place while still applying
            # audio_content_weight meant a video whose audio analysis failed
            # outscored one with real speech, which measures around 0.32.
            audio_available = audio_content_result is not None and enable_audio_content_analysis
            if audio_content_result is not None and audio_available:
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
            segment.total_score = self._compute_total_score(
                segment,
                enable_content_analysis=enable_content_analysis,
                enable_audio_content_analysis=audio_available,
            )
            scored.append(segment)

        # Direct UnifiedSegmentAnalyzer callers have no ClipAnalyzer lifecycle.
        self.scorer.release_capture()
        return scored

    def _run_llm_scoring(
        self,
        scored_segments: list,
        visual_video: Path,
        *,
        enable_audio_content_analysis: bool = True,
    ) -> None:
        """Run LLM content analysis on top candidates (in-place).

        Args:
            scored_segments: Segments to score (modified in place).
            visual_video: Video to decode frames from -- the 480p analysis
                proxy, not the original. The provider resizes frames to 480px
                regardless, so decoding 4K here is pure cost.
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
                    visual_video, segment.start_time, segment.end_time, segment=segment
                )
                segment.total_score = self._compute_total_score(
                    segment,
                    enable_audio_content_analysis=enable_audio_content_analysis,
                )
            except (RuntimeError, ValueError, OSError) as e:
                logger.warning(f"  -> LLM analysis failed: {e}")
                segment.content_score = 0.5

        scored_segments.sort(key=lambda s: s.total_score, reverse=True)
