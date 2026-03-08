"""Interest scoring for video moments.

Delegates to scoring_face, scoring_motion, scoring_segments, scoring_factory.
"""

from __future__ import annotations

import gc
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np

from immich_memories.analysis.scenes import Scene, get_video_info
from immich_memories.analysis.scoring_face import (
    check_vision_available,
    compute_face_score,
    init_opencv_cascade,
    init_vision_detector,
)
from immich_memories.analysis.scoring_factory import (
    create_scorer_from_config,
    sample_video,
    score_scene,
    select_top_moments,
)
from immich_memories.analysis.scoring_motion import (
    compute_duration_score,
    compute_motion_metrics,
)
from immich_memories.analysis.scoring_segments import (
    generate_scene_aware_segments,
    generate_segments,
)

if TYPE_CHECKING:
    from immich_memories.analysis.content_analyzer import ContentAnalyzer

logger = logging.getLogger(__name__)

# Re-export convenience functions and factory for backwards compatibility
__all__ = [
    "MomentScore",
    "SceneScorer",
    "create_scorer_from_config",
    "sample_video",
    "score_scene",
    "select_top_moments",
]


@dataclass
class MomentScore:
    """Score for a video moment/segment."""

    start_time: float
    end_time: float
    total_score: float
    face_score: float = 0.0
    motion_score: float = 0.0
    audio_score: float = 0.0
    stability_score: float = 0.0
    content_score: float = 0.0
    duration_score: float = 0.0
    face_positions: list[tuple[float, float]] | None = None

    @property
    def duration(self) -> float:
        """Get moment duration."""
        return self.end_time - self.start_time

    @property
    def midpoint(self) -> float:
        """Get moment midpoint."""
        return (self.start_time + self.end_time) / 2

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "start_time": self.start_time,
            "end_time": self.end_time,
            "total_score": self.total_score,
            "face_score": self.face_score,
            "motion_score": self.motion_score,
            "audio_score": self.audio_score,
            "stability_score": self.stability_score,
            "content_score": self.content_score,
            "duration_score": self.duration_score,
        }


