"""Unified video segment analysis with audio-aware boundaries.

This module provides audio-aware video segment analysis that ensures cuts
happen during silence gaps rather than mid-sentence. It combines visual
scene detection with audio analysis to find natural cut points.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.analysis.scenes import Scene, SceneDetector, get_video_info
from immich_memories.analysis.scoring import MomentScore, SceneScorer
from immich_memories.analysis.silence_detection import detect_silence_gaps

if TYPE_CHECKING:
    from immich_memories.analysis.content_analyzer import ContentAnalyzer
    from immich_memories.audio.content_analyzer import AudioAnalysisResult

logger = logging.getLogger(__name__)


@dataclass
class CutPoint:
    """A potential cut point in a video.

    Cut points can be visual (scene change), audio (silence gap boundary),
    or both. Points that are both visual and audio are ideal cut locations.
    """

    time: float
    is_visual: bool = False  # From PySceneDetect
    is_audio: bool = False  # From silence detection

    @property
    def priority(self) -> int:
        """Get cut point priority.

        Returns:
            2 = both visual+audio (ideal), 1 = one type, 0 = neither
        """
        return int(self.is_visual) + int(self.is_audio)

    def __lt__(self, other: CutPoint) -> bool:
        """Enable sorting by time."""
        return self.time < other.time


@dataclass
class ScoredSegment:
    """A scored video segment with timing and quality metrics."""

    start_time: float
    end_time: float
    visual_score: float = 0.0  # Combined face, motion, stability
    content_score: float = 0.5  # LLM analysis (default neutral, not 0)
    audio_score: float = 0.5  # Audio content score (laughter, speech, etc.)
    duration_score: float = 0.5  # Duration preference score (peaks at optimal duration)
    total_score: float = 0.0
    start_cut_priority: int = 0  # Priority of start cut point
    end_cut_priority: int = 0  # Priority of end cut point

    # Component scores for debugging/display
    face_score: float = 0.0
    motion_score: float = 0.0
    stability_score: float = 0.0

    # Audio analysis results
    has_laughter: bool = False
    has_speech: bool = False
    has_music: bool = False

    # LLM analysis results (populated by content analyzer)
    llm_description: str | None = None
    llm_emotion: str | None = None
    llm_setting: str | None = None
    llm_activities: list[str] | None = None
    llm_subjects: list[str] | None = None
    llm_interestingness: float | None = None
    llm_quality: float | None = None

    @property
    def duration(self) -> float:
        """Get segment duration in seconds."""
        return self.end_time - self.start_time

    @property
    def cut_quality(self) -> float:
        """Get overall cut quality (0-1 based on cut point priorities)."""
        return (self.start_cut_priority + self.end_cut_priority) / 4.0

    def to_moment_score(self) -> MomentScore:
        """Convert to MomentScore for compatibility with existing code."""
        # Ensure all values are Python floats (not numpy.float64) for SQLite compatibility
        return MomentScore(
            start_time=float(self.start_time),
            end_time=float(self.end_time),
            total_score=float(self.total_score),
            face_score=float(self.face_score),
            motion_score=float(self.motion_score),
            stability_score=float(self.stability_score),
            audio_score=float(self.audio_score),
            content_score=float(self.content_score),
        )


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
        self._audio_analyzer = None
        self._audio_analysis_cache: dict[str, AudioAnalysisResult] = {}

    def clear_cache(self):
        """Clear internal caches to free memory."""
        self._audio_analysis_cache.clear()
        if self._audio_analyzer is not None:
            self._audio_analyzer = None

    def _get_max_segment_for_source(
        self, source_duration: float, has_good_scene: bool = False
    ) -> float:
        """Calculate maximum segment duration based on source video length.

        Logic:
        - If source ≤ max_segment_duration: allow full source (no trimming needed)
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

        # Use separate paths for visual (downscaled) and audio (original) analysis
        visual_video = video_path
        audio_video = Path(audio_video_path) if audio_video_path else video_path

        # Get video duration if not provided
        if video_duration is None:
            video_info = get_video_info(video_path)
            video_duration = video_info.get("duration", 0)

        if video_duration <= 0:
            logger.error(f"Invalid video duration: {video_duration}")
            return []

        # Skip videos that are too short to be useful
        MIN_VIDEO_DURATION = 1.5  # seconds
        if video_duration < MIN_VIDEO_DURATION:
            logger.warning(
                f"Video too short ({video_duration:.1f}s < {MIN_VIDEO_DURATION}s), skipping"
            )
            return []

        # Log duration scoring config
        dynamic_optimal = self._get_dynamic_optimal_duration(video_duration)
        logger.info(
            f"Duration scoring: source={video_duration:.1f}s → "
            f"optimal clip={dynamic_optimal:.1f}s "
            f"(target {self.target_extraction_ratio * 100:.0f}% of source, "
            f"range {self.min_segment_duration:.1f}s-{self.max_segment_duration:.1f}s)"
        )

        # Step 1: Detect all boundaries
        # Use downscaled video for visual detection (faster)
        logger.info(f"Step 1a: Detecting visual scene boundaries from {visual_video.name}")
        visual_boundaries = self._detect_visual_boundaries(visual_video)
        logger.info(f"  -> Found {len(visual_boundaries)} visual boundaries")

        # Use original video for audio detection (needs audio track)
        logger.info(f"Step 1b: Detecting audio/silence boundaries from {audio_video.name}")
        audio_boundaries = self._detect_audio_boundaries(audio_video)
        logger.info(f"  -> Found {len(audio_boundaries)} audio boundaries (silence gaps)")

        # Step 1c: Audio content analysis (laughter, speech detection)
        audio_content_result = None
        if self.audio_content_enabled:
            logger.info("Step 1c: Analyzing audio content (laughter, speech, etc.)")
            audio_content_result = self._analyze_audio_content(audio_video, video_duration)
            if audio_content_result:
                logger.info(
                    f"  -> Audio score: {audio_content_result.audio_score:.2f}, "
                    f"laughter: {audio_content_result.has_laughter}, "
                    f"speech: {audio_content_result.has_speech}, "
                    f"protected_ranges: {len(audio_content_result.protected_ranges)}"
                )
                # Log the actual protected ranges for debugging
                for i, (start, end) in enumerate(audio_content_result.protected_ranges[:5]):
                    logger.info(
                        f"     Protected range {i + 1}: {start:.2f}s - {end:.2f}s (duration: {end - start:.2f}s)"
                    )

                # Calculate speech coverage to warn about problematic videos
                total_protected = sum(
                    end - start for start, end in audio_content_result.protected_ranges
                )
                speech_coverage = total_protected / video_duration if video_duration > 0 else 0
                if speech_coverage > 0.8:
                    logger.warning(
                        f"  ⚠️ High speech coverage: {speech_coverage:.0%} of video is speech/laughter. "
                        "May be difficult to find clean cut points."
                    )

                # Check if speech extends to video end (informational - not necessarily a problem)
                if audio_content_result.protected_ranges:
                    last_range_end = max(end for _, end in audio_content_result.protected_ranges)
                    # Only note if speech is very close to video end (within 0.1s = clamped)
                    if abs(last_range_end - video_duration) < 0.1:
                        # Check if it's a significant speech block (>1s) or just a clamped frame
                        last_range = max(audio_content_result.protected_ranges, key=lambda r: r[1])
                        last_range_duration = last_range[1] - last_range[0]
                        if last_range_duration > 1.0:
                            # Significant speech block at end - worth noting
                            logger.info(
                                f"  ℹ️ Speech detected at video end ({last_range[0]:.1f}s-{last_range_end:.1f}s). "
                                "Segment boundaries will be adjusted."
                            )

        # Step 2: Merge boundaries into cut points
        logger.info("Step 2: Merging visual + audio boundaries into cut points")
        cut_points = self._merge_boundaries(visual_boundaries, audio_boundaries, video_duration)
        priority_2_count = sum(1 for cp in cut_points if cp.priority == 2)
        logger.info(
            f"  -> {len(cut_points)} cut points ({priority_2_count} ideal = both visual+audio)"
        )

        # Step 3: Generate candidate segments
        logger.info("Step 3: Generating candidate segments (must start/end on silence)")
        candidates = self._generate_candidate_segments(cut_points, video_duration)

        if not candidates:
            logger.warning("No valid segments found, using fallback (visual-only)")
            candidates = self._generate_fallback_segments(video_duration, cut_points)

        logger.info(f"  -> Generated {len(candidates)} candidate segments")

        # Step 3b: Adjust segment boundaries to avoid cutting during protected audio events
        if audio_content_result and audio_content_result.protected_ranges:
            logger.info("Step 3b: Adjusting boundaries to avoid cutting mid-laugh/speech")
            original_count = len(candidates)
            candidates = self._adjust_candidates_for_audio(
                candidates, audio_content_result, video_duration
            )
            logger.info(
                f"  -> Adjusted {original_count} candidates to {len(candidates)} candidates"
            )
            # Log a sample of adjusted segments
            if candidates:
                sample = candidates[0]
                logger.info(f"     Example segment: {sample[0].time:.2f}s - {sample[1].time:.2f}s")
        else:
            if audio_content_result:
                logger.info(
                    "Step 3b: SKIPPED - no protected ranges to avoid "
                    f"(detected {len(audio_content_result.events)} audio events, "
                    f"but none were speech/laughter above confidence threshold)"
                )
            else:
                logger.debug("Step 3b: SKIPPED - audio content analysis not enabled/available")

        # Step 4: Score each candidate with VISUAL analysis only (fast)
        logger.info(
            f"Step 4a: Visual scoring {len(candidates)} candidates (faces, motion, stability, duration)"
        )
        scored_segments = self._score_segments_visual_only(
            visual_video, candidates, cut_points, audio_content_result, video_duration
        )

        # Sort by visual score
        scored_segments.sort(key=lambda s: s.total_score, reverse=True)

        # Step 4b: Run LLM on TOP candidates only (slow, so limit to top 5)
        if self.content_analyzer and self.content_weight > 0:
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
                    # Recompute total score with content
                    segment.total_score = self._compute_total_score(segment)
                except Exception as e:
                    logger.warning(f"  -> LLM analysis failed: {e}")
                    segment.content_score = 0.5

            # Re-sort after LLM scoring
            scored_segments.sort(key=lambda s: s.total_score, reverse=True)
        else:
            logger.info("  -> LLM content analysis DISABLED")

        if scored_segments:
            best = scored_segments[0]
            logger.info(
                f"Step 5: Best segment {best.start_time:.1f}s-{best.end_time:.1f}s "
                f"(score={best.total_score:.2f}, cut_quality={best.cut_quality:.0%})"
            )
            # Check if best segment cuts through any protected ranges and FIX it
            if audio_content_result and audio_content_result.protected_ranges:
                adjusted = False
                unfixable = False
                for start, end in audio_content_result.protected_ranges:
                    # Check if segment start cuts through a protected range
                    if start <= best.start_time < end:
                        old_start = best.start_time
                        # Move start to BEFORE the protected range
                        new_start = max(0, start - 0.05)
                        if abs(new_start - old_start) > 0.01:  # Only if actually changed
                            best.start_time = new_start
                            logger.warning(
                                f"  ⚠️ Fixed: Segment START {old_start:.2f}s was cutting through "
                                f"protected range {start:.2f}s-{end:.2f}s, moved to {best.start_time:.2f}s"
                            )
                            adjusted = True
                        else:
                            logger.warning(
                                f"  ⚠️ Cannot fix: Segment START {old_start:.2f}s cuts through "
                                f"protected range {start:.2f}s-{end:.2f}s (at video boundary)"
                            )
                            unfixable = True
                    # Check if segment end cuts through a protected range
                    if start <= best.end_time < end:
                        old_end = best.end_time
                        # Move end to AFTER the protected range
                        new_end = min(video_duration, end + 0.05)
                        if abs(new_end - old_end) > 0.01:  # Only if actually changed
                            best.end_time = new_end
                            logger.warning(
                                f"  ⚠️ Fixed: Segment END {old_end:.2f}s was cutting through "
                                f"protected range {start:.2f}s-{end:.2f}s, moved to {best.end_time:.2f}s"
                            )
                            adjusted = True
                        else:
                            logger.warning(
                                f"  ⚠️ Cannot fix: Segment END {old_end:.2f}s cuts through "
                                f"protected range {start:.2f}s-{end:.2f}s (at video boundary)"
                            )
                            unfixable = True
                if adjusted:
                    logger.info(
                        f"  -> Adjusted best segment: {best.start_time:.1f}s-{best.end_time:.1f}s"
                    )
                if unfixable:
                    logger.warning(
                        "  -> Some cuts through speech could not be fixed (segment at video boundary)"
                    )

                # Re-enforce proportional max AFTER speech adjustment
                # Only trim if segment now exceeds the proportional max
                proportional_max = self._get_max_segment_for_source(video_duration)
                final_duration = best.end_time - best.start_time
                if final_duration > proportional_max:
                    old_end = best.end_time
                    best.end_time = best.start_time + proportional_max
                    logger.info(
                        f"  -> Re-trimmed to proportional max: {best.start_time:.1f}s-{best.end_time:.1f}s "
                        f"(was {final_duration:.1f}s, max={proportional_max:.1f}s for {video_duration:.1f}s source)"
                    )

        return scored_segments

    def _detect_visual_boundaries(self, video_path: Path) -> list[float]:
        """Detect visual scene boundaries using PySceneDetect.

        Args:
            video_path: Path to video file.

        Returns:
            List of boundary timestamps in seconds.
        """
        try:
            scenes = self._scene_detector.detect(video_path, extract_keyframes=False)

            # Extract unique boundary times
            boundaries = set()
            for scene in scenes:
                boundaries.add(scene.start_time)
                boundaries.add(scene.end_time)

            return sorted(boundaries)

        except Exception as e:
            logger.warning(f"Visual scene detection failed: {e}")
            return []

    def _detect_audio_boundaries(self, video_path: Path) -> list[float]:
        """Detect audio boundaries (silence gap edges).

        Args:
            video_path: Path to video file.

        Returns:
            List of boundary timestamps (silence gap start/end points).
        """
        try:
            silence_gaps = detect_silence_gaps(
                video_path,
                threshold_db=self.silence_threshold_db,
                min_silence_duration=self.min_silence_duration,
            )

            # Extract all silence gap boundary times
            boundaries = set()
            for gap_start, gap_end in silence_gaps:
                boundaries.add(gap_start)
                boundaries.add(gap_end)

            sorted_boundaries = sorted(boundaries)
            logger.info(
                f"Audio analysis: found {len(silence_gaps)} silence gaps, "
                f"{len(sorted_boundaries)} boundary points "
                f"(threshold={self.silence_threshold_db}dB, min_duration={self.min_silence_duration}s)"
            )
            return sorted_boundaries

        except Exception as e:
            logger.warning(f"Audio boundary detection failed: {e}")
            return []

    def _merge_boundaries(
        self,
        visual: list[float],
        audio: list[float],
        video_duration: float,
    ) -> list[CutPoint]:
        """Merge visual and audio boundaries into unified cut points.

        Boundaries within the merge tolerance are considered the same point.
        Points that are both visual and audio are marked with higher priority.

        Args:
            visual: Visual boundary timestamps.
            audio: Audio boundary timestamps.
            video_duration: Total video duration.

        Returns:
            List of CutPoint sorted by time.
        """
        # Start with all unique timestamps
        all_times: dict[float, CutPoint] = {}

        # Add visual boundaries
        for t in visual:
            all_times[t] = CutPoint(time=t, is_visual=True, is_audio=False)

        # Add or update with audio boundaries
        for t in audio:
            # Check if there's a nearby visual boundary
            merged = False
            for existing_time, cp in list(all_times.items()):
                if abs(t - existing_time) <= self.cut_point_merge_tolerance:
                    # Merge with existing point
                    cp.is_audio = True
                    merged = True
                    break

            if not merged:
                all_times[t] = CutPoint(time=t, is_visual=False, is_audio=True)

        # Always include video start and end as cut points
        if 0.0 not in all_times:
            all_times[0.0] = CutPoint(time=0.0, is_visual=True, is_audio=True)
        if video_duration not in all_times:
            all_times[video_duration] = CutPoint(time=video_duration, is_visual=True, is_audio=True)

        # Sort by time
        cut_points = sorted(all_times.values(), key=lambda cp: cp.time)

        # Deduplicate very close points (within 0.3s)
        deduped: list[CutPoint] = []
        for cp in cut_points:
            if not deduped or cp.time - deduped[-1].time > 0.3:
                deduped.append(cp)
            else:
                # Merge priorities with existing point
                deduped[-1].is_visual = deduped[-1].is_visual or cp.is_visual
                deduped[-1].is_audio = deduped[-1].is_audio or cp.is_audio

        return deduped

    def _generate_candidate_segments(
        self,
        cut_points: list[CutPoint],
        video_duration: float,
    ) -> list[tuple[CutPoint, CutPoint]]:
        """Generate candidate segments from cut points.

        Segments preferentially start and end on audio boundaries.
        Falls back to visual-only if no audio boundaries available.

        Args:
            cut_points: List of available cut points.
            video_duration: Total video duration.

        Returns:
            List of (start_point, end_point) tuples for candidate segments.
        """
        if len(cut_points) < 2:
            return []

        # First try: segments with audio boundaries on both ends
        audio_points = [cp for cp in cut_points if cp.is_audio]

        if len(audio_points) >= 2:
            candidates = self._generate_segments_from_points(audio_points, video_duration)
            if candidates:
                return candidates

        # Second try: segments with audio boundary on at least one end
        candidates = []
        for i, start_cp in enumerate(cut_points):
            for end_cp in cut_points[i + 1 :]:
                duration = end_cp.time - start_cp.time

                if duration < self.min_segment_duration:
                    continue
                if duration > self.max_segment_duration:
                    continue

                # Prefer segments with at least one audio boundary
                if start_cp.is_audio or end_cp.is_audio:
                    candidates.append((start_cp, end_cp))

        if candidates:
            # Sort by combined priority:
            # 1. Prefer both audio boundaries (primary)
            # 2. Prefer durations closer to optimal (secondary)
            dynamic_optimal = self._get_dynamic_optimal_duration(video_duration)
            candidates.sort(
                key=lambda pair: (
                    -(
                        pair[0].is_audio + pair[1].is_audio
                    ),  # More audio = better (negative for desc)
                    abs(
                        (pair[1].time - pair[0].time) - dynamic_optimal
                    ),  # Closer to optimal = better
                ),
            )
            return candidates[:20]  # Limit to top 20 candidates

        # Third try: any valid segment (visual-only fallback)
        logger.warning("No audio boundaries found, using visual-only segments")
        return self._generate_segments_from_points(cut_points, video_duration)

    def _generate_segments_from_points(
        self, points: list[CutPoint], video_duration: float | None = None
    ) -> list[tuple[CutPoint, CutPoint]]:
        """Generate all valid segments from a list of cut points.

        Args:
            points: List of cut points to use as boundaries.
            video_duration: Total video duration for optimal duration preference.

        Returns:
            List of (start, end) point pairs for valid segments.
        """
        candidates = []

        for i, start_cp in enumerate(points):
            for end_cp in points[i + 1 :]:
                duration = end_cp.time - start_cp.time

                if duration < self.min_segment_duration:
                    continue
                if duration > self.max_segment_duration:
                    continue

                candidates.append((start_cp, end_cp))

        # Sort by proximity to optimal duration
        if candidates and video_duration:
            dynamic_optimal = self._get_dynamic_optimal_duration(video_duration)
            candidates.sort(key=lambda pair: abs((pair[1].time - pair[0].time) - dynamic_optimal))

        return candidates

    def _generate_fallback_segments(
        self,
        video_duration: float,
        cut_points: list[CutPoint],
    ) -> list[tuple[CutPoint, CutPoint]]:
        """Generate fallback segments when normal generation fails.

        Creates segments based on fixed intervals or the entire video.
        Uses proportional max duration based on source length.

        Args:
            video_duration: Total video duration.
            cut_points: Available cut points.

        Returns:
            List of fallback segment candidates.
        """
        # Use proportional max instead of hard max_segment_duration
        # This prevents long videos from creating excessively long fallback segments
        proportional_max = self._get_max_segment_for_source(video_duration)
        target_duration = min(proportional_max, video_duration)
        logger.debug(
            f"Fallback segments: source={video_duration:.1f}s, "
            f"proportional_max={proportional_max:.1f}s, target={target_duration:.1f}s"
        )

        # Generate evenly spaced segments
        candidates = []
        step = target_duration / 2  # 50% overlap

        current_start = 0.0
        while current_start + self.min_segment_duration <= video_duration:
            end_time = min(current_start + target_duration, video_duration)

            # Find nearest cut points
            start_cp = self._find_nearest_cut_point(cut_points, current_start)
            end_cp = self._find_nearest_cut_point(cut_points, end_time)

            if start_cp and end_cp and start_cp.time < end_cp.time:
                candidates.append((start_cp, end_cp))

            current_start += step

        # Always include a fallback using video bounds
        if not candidates:
            start_cp = CutPoint(time=0.0, is_visual=True, is_audio=True)
            end_time = min(target_duration, video_duration)
            end_cp = CutPoint(time=end_time, is_visual=True, is_audio=True)
            candidates.append((start_cp, end_cp))

        return candidates

    def _find_nearest_cut_point(self, cut_points: list[CutPoint], time: float) -> CutPoint | None:
        """Find the cut point nearest to a given time.

        Args:
            cut_points: List of available cut points.
            time: Target time in seconds.

        Returns:
            Nearest CutPoint, or None if no points available.
        """
        if not cut_points:
            return None

        nearest = min(cut_points, key=lambda cp: abs(cp.time - time))
        return nearest

    def _adjust_candidates_for_audio(
        self,
        candidates: list[tuple[CutPoint, CutPoint]],
        audio_result: AudioAnalysisResult,
        video_duration: float,
        max_adjustment: float = 5.0,  # Increased to handle long speech segments
    ) -> list[tuple[CutPoint, CutPoint]]:
        """Adjust candidate segment boundaries to avoid cutting during protected audio events.

        This ensures we don't cut mid-laugh or mid-speech by adjusting boundaries
        to the edges of protected audio ranges.

        Args:
            candidates: List of (start, end) cut point pairs.
            audio_result: Audio analysis with protected ranges.
            video_duration: Total video duration.
            max_adjustment: Maximum adjustment per boundary in seconds.

        Returns:
            Adjusted list of candidate segments.
        """
        if not audio_result.protected_ranges:
            return candidates

        # Add buffer around protected ranges to catch speech at edges
        # YAMNet uses 0.96s windows, so speech might extend slightly beyond detected boundaries
        BUFFER = 0.3  # seconds
        buffered_ranges = [
            (max(0, start - BUFFER), min(video_duration, end + BUFFER))
            for start, end in audio_result.protected_ranges
        ]

        # Merge overlapping buffered ranges
        merged_ranges = []
        for start, end in sorted(buffered_ranges):
            if merged_ranges and start <= merged_ranges[-1][1]:
                # Overlaps with previous range, extend it
                merged_ranges[-1] = (merged_ranges[-1][0], max(merged_ranges[-1][1], end))
            else:
                merged_ranges.append((start, end))

        logger.info(
            f"     Buffered+merged ranges: {[(f'{s:.2f}-{e:.2f}') for s, e in merged_ranges]}"
        )

        adjusted = []
        adjustments_made = 0

        for start_cp, end_cp in candidates:
            orig_start = start_cp.time
            orig_end = end_cp.time
            new_start = orig_start
            new_end = orig_end
            was_adjusted = False

            # Check if start or end CROSSES a protected range boundary
            # KEY INSIGHT: Only adjust if we're CROSSING a boundary, not if entirely inside
            # - If segment is entirely inside a protected range → can't avoid mid-speech, keep as-is
            # - If segment START crosses INTO speech → nudge slightly to clean boundary
            # - If segment END crosses INTO speech → nudge slightly to clean boundary
            for range_start, range_end in merged_ranges:
                start_inside = range_start <= new_start < range_end
                end_inside = range_start < new_end <= range_end

                # If BOTH start and end are inside the same protected range,
                # we can't avoid cutting mid-speech - keep original segment
                if start_inside and end_inside:
                    logger.debug(
                        f"Segment {new_start:.2f}s-{new_end:.2f}s entirely inside speech "
                        f"{range_start:.2f}-{range_end:.2f}s - keeping as-is"
                    )
                    continue

                # Only adjust start if it's inside but end is outside (crossing boundary)
                # Nudge by small amount, not to the entire range edge
                if start_inside and not end_inside:
                    # Move start slightly earlier (max 2s) to avoid cutting mid-speech
                    nudge = min(2.0, new_start - range_start + 0.1)
                    new_val = max(0, new_start - nudge)
                    logger.debug(
                        f"Nudging segment start {new_start:.2f}s -> {new_val:.2f}s "
                        f"(speech ends at {range_end:.2f}s)"
                    )
                    new_start = new_val
                    was_adjusted = True

                # Only adjust end if it's inside but start is outside (crossing boundary)
                if end_inside and not start_inside:
                    # Move end slightly later (max 2s) to avoid cutting mid-speech
                    nudge = min(2.0, range_end - new_end + 0.1)
                    new_val = min(video_duration, new_end + nudge)
                    logger.debug(
                        f"Nudging segment end {new_end:.2f}s -> {new_val:.2f}s "
                        f"(speech starts at {range_start:.2f}s)"
                    )
                    new_end = new_val
                    was_adjusted = True

            if was_adjusted:
                adjustments_made += 1
                logger.info(
                    f"     Adjusted: {orig_start:.2f}s-{orig_end:.2f}s -> {new_start:.2f}s-{new_end:.2f}s"
                )

            # Enforce proportional max segment duration after audio adjustment
            # This prevents speech-heavy videos from creating excessively long segments
            proportional_max = self._get_max_segment_for_source(video_duration)
            segment_duration = new_end - new_start
            if segment_duration > proportional_max:
                logger.info(
                    f"     Trimming oversized segment {new_start:.2f}s-{new_end:.2f}s "
                    f"({segment_duration:.1f}s) to proportional max {proportional_max:.1f}s "
                    f"(source={video_duration:.1f}s)"
                )
                new_end = new_start + proportional_max

            # Ensure valid segment
            if new_end - new_start >= self.min_segment_duration:
                # Create adjusted cut points
                adj_start = CutPoint(
                    time=new_start,
                    is_visual=start_cp.is_visual,
                    is_audio=True,  # Audio-adjusted
                )
                adj_end = CutPoint(
                    time=new_end,
                    is_visual=end_cp.is_visual,
                    is_audio=True,  # Audio-adjusted
                )
                adjusted.append((adj_start, adj_end))
            elif new_end - new_start > 0:
                # Keep original if adjustment made it too short
                if was_adjusted:
                    logger.warning(
                        f"     ⚠️ Keeping original segment {orig_start:.2f}s-{orig_end:.2f}s "
                        f"(adjustment would make it too short: {new_end - new_start:.2f}s < {self.min_segment_duration}s). "
                        f"This segment may still cut through speech!"
                    )
                adjusted.append((start_cp, end_cp))

        if adjustments_made > 0:
            logger.info(
                f"     Made {adjustments_made} boundary adjustments to avoid mid-speech cuts"
            )

        return adjusted

    def _score_segments_visual_only(
        self,
        video_path: Path,
        candidates: list[tuple[CutPoint, CutPoint]],
        all_cut_points: list[CutPoint],
        audio_content_result: AudioAnalysisResult | None = None,
        video_duration: float | None = None,
    ) -> list[ScoredSegment]:
        """Score candidate segments using visual analysis only (fast).

        LLM content analysis is done separately on top candidates only.

        Args:
            video_path: Path to video file.
            candidates: List of (start, end) cut point pairs.
            all_cut_points: All available cut points (for context).
            audio_content_result: Optional audio content analysis results.
            video_duration: Total video duration for duration scoring.

        Returns:
            List of ScoredSegment with visual scores populated.
        """
        import gc

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
        candidates: list[tuple[CutPoint, CutPoint]],
        all_cut_points: list[CutPoint],
    ) -> list[ScoredSegment]:
        """Score candidate segments using visual and optional content analysis.

        DEPRECATED: Use _score_segments_visual_only + separate LLM scoring.

        Args:
            video_path: Path to video file.
            candidates: List of (start, end) cut point pairs.
            all_cut_points: All available cut points (for context).

        Returns:
            List of ScoredSegment with scores populated.
        """
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
        segment: ScoredSegment | None = None,
    ) -> float:
        """Score a segment using LLM content analysis.

        Args:
            video_path: Path to video file.
            start_time: Segment start time.
            end_time: Segment end time.
            segment: Optional segment to update with full LLM analysis.

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
                    use_yamnet=config.audio_content.use_yamnet,
                    min_confidence=config.audio_content.min_confidence,
                    laughter_confidence=config.audio_content.laughter_confidence,
                )

            result = self._audio_analyzer.analyze(video_path, video_duration)
            self._audio_analysis_cache[cache_key] = result
            return result

        except Exception as e:
            logger.warning(f"Audio content analysis failed: {e}")
            return None

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
            Dict with score, has_laughter, has_speech, has_music.
        """
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
            }

        # Calculate weighted score based on overlapping events
        total_weighted = 0.0
        total_duration = 0.0
        has_laughter = False
        has_speech = False
        has_music = False

        for event, duration in segment_events:
            weight = event.weight * event.confidence
            total_weighted += weight * duration
            total_duration += duration

            # Check for specific content types
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
        }

    def _compute_total_score(self, segment: ScoredSegment) -> float:
        """Compute the total score for a segment.

        Args:
            segment: Segment with component scores.

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


def create_unified_analyzer_from_config() -> UnifiedSegmentAnalyzer:
    """Create a UnifiedSegmentAnalyzer from current configuration.

    Returns:
        Configured UnifiedSegmentAnalyzer instance.
    """
    from immich_memories.config import get_config

    config = get_config()

    # Get content analyzer if enabled
    content_analyzer = None
    content_weight = 0.0

    if config.content_analysis.enabled:
        try:
            from immich_memories.analysis.content_analyzer import get_content_analyzer

            content_analyzer = get_content_analyzer(
                ollama_url=config.llm.ollama_url,
                ollama_model=config.llm.ollama_model,
                openai_api_key=config.llm.openai_api_key,
                openai_model=config.llm.openai_model,
                openai_base_url=config.llm.openai_base_url,
                provider=config.llm.provider,
            )
            content_weight = config.content_analysis.weight
        except Exception as e:
            logger.warning(f"Failed to initialize content analyzer: {e}")

    # Get audio content analysis settings
    audio_content_enabled = config.audio_content.enabled
    audio_content_weight = config.audio_content.weight if audio_content_enabled else 0.0

    # Log duration scoring config
    logger.info(
        f"Duration scoring config: base={config.analysis.optimal_clip_duration:.1f}s, "
        f"max={config.analysis.max_optimal_duration:.1f}s, "
        f"ratio={config.analysis.target_extraction_ratio * 100:.0f}%"
    )

    return UnifiedSegmentAnalyzer(
        scorer=SceneScorer(),
        content_analyzer=content_analyzer,
        min_segment_duration=config.analysis.min_segment_duration,
        max_segment_duration=config.analysis.max_segment_duration,
        silence_threshold_db=config.analysis.silence_threshold_db,
        min_silence_duration=config.analysis.min_silence_duration,
        cut_point_merge_tolerance=config.analysis.cut_point_merge_tolerance,
        content_weight=content_weight,
        audio_content_enabled=audio_content_enabled,
        audio_content_weight=audio_content_weight,
        optimal_clip_duration=config.analysis.optimal_clip_duration,
        max_optimal_duration=config.analysis.max_optimal_duration,
        target_extraction_ratio=config.analysis.target_extraction_ratio,
    )
