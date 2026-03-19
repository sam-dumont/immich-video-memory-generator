"""Interest scoring for video moments.

Contains face detection, motion/duration scoring, segment generation,
and the main SceneScorer class. Factory functions are in scoring_factory.py.
"""

from __future__ import annotations

import gc
import logging
import platform
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np

from immich_memories.analysis.scenes import Scene, get_video_info

if TYPE_CHECKING:
    from immich_memories.analysis.content_analyzer import ContentAnalyzer
    from immich_memories.config_models import AnalysisConfig, ContentAnalysisConfig
    from immich_memories.memory_types.presets import ScoringProfile

logger = logging.getLogger(__name__)

__all__ = [
    "MomentScore",
    "SceneScorer",
    "check_vision_available",
    "compute_duration_score",
    "compute_face_score",
    "compute_motion_metrics",
    "generate_scene_aware_segments",
    "generate_segments",
    "init_opencv_cascade",
    "init_vision_detector",
    "subdivide_scene",
]


# ---------------------------------------------------------------------------
# Face detection scoring
# ---------------------------------------------------------------------------

_use_vision = None


def check_vision_available() -> bool:
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


def init_vision_detector():
    """Initialize Apple Vision face detector.

    Returns:
        VisionFaceDetector instance or None if initialization fails.
    """
    try:
        from immich_memories.analysis.apple_vision import VisionFaceDetector

        detector = VisionFaceDetector(detect_landmarks=False)
        logger.info("Using Apple Vision for GPU-accelerated face detection")
        return detector
    except Exception as e:
        logger.warning(f"Failed to initialize Vision detector: {e}")
        return None


def init_opencv_cascade() -> cv2.CascadeClassifier | None:
    """Initialize OpenCV face cascade classifier.

    Returns:
        CascadeClassifier instance or None if loading fails.
    """
    try:
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        return cv2.CascadeClassifier(cascade_path)
    except Exception as e:
        logger.warning(f"Could not load face cascade: {e}")
        return None


def compute_face_score(
    frame: np.ndarray,
    use_vision: bool,
    vision_detector,
    face_cascade: cv2.CascadeClassifier | None,
) -> tuple[float, list[tuple[float, float]]]:
    """Compute face presence and size score.

    Uses Apple Vision framework on Mac for GPU-accelerated detection,
    falls back to OpenCV on other platforms.

    Args:
        frame: BGR image.
        use_vision: Whether to use Apple Vision.
        vision_detector: VisionFaceDetector instance (or None).
        face_cascade: OpenCV CascadeClassifier instance (or None).

    Returns:
        Tuple of (score, list of face center positions).
    """
    h, w = frame.shape[:2]

    if use_vision and vision_detector is not None:
        return _compute_face_score_vision(frame, vision_detector)

    return _compute_face_score_opencv(frame, w, h, face_cascade)


def _compute_face_score_vision(
    frame: np.ndarray,
    vision_detector,
) -> tuple[float, list[tuple[float, float]]]:
    """Compute face score using Apple Vision framework."""
    faces = vision_detector.detect_faces(frame, min_confidence=0.3)

    if not faces:
        return 0.0, []

    total_coverage = 0
    positions = []

    for face in faces:
        total_coverage += face.area
        positions.append(face.center)

    face_count_bonus = min(len(faces) * 0.1, 0.3)
    coverage_score = min(total_coverage / 0.15, 1.0)

    score = min(coverage_score + face_count_bonus, 1.0)
    return score, positions


def _compute_face_score_opencv(
    frame: np.ndarray,
    w: int,
    h: int,
    face_cascade: cv2.CascadeClassifier | None,
) -> tuple[float, list[tuple[float, float]]]:
    """Compute face score using OpenCV cascade classifier."""
    if face_cascade is None:
        return 0.5, []

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    faces = face_cascade.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(30, 30),
    )

    if not faces:
        return 0.0, []

    total_face_area = 0
    positions = []

    for x, y, fw, fh in faces:
        face_area = fw * fh
        total_face_area += face_area

        center_x = (x + fw / 2) / w
        center_y = (y + fh / 2) / h
        positions.append((center_x, center_y))

    frame_area = w * h
    coverage = total_face_area / frame_area

    face_count_bonus = min(len(faces) * 0.1, 0.3)
    coverage_score = min(coverage / 0.15, 1.0)

    score = min(coverage_score + face_count_bonus, 1.0)
    return score, positions


# ---------------------------------------------------------------------------
# Motion and duration scoring
# ---------------------------------------------------------------------------


