"""Interest scoring for video moments."""

from __future__ import annotations

import gc
import logging
import platform
import random
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np

from immich_memories.analysis.scenes import Scene, get_video_info

if TYPE_CHECKING:
    from immich_memories.analysis.content_analyzer import ContentAnalyzer

logger = logging.getLogger(__name__)

# Check for Apple Vision framework availability
_use_vision = None


def _check_vision_available() -> bool:
    """Check if Apple Vision framework should be used for face detection."""
    global _use_vision
    if _use_vision is not None:
        return _use_vision

    if platform.system() != "Darwin":
        _use_vision = False
        return False

    try:
        from immich_memories.analysis.apple_vision import is_vision_available

        _use_vision = is_vision_available()
        if _use_vision:
            logger.info("Using Apple Vision framework for face detection (GPU accelerated)")
        return _use_vision
    except ImportError:
        _use_vision = False
        return False


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
        """Initialize the scorer.

        Args:
            face_weight: Weight for face presence/size.
            motion_weight: Weight for motion amount.
            stability_weight: Weight for camera stability.
            audio_weight: Weight for audio interest.
            content_weight: Weight for LLM content analysis (0 = disabled).
            duration_weight: Weight for duration preference (0.15 = 15%).
            content_analyzer: Content analyzer instance (optional).
            optimal_duration: Base sweet spot duration in seconds (default 5.0).
            max_optimal_duration: Max optimal duration for long sources (default 10.0).
            target_extraction_ratio: Target ratio of clip to source (default 0.15).
            min_duration: Minimum acceptable duration in seconds (default 2.0).
        """
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
        self._use_vision = _check_vision_available()
        self._vision_detector = None

        if self._use_vision:
            try:
                from immich_memories.analysis.apple_vision import VisionFaceDetector

                self._vision_detector = VisionFaceDetector(detect_landmarks=False)
                logger.info("Using Apple Vision for GPU-accelerated face detection")
            except Exception as e:
                logger.warning(f"Failed to initialize Vision detector: {e}")
                self._use_vision = False

        # Fallback to OpenCV face detection
        self._face_cascade = None
        if not self._use_vision:
            try:
                cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                self._face_cascade = cv2.CascadeClassifier(cascade_path)
            except Exception as e:
                logger.warning(f"Could not load face cascade: {e}")

        # Video capture caching to avoid reopening same file for multiple candidates
        self._current_cap: cv2.VideoCapture | None = None
        self._current_path: str | None = None

    def _get_capture(self, video_path: Path) -> cv2.VideoCapture:
        """Get or create video capture, reusing if same file.

        This prevents opening the same video file 21+ times when scoring
        multiple candidates from the same video.
        """
        path_str = str(video_path)
        if self._current_cap is None or self._current_path != path_str:
            # Release old capture if exists
            if self._current_cap is not None:
                self._current_cap.release()
            # Open new capture
            self._current_cap = cv2.VideoCapture(path_str)
            self._current_path = path_str
        return self._current_cap

    def release_capture(self) -> None:
        """Explicitly release video capture resources.

        Call this after finishing all scoring for a video to free memory.
        """
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
        """Score a scene for interest level.

        Args:
            video_path: Path to the video file.
            scene: Scene to score.
            sample_frames: Number of frames to sample.
            source_duration: Full video duration for ratio-based scoring.

        Returns:
            MomentScore with component scores.
        """
        video_path = Path(video_path)
        # Use cached capture to avoid reopening same file for multiple candidates
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

            # Face detection
            face_score, positions = self._compute_face_score(frame)
            face_scores.append(face_score)
            face_positions.extend(positions)

            # Motion and stability
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # Memory optimization: delete BGR frame now that we have grayscale
            del frame

            if prev_frame is not None:
                motion, stability = self._compute_motion_metrics(prev_frame, gray)
                motion_scores.append(motion)
                stability_scores.append(stability)
                # Memory optimization: delete old frame
                del prev_frame

            prev_frame = gray

        # Memory optimization: cleanup (don't release cap - it's cached for reuse)
        # Call release_capture() explicitly when done with all candidates
        if prev_frame is not None:
            del prev_frame
        gc.collect()

        # Aggregate scores
        avg_face = np.mean(face_scores) if face_scores else 0.0
        avg_motion = np.mean(motion_scores) if motion_scores else 0.5
        avg_stability = np.mean(stability_scores) if stability_scores else 0.5

        # Audio score placeholder (would need audio analysis)
        audio_score = 0.5

        # Content analysis (if enabled)
        content_score = 0.0
        if self._content_analyzer and self.content_weight > 0:
            try:
                from immich_memories.config import get_config

                config = get_config()
                analysis = self._content_analyzer.analyze_segment(
                    video_path,
                    scene.start_time,
                    scene.end_time,
                    num_frames=config.content_analysis.analyze_frames,
                )
                # Only use content score if confidence meets threshold
                if analysis.confidence >= config.content_analysis.min_confidence:
                    content_score = analysis.content_score
                else:
                    content_score = 0.5  # Neutral score for low confidence
            except Exception as e:
                logger.debug(f"Content analysis failed: {e}")
                content_score = 0.5

        # Duration score: prefer clips closer to optimal duration
        # For longer source videos, the optimal duration scales up
        scene_duration = scene.end_time - scene.start_time
        duration_score = self._compute_duration_score(scene_duration, source_duration)

        # Compute total weighted score
        total = (
            self.face_weight * avg_face
            + self.motion_weight * avg_motion
            + self.stability_weight * avg_stability
            + self.audio_weight * audio_score
            + self.content_weight * content_score
            + self.duration_weight * duration_score
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
            duration_score=float(duration_score),
            face_positions=face_positions if face_positions else None,
        )

    def _compute_face_score(
        self,
        frame: np.ndarray,
    ) -> tuple[float, list[tuple[float, float]]]:
        """Compute face presence and size score.

        Uses Apple Vision framework on Mac for GPU-accelerated detection,
        falls back to OpenCV on other platforms.

        Args:
            frame: BGR image.

        Returns:
            Tuple of (score, list of face center positions).
        """
        h, w = frame.shape[:2]

        # Use Apple Vision framework if available (GPU accelerated)
        if self._use_vision and self._vision_detector is not None:
            return self._compute_face_score_vision(frame, w, h)

        # Fallback to OpenCV cascade classifier
        return self._compute_face_score_opencv(frame, w, h)

    def _compute_face_score_vision(
        self,
        frame: np.ndarray,
        w: int,  # noqa: ARG002 - kept for API consistency with opencv version
        h: int,  # noqa: ARG002 - kept for API consistency with opencv version
    ) -> tuple[float, list[tuple[float, float]]]:
        """Compute face score using Apple Vision framework.

        Args:
            frame: BGR image.
            w: Frame width.
            h: Frame height.

        Returns:
            Tuple of (score, list of face center positions).
        """
        faces = self._vision_detector.detect_faces(frame, min_confidence=0.3)

        if not faces:
            return 0.0, []

        # Score based on face count and size
        total_coverage = 0
        positions = []

        for face in faces:
            total_coverage += face.area
            # Vision uses bottom-left origin, face.center converts to top-left
            positions.append(face.center)

        # Score: higher coverage = better (up to ~20% coverage)
        # Multiple faces also boost score
        face_count_bonus = min(len(faces) * 0.1, 0.3)
        coverage_score = min(total_coverage / 0.15, 1.0)

        score = min(coverage_score + face_count_bonus, 1.0)
        return score, positions

    def _compute_face_score_opencv(
        self,
        frame: np.ndarray,
        w: int,
        h: int,
    ) -> tuple[float, list[tuple[float, float]]]:
        """Compute face score using OpenCV cascade classifier.

        Args:
            frame: BGR image.
            w: Frame width.
            h: Frame height.

        Returns:
            Tuple of (score, list of face center positions).
        """
        if self._face_cascade is None:
            return 0.5, []

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Detect faces
        faces = self._face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(30, 30),
        )

        if len(faces) == 0:
            return 0.0, []

        # Score based on face count and size
        total_face_area = 0
        positions = []

        for x, y, fw, fh in faces:
            face_area = fw * fh
            total_face_area += face_area

            # Store normalized center position
            center_x = (x + fw / 2) / w
            center_y = (y + fh / 2) / h
            positions.append((center_x, center_y))

        # Normalize by frame area
        frame_area = w * h
        coverage = total_face_area / frame_area

        # Score: higher coverage = better (up to ~20% coverage)
        # Multiple faces also boost score
        face_count_bonus = min(len(faces) * 0.1, 0.3)
        coverage_score = min(coverage / 0.15, 1.0)

        score = min(coverage_score + face_count_bonus, 1.0)
        return score, positions

    def _compute_motion_metrics(
        self,
        prev_frame: np.ndarray,
        curr_frame: np.ndarray,
    ) -> tuple[float, float]:
        """Compute motion amount and stability.

        Args:
            prev_frame: Previous grayscale frame.
            curr_frame: Current grayscale frame.

        Returns:
            Tuple of (motion_score, stability_score).
        """
        # Compute optical flow
        flow = cv2.calcOpticalFlowFarneback(
            prev_frame,
            curr_frame,
            None,
            pyr_scale=0.5,
            levels=3,
            winsize=15,
            iterations=3,
            poly_n=5,
            poly_sigma=1.2,
            flags=0,
        )

        # Magnitude of flow
        magnitude = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
        mean_motion = np.mean(magnitude)
        motion_std = np.std(magnitude)

        # Motion score: some motion is good, too much is bad
        # Optimal range is roughly 2-10 pixels of movement
        if mean_motion < 1:
            motion_score = 0.3  # Too static
        elif mean_motion < 5:
            motion_score = 0.5 + (mean_motion - 1) * 0.125  # Good range
        elif mean_motion < 15:
            motion_score = 1.0 - (mean_motion - 5) * 0.05  # Getting shaky
        else:
            motion_score = 0.3  # Too much motion (shake or blur)

        # Stability score: lower variance = more stable
        # High std indicates camera shake or erratic motion
        stability_score = max(0, 1 - (motion_std / 20))

        return float(motion_score), float(stability_score)

    def _compute_duration_score(
        self,
        duration: float,
        source_duration: float | None = None,
    ) -> float:
        """Compute duration preference score using a Gaussian curve.

        The score peaks at the optimal duration, which scales with source
        duration for longer videos. For a 15s source, 5s is optimal. For a
        70s source, ~10s is optimal (to avoid extracting too little).

        Args:
            duration: Clip duration in seconds.
            source_duration: Full source video duration (for ratio-based scaling).

        Returns:
            Score between 0.0 and 1.0, with 1.0 at optimal duration.
        """
        # Clips below minimum duration get heavy penalty
        if duration < self._min_duration:
            # Linear penalty: 0.0 at 0s, 0.3 at min_duration
            return max(0.0, 0.3 * (duration / self._min_duration))

        # Dynamic optimal duration based on source length
        # For short sources (< 20s): optimal stays at base (5s)
        # For longer sources: optimal scales up to max_optimal
        if source_duration and source_duration > 20.0:
            dynamic_optimal = min(
                self._max_optimal_duration,
                max(self._optimal_duration, source_duration * self._target_extraction_ratio),
            )
            logger.debug(
                f"Duration scoring: source={source_duration:.1f}s, "
                f"clip={duration:.1f}s, optimal={dynamic_optimal:.1f}s "
                f"(target {self._target_extraction_ratio*100:.0f}% of source)"
            )
        else:
            dynamic_optimal = self._optimal_duration
            if source_duration:
                logger.debug(
                    f"Duration scoring: source={source_duration:.1f}s (short), "
                    f"clip={duration:.1f}s, optimal={dynamic_optimal:.1f}s (base)"
                )

        # Gaussian curve centered at dynamic optimal duration
        # sigma scales with optimal to keep curve proportional
        sigma = max(3.0, dynamic_optimal * 0.6)
        diff = duration - dynamic_optimal
        score = np.exp(-(diff * diff) / (2 * sigma * sigma))

        # For very long clips (>15s), add extra penalty
        if duration > 15.0:
            long_penalty = (duration - 15.0) * 0.05
            score = max(0.2, score - long_penalty)

        return float(score)

    def find_best_moments(
        self,
        video_path: str | Path,
        scenes: list[Scene],
        target_duration: float = 5.0,
        min_duration: float = 2.0,
        max_duration: float = 10.0,
    ) -> list[MomentScore]:
        """Find the best moments across all scenes.

        Args:
            video_path: Path to the video file.
            scenes: List of scenes to analyze.
            target_duration: Target moment duration in seconds.
            min_duration: Minimum moment duration.
            max_duration: Maximum moment duration.

        Returns:
            List of scored moments, sorted by score (best first).
        """
        video_path = Path(video_path)
        moments = []

        for scene in scenes:
            # Score the scene
            score = self.score_scene(video_path, scene)

            # If scene is longer than max_duration, find best sub-segment
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
                    # Use first target_duration seconds
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

        # Sort by score (best first)
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
        """Find the best segment within a long scene.

        Uses sliding window analysis.

        Args:
            video_path: Path to the video file.
            scene: Scene to analyze.
            target_duration: Target segment duration.
            min_duration: Minimum segment duration.
            max_duration: Maximum segment duration (reserved).

        Returns:
            Best scoring segment or None.
        """
        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30

        # Slide window across the scene
        step = target_duration / 2  # 50% overlap
        best_score = -1
        best_segment = None

        current_start = scene.start_time
        while current_start + min_duration <= scene.end_time:
            segment_end = min(current_start + target_duration, scene.end_time)

            # Create temporary scene for scoring
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

    def _generate_segments(
        self,
        video_path: Path,
        segment_duration: float,
        overlap: float,
    ) -> list[Scene]:
        """Generate segment boundaries using sliding window.

        Args:
            video_path: Path to the video file.
            segment_duration: Duration of each segment in seconds.
            overlap: Overlap fraction between segments (0-1).

        Returns:
            List of Scene objects representing segments.
        """
        info = get_video_info(video_path)
        duration = info.get("duration", 0)
        fps = info.get("fps", 30) or 30

        if duration <= 0:
            return []

        # Handle video shorter than segment_duration
        if duration <= segment_duration:
            return [
                Scene(
                    start_time=0,
                    end_time=duration,
                    start_frame=0,
                    end_frame=int(duration * fps),
                )
            ]

        step = segment_duration * (1 - overlap)
        segments = []
        current_start = 0.0

        while current_start + segment_duration <= duration:
            segments.append(
                Scene(
                    start_time=current_start,
                    end_time=current_start + segment_duration,
                    start_frame=int(current_start * fps),
                    end_frame=int((current_start + segment_duration) * fps),
                )
            )
            current_start += step

        # Handle final partial segment if substantial
        if current_start < duration and (duration - current_start) >= segment_duration * 0.5:
            segments.append(
                Scene(
                    start_time=current_start,
                    end_time=duration,
                    start_frame=int(current_start * fps),
                    end_frame=int(duration * fps),
                )
            )

        return segments

    def _generate_scene_aware_segments(
        self,
        video_path: Path,
        max_segment_duration: float,
        min_segment_duration: float,
        scene_threshold: float,
        min_scene_duration: float,
    ) -> list[Scene]:
        """Generate segments using scene detection with subdivision for long scenes.

        Args:
            video_path: Path to the video file.
            max_segment_duration: Maximum segment duration (subdivide longer scenes).
            min_segment_duration: Minimum segment duration (filter out shorter).
            scene_threshold: Threshold for scene detection.
            min_scene_duration: Minimum scene duration for detection.

        Returns:
            List of Scene objects representing segments.
        """
        from immich_memories.analysis.scenes import SceneDetector

        # Detect natural scene boundaries
        detector = SceneDetector(
            threshold=scene_threshold,
            min_scene_duration=min_scene_duration,
            adaptive_threshold=True,
        )
        scenes = detector.detect(
            video_path,
            extract_keyframes=False,  # Skip for performance
        )

        # Get video info for fps
        info = get_video_info(video_path)
        fps = info.get("fps", 30) or 30

        # Process scenes into segments
        segments = []

        for scene in scenes:
            if scene.duration < min_segment_duration:
                # Skip very short scenes (likely flashes/glitches)
                continue

            if scene.duration <= max_segment_duration:
                # Short/medium scene: use entire scene as one segment
                segments.append(scene)
            else:
                # Long scene: subdivide with sliding window WITHIN scene boundaries
                sub_segments = self._subdivide_scene(
                    scene=scene,
                    target_duration=max_segment_duration / 2,  # Target smaller segments
                    overlap=0.5,
                    fps=fps,
                )
                segments.extend(sub_segments)

        return segments

    def _subdivide_scene(
        self,
        scene: Scene,
        target_duration: float,
        overlap: float,
        fps: float,
    ) -> list[Scene]:
        """Subdivide a long scene into overlapping segments.

        Args:
            scene: The scene to subdivide.
            target_duration: Target duration for each sub-segment.
            overlap: Overlap fraction between segments (0-1).
            fps: Video frame rate.

        Returns:
            List of Scene objects representing sub-segments.
        """
        sub_segments = []
        step = target_duration * (1 - overlap)
        current_start = scene.start_time

        while current_start + target_duration <= scene.end_time:
            sub_segments.append(
                Scene(
                    start_time=current_start,
                    end_time=current_start + target_duration,
                    start_frame=int(current_start * fps),
                    end_frame=int((current_start + target_duration) * fps),
                )
            )
            current_start += step

        # Handle final partial segment if substantial
        remaining = scene.end_time - current_start
        if remaining >= target_duration * 0.5:
            sub_segments.append(
                Scene(
                    start_time=current_start,
                    end_time=scene.end_time,
                    start_frame=int(current_start * fps),
                    end_frame=int(scene.end_time * fps),
                )
            )

        return sub_segments

    def _compute_sort_key(
        self,
        moment: MomentScore,
        video_duration: float,
    ) -> tuple[float, float, float, float]:
        """Compute sort key with intelligent tiebreaking.

        When scores are equal, this prefers:
        1. Higher component variance (more "interesting")
        2. Closer to video midpoint (avoid start/end)
        3. Random factor to break remaining ties

        Args:
            moment: The moment to compute sort key for.
            video_duration: Total video duration in seconds.

        Returns:
            Tuple for sorting (lower = better).
        """
        # 1. Primary: total score (negated for descending sort)
        primary = -moment.total_score

        # 2. Variance bonus: prefer segments with diverse scores
        scores = [moment.face_score, moment.motion_score, moment.stability_score]
        variance = -float(np.var(scores))  # Negated: higher variance first

        # 3. Middle preference: distance from video midpoint (normalized)
        video_midpoint = video_duration / 2
        midpoint_distance = abs(moment.midpoint - video_midpoint) / max(video_midpoint, 0.001)

        # 4. Random factor: break remaining ties
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
        """Sample video at regular intervals and score each segment.

        When scene detection is enabled, segments are created from natural scene
        boundaries detected in the video. Long scenes are subdivided. When disabled,
        uses fixed-duration sliding window segments.

        Args:
            video_path: Path to the video file.
            segment_duration: Duration of each segment in seconds (for fixed segmentation).
            overlap: Overlap fraction between segments (0-1).
            sample_frames: Number of frames to sample per segment.
            progress_callback: Optional callback with (current, total).
            use_scene_detection: Override config setting. None = use config default.

        Returns:
            List of MomentScore objects sorted by score (best first).
        """
        from immich_memories.config import get_config

        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Video not found: {video_path}")

        config = get_config()

        # Determine whether to use scene detection
        should_use_scene_detection = (
            use_scene_detection
            if use_scene_detection is not None
            else config.analysis.use_scene_detection
        )

        # Get video info for sorting
        info = get_video_info(video_path)
        video_duration = info.get("duration", 0)

        if video_duration <= 0:
            return []

        # Generate segments - scene-aware or fixed
        if should_use_scene_detection:
            try:
                segments = self._generate_scene_aware_segments(
                    video_path=video_path,
                    max_segment_duration=config.analysis.max_segment_duration,
                    min_segment_duration=config.analysis.min_segment_duration,
                    scene_threshold=config.analysis.scene_threshold,
                    min_scene_duration=config.analysis.min_scene_duration,
                )
                logger.debug(
                    f"Scene detection: {len(segments)} segments from natural boundaries"
                )
            except Exception as e:
                logger.warning(f"Scene detection failed, falling back to fixed segments: {e}")
                segments = self._generate_segments(video_path, segment_duration, overlap)
        else:
            segments = self._generate_segments(video_path, segment_duration, overlap)

        if not segments:
            return []

        # Score each segment
        # Pass video_duration for ratio-based duration scoring
        # Calculate dynamic optimal for this video
        if video_duration > 20.0:
            dynamic_optimal = min(
                self._max_optimal_duration,
                max(self._optimal_duration, video_duration * self._target_extraction_ratio),
            )
            logger.info(
                f"Duration scoring: source={video_duration:.1f}s → "
                f"optimal clip={dynamic_optimal:.1f}s "
                f"(target {self._target_extraction_ratio*100:.0f}% of source, "
                f"min={self._min_duration:.1f}s, max={self._max_optimal_duration:.1f}s)"
            )
        else:
            logger.info(
                f"Duration scoring: source={video_duration:.1f}s (short) → "
                f"optimal clip={self._optimal_duration:.1f}s (base)"
            )

        moments = []
        for i, segment in enumerate(segments):
            score = self.score_scene(
                video_path,
                segment,
                sample_frames=sample_frames,
                source_duration=video_duration,
            )
            moments.append(score)

            if progress_callback:
                progress_callback(i + 1, len(segments))

            # Memory optimization: periodic cleanup during long scoring sessions
            # This helps prevent memory buildup for videos with many segments
            if (i + 1) % 10 == 0:
                gc.collect()

        # Final cleanup before sorting
        del segments
        gc.collect()

        # Sort with enhanced tiebreaking
        moments.sort(key=lambda m: self._compute_sort_key(m, video_duration))

        return moments


