"""Candidate segment generation and boundary detection logic.

This module contains methods for detecting visual and audio boundaries,
merging them into unified cut points, and generating candidate segments
for scoring.

Extracted from unified_analyzer.py for maintainability.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from immich_memories.analysis.silence_detection import detect_silence_gaps

if TYPE_CHECKING:
    from immich_memories.analysis.unified_analyzer import CutPoint

logger = logging.getLogger(__name__)


class CandidateGenerationMixin:
    """Mixin providing boundary detection and candidate segment generation.

    This mixin is used by UnifiedSegmentAnalyzer to keep candidate generation
    logic separate from scoring and main orchestration logic.

    Expects the following attributes on the host class:
        min_segment_duration: float
        max_segment_duration: float
        silence_threshold_db: float
        min_silence_duration: float
        cut_point_merge_tolerance: float
        _scene_detector: SceneDetector
    """

    # Declared for type checking; actual values set by host class __init__.
    min_segment_duration: float
    max_segment_duration: float
    silence_threshold_db: float
    min_silence_duration: float
    cut_point_merge_tolerance: float

    def _detect_visual_boundaries(self, video_path) -> list[float]:
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

    def _detect_audio_boundaries(self, video_path) -> list[float]:
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

    def _merge_audio_time_into_points(
        self, t: float, all_times: dict[float, CutPoint], CutPoint: type
    ) -> None:
        """Merge one audio timestamp into all_times, finding a nearby visual point or adding new."""
        for existing_time, cp in list(all_times.items()):
            if abs(t - existing_time) <= self.cut_point_merge_tolerance:
                cp.is_audio = True
                return
        all_times[t] = CutPoint(time=t, is_visual=False, is_audio=True)

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
        from immich_memories.analysis.unified_analyzer import CutPoint

        all_times: dict[float, CutPoint] = {}
        for t in visual:
            all_times[t] = CutPoint(time=t, is_visual=True, is_audio=False)

        for t in audio:
            self._merge_audio_time_into_points(t, all_times, CutPoint)

        if 0.0 not in all_times:
            all_times[0.0] = CutPoint(time=0.0, is_visual=True, is_audio=True)
        if video_duration not in all_times:
            all_times[video_duration] = CutPoint(time=video_duration, is_visual=True, is_audio=True)

        cut_points = sorted(all_times.values(), key=lambda cp: cp.time)

        deduped: list[CutPoint] = []
        for cp in cut_points:
            if not deduped or cp.time - deduped[-1].time > 0.3:
                deduped.append(cp)
            else:
                deduped[-1].is_visual = deduped[-1].is_visual or cp.is_visual
                deduped[-1].is_audio = deduped[-1].is_audio or cp.is_audio

        return deduped

    def _collect_mixed_boundary_candidates(
        self,
        cut_points: list[CutPoint],
        video_duration: float,
    ) -> list[tuple[CutPoint, CutPoint]]:
        """Return valid segments with at least one audio boundary, sorted by priority."""
        candidates = []
        for i, start_cp in enumerate(cut_points):
            for end_cp in cut_points[i + 1 :]:
                duration = end_cp.time - start_cp.time
                if duration < self.min_segment_duration or duration > self.max_segment_duration:
                    continue
                if start_cp.is_audio or end_cp.is_audio:
                    candidates.append((start_cp, end_cp))

        if not candidates:
            return []

        dynamic_optimal = self._get_dynamic_optimal_duration(video_duration)
        candidates.sort(
            key=lambda pair: (
                -(pair[0].is_audio + pair[1].is_audio),
                abs((pair[1].time - pair[0].time) - dynamic_optimal),
            ),
        )
        return candidates[:20]

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

        audio_points = [cp for cp in cut_points if cp.is_audio]
        if len(audio_points) >= 2:
            candidates = self._generate_segments_from_points(audio_points, video_duration)
            if candidates:
                return candidates

        candidates = self._collect_mixed_boundary_candidates(cut_points, video_duration)
        if candidates:
            return candidates

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
        from immich_memories.analysis.unified_analyzer import CutPoint

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

    @staticmethod
    def _merge_buffered_ranges(
        protected_ranges: list[tuple[float, float]],
        video_duration: float,
        buffer: float = 0.3,
    ) -> list[tuple[float, float]]:
        """Buffer and merge overlapping protected audio ranges.

        Args:
            protected_ranges: Raw protected ranges from audio analysis.
            video_duration: Total video duration.
            buffer: Buffer to add around each range (seconds).

        Returns:
            Merged list of buffered ranges.
        """
        buffered = [
            (max(0, start - buffer), min(video_duration, end + buffer))
            for start, end in protected_ranges
        ]

        merged: list[tuple[float, float]] = []
        for start, end in sorted(buffered):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged

    def _nudge_segment_for_speech(
        self,
        start: float,
        end: float,
        merged_ranges: list[tuple[float, float]],
        video_duration: float,
    ) -> tuple[float, float, bool]:
        """Nudge a single segment's boundaries away from speech ranges.

        Args:
            start: Segment start time.
            end: Segment end time.
            merged_ranges: Merged protected audio ranges.
            video_duration: Total video duration.

        Returns:
            Tuple of (new_start, new_end, was_adjusted).
        """
        new_start, new_end = start, end
        was_adjusted = False

        for range_start, range_end in merged_ranges:
            start_inside = range_start <= new_start < range_end
            end_inside = range_start < new_end <= range_end

            if start_inside and end_inside:
                continue  # Entirely inside — can't avoid

            if start_inside and not end_inside:
                nudge = min(2.0, new_start - range_start + 0.1)
                new_start = max(0, new_start - nudge)
                was_adjusted = True

            if end_inside and not start_inside:
                nudge = min(2.0, range_end - new_end + 0.1)
                new_end = min(video_duration, new_end + nudge)
                was_adjusted = True

        return new_start, new_end, was_adjusted

    def _adjust_candidates_for_audio(
        self,
        candidates: list[tuple[CutPoint, CutPoint]],
        audio_result,
        video_duration: float,
        max_adjustment: float = 5.0,
    ) -> list[tuple[CutPoint, CutPoint]]:
        """Adjust candidate segment boundaries to avoid cutting during protected audio events.

        Args:
            candidates: List of (start, end) cut point pairs.
            audio_result: Audio analysis with protected ranges.
            video_duration: Total video duration.
            max_adjustment: Maximum adjustment per boundary in seconds.

        Returns:
            Adjusted list of candidate segments.
        """
        from immich_memories.analysis.unified_analyzer import CutPoint

        if not audio_result.protected_ranges:
            return candidates

        merged_ranges = self._merge_buffered_ranges(audio_result.protected_ranges, video_duration)
        logger.info(
            f"     Buffered+merged ranges: {[(f'{s:.2f}-{e:.2f}') for s, e in merged_ranges]}"
        )

        adjusted = []
        adjustments_made = 0
        proportional_max = self._get_max_segment_for_source(video_duration)

        for start_cp, end_cp in candidates:
            new_start, new_end, was_adjusted = self._nudge_segment_for_speech(
                start_cp.time, end_cp.time, merged_ranges, video_duration
            )

            if was_adjusted:
                adjustments_made += 1
                logger.info(
                    f"     Adjusted: {start_cp.time:.2f}s-{end_cp.time:.2f}s -> {new_start:.2f}s-{new_end:.2f}s"
                )

            # Enforce proportional max
            if new_end - new_start > proportional_max:
                logger.info(
                    f"     Trimming oversized segment {new_start:.2f}s-{new_end:.2f}s "
                    f"({new_end - new_start:.1f}s) to proportional max {proportional_max:.1f}s "
                    f"(source={video_duration:.1f}s)"
                )
                new_end = new_start + proportional_max

            if new_end - new_start >= self.min_segment_duration:
                adj_start = CutPoint(time=new_start, is_visual=start_cp.is_visual, is_audio=True)
                adj_end = CutPoint(time=new_end, is_visual=end_cp.is_visual, is_audio=True)
                adjusted.append((adj_start, adj_end))
            elif new_end - new_start > 0:
                if was_adjusted:
                    logger.warning(
                        f"     ⚠️ Keeping original segment {start_cp.time:.2f}s-{end_cp.time:.2f}s "
                        f"(adjustment would make it too short: {new_end - new_start:.2f}s < {self.min_segment_duration}s). "
                        f"This segment may still cut through speech!"
                    )
                adjusted.append((start_cp, end_cp))

        if adjustments_made > 0:
            logger.info(
                f"     Made {adjustments_made} boundary adjustments to avoid mid-speech cuts"
            )

        return adjusted