class SceneScorer:
    """Score video scenes for interest level."""

    def __init__(
        self,
        face_weight: float = 0.4,
        motion_weight: float = 0.25,
        stability_weight: float = 0.2,
        audio_weight: float = 0.15,
        content_weight: float = 0.0,
        duration_weight: float = 0.15,
        content_analyzer: ContentAnalyzer | None = None,
        optimal_duration: float = 5.0,
        max_optimal_duration: float = 10.0,
        target_extraction_ratio: float = 0.15,
        min_duration: float = 2.0,
    ):
        """Initialize the scorer with component weights and duration settings."""
        self.face_weight = face_weight
        self.motion_weight = motion_weight
        self.stability_weight = stability_weight
        self.audio_weight = audio_weight
        self.content_weight = content_weight
        self.duration_weight = duration_weight
        self._content_analyzer = content_analyzer
        self._optimal_duration = optimal_duration
        self._max_optimal_duration = max_optimal_duration
        self._target_extraction_ratio = target_extraction_ratio
        self._min_duration = min_duration

        # Check for Apple Vision (GPU-accelerated on Mac)
        self._use_vision = check_vision_available()
        self._vision_detector = None

        if self._use_vision:
            self._vision_detector = init_vision_detector()
            if self._vision_detector is None:
                self._use_vision = False

        # Fallback to OpenCV face detection
        self._face_cascade = None
        if not self._use_vision:
            self._face_cascade = init_opencv_cascade()

        # Video capture caching to avoid reopening same file for multiple candidates
        self._current_cap: cv2.VideoCapture | None = None
        self._current_path: str | None = None

    def _get_capture(self, video_path: Path) -> cv2.VideoCapture:
        """Get or create video capture, reusing if same file."""
        path_str = str(video_path)
        if self._current_cap is None or self._current_path != path_str:
            if self._current_cap is not None:
                self._current_cap.release()
            self._current_cap = cv2.VideoCapture(path_str)
            self._current_path = path_str
        return self._current_cap

    def release_capture(self) -> None:
        """Explicitly release video capture resources."""
        if self._current_cap is not None:
            self._current_cap.release()
            self._current_cap = None
            self._current_path = None

    def score_scene(
        self,
        video_path: str | Path,
        scene: Scene,
        sample_frames: int = 10,
        source_duration: float | None = None,
    ) -> MomentScore:
        """Score a scene for interest level."""
        video_path = Path(video_path)
        cap = self._get_capture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30

        # Sample frames throughout the scene
        start_frame = int(scene.start_time * fps)
        end_frame = int(scene.end_time * fps)
        frame_indices = np.linspace(start_frame, end_frame - 1, sample_frames, dtype=int)

        face_scores = []
        motion_scores = []
        stability_scores = []
        face_positions = []
        prev_frame = None

        for idx in frame_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret:
                continue

            # Face detection - delegate to scoring_face module
            f_score, positions = compute_face_score(
                frame, self._use_vision, self._vision_detector, self._face_cascade
            )
            face_scores.append(f_score)
            face_positions.extend(positions)

            # Motion and stability
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            del frame

            if prev_frame is not None:
                motion, stability = compute_motion_metrics(prev_frame, gray)
                motion_scores.append(motion)
                stability_scores.append(stability)
                del prev_frame

            prev_frame = gray

        # Memory cleanup (don't release cap - it's cached for reuse)
        if prev_frame is not None:
            del prev_frame
        gc.collect()

        # Aggregate scores
        avg_face = np.mean(face_scores) if face_scores else 0.0
        avg_motion = np.mean(motion_scores) if motion_scores else 0.5
        avg_stability = np.mean(stability_scores) if stability_scores else 0.5

        audio_score = 0.5

        # Content analysis (if enabled)
        content_score = self._run_content_analysis(video_path, scene)

        # Duration score - delegate to scoring_motion module
        scene_duration = scene.end_time - scene.start_time
        dur_score = compute_duration_score(
            scene_duration,
            source_duration,
            self._optimal_duration,
            self._max_optimal_duration,
            self._target_extraction_ratio,
            self._min_duration,
        )

        # Compute total weighted score
        total = (
            self.face_weight * avg_face
            + self.motion_weight * avg_motion
            + self.stability_weight * avg_stability
            + self.audio_weight * audio_score
            + self.content_weight * content_score
            + self.duration_weight * dur_score
        )

        return MomentScore(
            start_time=scene.start_time,
            end_time=scene.end_time,
            total_score=float(total),
            face_score=float(avg_face),
            motion_score=float(avg_motion),
            audio_score=float(audio_score),
            stability_score=float(avg_stability),
            content_score=float(content_score),
            duration_score=float(dur_score),
            face_positions=face_positions if face_positions else None,
        )

    def _run_content_analysis(self, video_path: Path, scene: Scene) -> float:
        """Run content analysis if enabled, returning score 0.0-1.0."""
        if not self._content_analyzer or self.content_weight <= 0:
            return 0.0

        try:
            from immich_memories.config import get_config

            config = get_config()
            analysis = self._content_analyzer.analyze_segment(
                video_path,
                scene.start_time,
                scene.end_time,
                num_frames=config.content_analysis.analyze_frames,
            )
            if analysis.confidence >= config.content_analysis.min_confidence:
                return analysis.content_score
            return 0.5
        except Exception as e:
            logger.debug(f"Content analysis failed: {e}")
            return 0.5

    def find_best_moments(
        self,
        video_path: str | Path,
        scenes: list[Scene],
        target_duration: float = 5.0,
        min_duration: float = 2.0,
        max_duration: float = 10.0,
    ) -> list[MomentScore]:
        """Find the best moments across all scenes, sorted by score."""
        video_path = Path(video_path)
        moments = []

        for scene in scenes:
            score = self.score_scene(video_path, scene)

            if scene.duration > max_duration:
                best_segment = self._find_best_segment(
                    video_path,
                    scene,
                    target_duration,
                    min_duration,
                    max_duration,
                )
                if best_segment:
                    moments.append(best_segment)
                else:
                    adjusted = MomentScore(
                        start_time=scene.start_time,
                        end_time=min(scene.start_time + target_duration, scene.end_time),
                        total_score=score.total_score,
                        face_score=score.face_score,
                        motion_score=score.motion_score,
                        audio_score=score.audio_score,
                        stability_score=score.stability_score,
                    )
                    moments.append(adjusted)
            elif scene.duration >= min_duration:
                moments.append(score)

        moments.sort(key=lambda m: m.total_score, reverse=True)
        return moments

    def _find_best_segment(
        self,
        video_path: Path,
        scene: Scene,
        target_duration: float,
        min_duration: float,
        max_duration: float,  # noqa: ARG002 - reserved for future use
    ) -> MomentScore | None:
        """Find the best segment within a long scene using sliding window."""
        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30

        step = target_duration / 2
        best_score = -1
        best_segment = None

        current_start = scene.start_time
        while current_start + min_duration <= scene.end_time:
            segment_end = min(current_start + target_duration, scene.end_time)
            temp_scene = Scene(
                start_time=current_start,
                end_time=segment_end,
                start_frame=int(current_start * fps),
                end_frame=int(segment_end * fps),
            )
            score = self.score_scene(video_path, temp_scene, sample_frames=5)
            if score.total_score > best_score:
                best_score = score.total_score
                best_segment = score
            current_start += step

        cap.release()
        return best_segment

    def _compute_sort_key(
        self,
        moment: MomentScore,
        video_duration: float,
    ) -> tuple[float, float, float, float]:
        """Compute sort key with intelligent tiebreaking."""
        primary = -moment.total_score
        scores = [moment.face_score, moment.motion_score, moment.stability_score]
        variance = -float(np.var(scores))
        video_midpoint = video_duration / 2
        midpoint_distance = abs(moment.midpoint - video_midpoint) / max(video_midpoint, 0.001)
        random_factor = random.random() * 0.001
        return (primary, variance, midpoint_distance, random_factor)

    def sample_and_score_video(
        self,
        video_path: str | Path,
        segment_duration: float = 3.0,
        overlap: float = 0.5,
        sample_frames: int = 5,
        progress_callback: Callable[[int, int], None] | None = None,
        use_scene_detection: bool | None = None,
    ) -> list[MomentScore]:
        """Sample video and score segments, sorted by score (best first)."""
        from immich_memories.config import get_config

        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Video not found: {video_path}")

        config = get_config()

        should_use_scene_detection = (
            use_scene_detection
            if use_scene_detection is not None
            else config.analysis.use_scene_detection
        )

        info = get_video_info(video_path)
        video_duration = info.get("duration", 0)

        if video_duration <= 0:
            return []

        # Generate segments - delegate to scoring_segments module
        segments = self._get_segments(
            video_path,
            should_use_scene_detection,
            config,
            segment_duration,
            overlap,
        )

        if not segments:
            return []

        self._log_duration_info(video_duration)

        # Score each segment
        moments = []
        for i, segment in enumerate(segments):
            s = self.score_scene(
                video_path,
                segment,
                sample_frames=sample_frames,
                source_duration=video_duration,
            )
            moments.append(s)
            if progress_callback:
                progress_callback(i + 1, len(segments))
            if (i + 1) % 10 == 0:
                gc.collect()

        del segments
        gc.collect()

        moments.sort(key=lambda m: self._compute_sort_key(m, video_duration))
        return moments

    def _get_segments(
        self,
        video_path: Path,
        should_use_scene_detection: bool,
        config,
        segment_duration: float,
        overlap: float,
    ) -> list[Scene]:
        """Get segments using scene detection or fixed windowing."""
        if should_use_scene_detection:
            try:
                segments = self._generate_scene_aware_segments(
                    video_path=video_path,
                    max_segment_duration=config.analysis.max_segment_duration,
                    min_segment_duration=config.analysis.min_segment_duration,
                    scene_threshold=config.analysis.scene_threshold,
                    min_scene_duration=config.analysis.min_scene_duration,
                )
                logger.debug(f"Scene detection: {len(segments)} segments from natural boundaries")
                return segments
            except Exception as e:
                logger.warning(f"Scene detection failed, falling back to fixed segments: {e}")
        return self._generate_segments(video_path, segment_duration, overlap)

    def _log_duration_info(self, video_duration: float) -> None:
        """Log duration scoring parameters."""
        if video_duration > 20.0:
            dynamic_optimal = min(
                self._max_optimal_duration,
                max(self._optimal_duration, video_duration * self._target_extraction_ratio),
            )
            logger.info(
                f"Duration scoring: source={video_duration:.1f}s -> "
                f"optimal clip={dynamic_optimal:.1f}s "
                f"(target {self._target_extraction_ratio * 100:.0f}% of source, "
                f"min={self._min_duration:.1f}s, max={self._max_optimal_duration:.1f}s)"
            )
        else:
            logger.info(
                f"Duration scoring: source={video_duration:.1f}s (short) -> "
                f"optimal clip={self._optimal_duration:.1f}s (base)"
            )

    # Backward-compatible shims delegating to helper modules
    def _generate_segments(self, video_path, segment_duration, overlap):
        """Delegate to scoring_segments.generate_segments."""
        return generate_segments(video_path, segment_duration, overlap)

    def _generate_scene_aware_segments(self, **kwargs):
        """Delegate to scoring_segments.generate_scene_aware_segments."""
        return generate_scene_aware_segments(**kwargs)

    def _subdivide_scene(self, scene, target_duration, overlap, fps):
        """Delegate to scoring_segments.subdivide_scene."""
        from immich_memories.analysis.scoring_segments import subdivide_scene

        return subdivide_scene(
            scene=scene, target_duration=target_duration, overlap=overlap, fps=fps
        )