def score_scene(
    video_path: str | Path,
    scene: Scene,
    sample_frames: int = 10,
) -> MomentScore:
    """Convenience function to score a single scene.

    Args:
        video_path: Path to the video file.
        scene: Scene to score.
        sample_frames: Number of frames to sample.

    Returns:
        MomentScore with component scores.
    """
    scorer = SceneScorer()
    return scorer.score_scene(video_path, scene, sample_frames)


def select_top_moments(
    video_path: str | Path,
    scenes: list[Scene],
    target_count: int = 5,
    target_duration: float = 5.0,
) -> list[MomentScore]:
    """Select the top N moments from a video.

    Args:
        video_path: Path to the video file.
        scenes: List of detected scenes.
        target_count: Number of moments to select.
        target_duration: Target duration per moment.

    Returns:
        List of top scored moments.
    """
    scorer = SceneScorer()
    moments = scorer.find_best_moments(video_path, scenes, target_duration)
    return moments[:target_count]


def create_scorer_from_config() -> SceneScorer:
    """Create a SceneScorer with content analysis configured from config.

    This factory function creates a SceneScorer that respects the
    content_analysis config settings, initializing the content analyzer
    if enabled.

    Returns:
        SceneScorer instance configured from current config.
    """
    from immich_memories.config import get_config

    config = get_config()

    # Base weights (sum to 1.0 without content analysis)
    # Duration weight gives preference to ~5 second clips
    base_face = 0.35
    base_motion = 0.20
    base_stability = 0.15
    base_audio = 0.15
    base_duration = 0.15

    # Get duration scoring settings from config
    optimal_duration = config.analysis.optimal_clip_duration
    max_optimal_duration = config.analysis.max_optimal_duration
    target_extraction_ratio = config.analysis.target_extraction_ratio
    min_duration = config.analysis.min_segment_duration

    logger.info(
        f"Duration scoring config: base={optimal_duration:.1f}s, "
        f"max={max_optimal_duration:.1f}s, ratio={target_extraction_ratio*100:.0f}%, "
        f"min={min_duration:.1f}s"
    )

    # Initialize content analyzer if enabled
    content_analyzer = None
    content_weight = 0.0

    if config.content_analysis.enabled:
        from immich_memories.analysis.content_analyzer import get_content_analyzer

        # Use shared LLM config
        content_analyzer = get_content_analyzer(
            ollama_url=config.llm.ollama_url,
            ollama_model=config.llm.ollama_model,
            openai_api_key=config.llm.openai_api_key,
            openai_model=config.llm.openai_model,
        )

        if content_analyzer:
            content_weight = config.content_analysis.weight
            logger.info(f"Content analysis enabled with weight {content_weight}")
        else:
            logger.warning("Content analysis enabled but no analyzer available")

    # Adjust other weights to account for content weight
    # When content analysis is enabled, reduce other weights proportionally
    if content_weight > 0:
        scale = 1 - content_weight
        return SceneScorer(
            face_weight=base_face * scale,
            motion_weight=base_motion * scale,
            stability_weight=base_stability * scale,
            audio_weight=base_audio * scale,
            duration_weight=base_duration * scale,
            content_weight=content_weight,
            content_analyzer=content_analyzer,
            optimal_duration=optimal_duration,
            max_optimal_duration=max_optimal_duration,
            target_extraction_ratio=target_extraction_ratio,
            min_duration=min_duration,
        )

    return SceneScorer(
        face_weight=base_face,
        motion_weight=base_motion,
        stability_weight=base_stability,
        audio_weight=base_audio,
        duration_weight=base_duration,
        optimal_duration=optimal_duration,
        max_optimal_duration=max_optimal_duration,
        target_extraction_ratio=target_extraction_ratio,
        min_duration=min_duration,
    )


def sample_video(
    video_path: str | Path,
    segment_duration: float = 3.0,
    overlap: float = 0.5,
    sample_frames: int = 5,
    use_scene_detection: bool | None = None,
) -> list[MomentScore]:
    """Convenience function to sample and score a video.

    When scene detection is enabled (default), segments are created from natural
    scene boundaries. When disabled, uses fixed-duration sliding window segments.

    If content analysis is enabled in config, the scorer will use LLM-based
    content analysis to improve scoring.

    Args:
        video_path: Path to the video file.
        segment_duration: Duration of each segment in seconds (for fixed segmentation).
        overlap: Overlap fraction between segments (0-1).
        sample_frames: Number of frames to sample per segment.
        use_scene_detection: Override config default. None = use config.

    Returns:
        List of MomentScore objects sorted by score (best first).
    """
    scorer = create_scorer_from_config()
    return scorer.sample_and_score_video(
        video_path,
        segment_duration=segment_duration,
        overlap=overlap,
        sample_frames=sample_frames,
        use_scene_detection=use_scene_detection,
    )