def compute_motion_metrics(
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

    return motion_score, float(stability_score)


def compute_duration_score(
    duration: float,
    source_duration: float | None,
    optimal_duration: float,
    max_optimal_duration: float,
    target_extraction_ratio: float,
    min_duration: float,
) -> float:
    """Compute duration preference score using a Gaussian curve.

    The score peaks at the optimal duration, which scales with source
    duration for longer videos. For a 15s source, 5s is optimal. For a
    70s source, ~10s is optimal (to avoid extracting too little).

    Args:
        duration: Clip duration in seconds.
        source_duration: Full source video duration (for ratio-based scaling).
        optimal_duration: Base sweet spot duration in seconds.
        max_optimal_duration: Max optimal duration for long sources.
        target_extraction_ratio: Target ratio of clip to source.
        min_duration: Minimum acceptable duration in seconds.

    Returns:
        Score between 0.0 and 1.0, with 1.0 at optimal duration.
    """
    # Clips below minimum duration get heavy penalty
    if duration < min_duration:
        # Linear penalty: 0.0 at 0s, 0.3 at min_duration
        return max(0.0, 0.3 * (duration / min_duration))

    # Dynamic optimal duration based on source length
    # For short sources (< 20s): optimal stays at base (5s)
    # For longer sources: optimal scales up to max_optimal
    if source_duration and source_duration > 20.0:
        dynamic_optimal = min(
            max_optimal_duration,
            max(optimal_duration, source_duration * target_extraction_ratio),
        )
        logger.debug(
            f"Duration scoring: source={source_duration:.1f}s, "
            f"clip={duration:.1f}s, optimal={dynamic_optimal:.1f}s "
            f"(target {target_extraction_ratio * 100:.0f}% of source)"
        )
    else:
        dynamic_optimal = optimal_duration
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


# ---------------------------------------------------------------------------
# Segment generation
# ---------------------------------------------------------------------------


def generate_segments(
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


def generate_scene_aware_segments(
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
    scenes = SceneDetector(
        threshold=scene_threshold,
        min_scene_duration=min_scene_duration,
        adaptive_threshold=True,
    ).detect(
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
            sub_segments = subdivide_scene(
                scene=scene,
                target_duration=max_segment_duration / 2,  # Target smaller segments
                overlap=0.5,
                fps=fps,
            )
            segments.extend(sub_segments)

    return segments


def subdivide_scene(
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


# ---------------------------------------------------------------------------
# Data classes and main scorer
# ---------------------------------------------------------------------------


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
        face_weight: float = 0.35,
        motion_weight: float = 0.20,
        stability_weight: float = 0.15,
        audio_weight: float = 0.15,
        content_weight: float = 0.0,
        duration_weight: float = 0.15,
        content_analyzer: ContentAnalyzer | None = None,
        optimal_duration: float = 5.0,
        max_optimal_duration: float = 10.0,
        target_extraction_ratio: float = 0.15,
        min_duration: float = 2.0,
        content_analysis_config: ContentAnalysisConfig | None = None,
        analysis_config: AnalysisConfig | None = None,
    ):
        """Initialize the scorer with component weights and duration settings."""
        # Auto-normalize weights to sum to 1.0
        raw_total = (
            face_weight
            + motion_weight
            + stability_weight
            + audio_weight
            + content_weight
            + duration_weight
        )
        if raw_total > 0 and abs(raw_total - 1.0) > 0.001:
            scale = 1.0 / raw_total
            face_weight *= scale
            motion_weight *= scale
            stability_weight *= scale
            audio_weight *= scale
            content_weight *= scale
            duration_weight *= scale

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
        self._content_analysis_config = content_analysis_config
        self._analysis_config = analysis_config

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

    @classmethod
    def from_profile(cls, profile: ScoringProfile, **kwargs) -> SceneScorer:
        """Create a SceneScorer from a ScoringProfile dataclass."""
        weights = profile.to_dict()
        return cls(**weights, **kwargs)  # type: ignore[arg-type]

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
            audio_score=audio_score,
            stability_score=float(avg_stability),
            content_score=content_score,
            duration_score=dur_score,
            face_positions=face_positions or None,
        )

    def _run_content_analysis(self, video_path: Path, scene: Scene) -> float:
        """Run content analysis if enabled, returning score 0.0-1.0."""
        if not self._content_analyzer or self.content_weight <= 0:
            return 0.0

        try:
            ca_config = self._content_analysis_config
            if ca_config is None:
                from immich_memories.config import get_config

                ca_config = get_config().content_analysis
            analysis = self._content_analyzer.analyze_segment(
                video_path,
                scene.start_time,
                scene.end_time,
                num_frames=ca_config.analyze_frames,
            )
            if analysis.confidence >= ca_config.min_confidence:
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
    ) -> MomentScore | None:
        """Find the best segment within a long scene using sliding window."""
        cap = self._get_capture(video_path)
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
        # Deterministic tiebreaker based on moment position
        random_factor = (hash((moment.start_time, moment.end_time)) % 1000) * 0.000001
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
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Video not found: {video_path}")

        a_config = self._analysis_config
        if a_config is None:
            from immich_memories.config import get_config

            a_config = get_config().analysis

        should_use_scene_detection = (
            use_scene_detection if use_scene_detection is not None else a_config.use_scene_detection
        )

        video_duration = get_video_info(video_path).get("duration", 0)

        if video_duration <= 0:
            return []

        # Generate segments - delegate to scoring_segments module
        segments = self._get_segments(
            video_path,
            should_use_scene_detection,
            a_config,
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
                segments = generate_scene_aware_segments(
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
        return generate_segments(video_path, segment_duration, overlap)

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
