"""Scene detection using PySceneDetect."""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from immich_memories.config import get_config

logger = logging.getLogger(__name__)

# Check for CUDA availability in OpenCV
_cuda_available: bool | None = None


def _check_cuda_available() -> bool:
    """Check if OpenCV CUDA is available."""
    global _cuda_available
    if _cuda_available is None:
        try:
            count = cv2.cuda.getCudaEnabledDeviceCount()
            _cuda_available = count > 0
            if _cuda_available:
                logger.info(f"OpenCV CUDA available with {count} device(s)")
        except (cv2.error, AttributeError):
            _cuda_available = False
    return _cuda_available


def _use_cuda_for_analysis() -> bool:
    """Check if we should use CUDA for analysis."""
    config = get_config()
    return config.hardware.enabled and config.hardware.gpu_analysis and _check_cuda_available()


@dataclass
class Scene:
    """A detected scene within a video."""

    start_time: float
    end_time: float
    start_frame: int
    end_frame: int
    keyframe_path: str | None = None
    thumbnail: np.ndarray | None = field(default=None, repr=False)

    @property
    def duration(self) -> float:
        """Get scene duration in seconds."""
        return self.end_time - self.start_time

    @property
    def midpoint(self) -> float:
        """Get scene midpoint time."""
        return (self.start_time + self.end_time) / 2

    def contains_time(self, time: float) -> bool:
        """Check if a time falls within this scene."""
        return self.start_time <= time <= self.end_time

    def to_dict(self) -> dict:
        """Convert to dictionary (excluding numpy arrays)."""
        return {
            "start_time": self.start_time,
            "end_time": self.end_time,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "keyframe_path": self.keyframe_path,
        }


class SceneDetector:
    """Detect scenes in video files using PySceneDetect."""

    def __init__(
        self,
        threshold: float | None = None,
        min_scene_duration: float | None = None,
        adaptive_threshold: bool = True,
    ):
        """Initialize the scene detector.

        Args:
            threshold: Detection threshold (lower = more sensitive).
            min_scene_duration: Minimum scene duration in seconds.
            adaptive_threshold: Use adaptive threshold detection.
        """
        config = get_config()
        self.threshold = threshold or config.analysis.scene_threshold
        self.min_scene_duration = min_scene_duration or config.analysis.min_scene_duration
        self.adaptive_threshold = adaptive_threshold

    def detect(
        self,
        video_path: str | Path,
        extract_keyframes: bool = True,
        keyframe_output_dir: str | Path | None = None,
    ) -> list[Scene]:
        """Detect scenes in a video.

        Args:
            video_path: Path to the video file.
            extract_keyframes: Whether to extract keyframe images.
            keyframe_output_dir: Directory to save keyframes.

        Returns:
            List of detected scenes.
        """
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Video not found: {video_path}")

        try:
            from scenedetect import SceneManager, open_video
            from scenedetect.detectors import AdaptiveDetector, ContentDetector
        except ImportError:
            logger.warning("PySceneDetect not available, using fallback detection")
            return self._fallback_detect(video_path, extract_keyframes, keyframe_output_dir)

        # Open video
        video = open_video(str(video_path))

        # Create scene manager with detector
        scene_manager = SceneManager()

        if self.adaptive_threshold:
            detector = AdaptiveDetector(
                adaptive_threshold=self.threshold,
                min_scene_len=int(self.min_scene_duration * video.frame_rate),
            )
        else:
            detector = ContentDetector(
                threshold=self.threshold,
                min_scene_len=int(self.min_scene_duration * video.frame_rate),
            )

        scene_manager.add_detector(detector)

        # Detect scenes
        scene_manager.detect_scenes(video)
        scene_list = scene_manager.get_scene_list()

        # Convert to our Scene objects
        scenes = []
        for start, end in scene_list:
            scene = Scene(
                start_time=start.get_seconds(),
                end_time=end.get_seconds(),
                start_frame=start.get_frames(),
                end_frame=end.get_frames(),
            )
            scenes.append(scene)

        # If no scenes detected, create one scene for the whole video
        if not scenes:
            cap = cv2.VideoCapture(str(video_path))
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            duration = frame_count / fps
            cap.release()

            scenes = [
                Scene(
                    start_time=0,
                    end_time=duration,
                    start_frame=0,
                    end_frame=frame_count,
                )
            ]

        # Extract keyframes if requested
        if extract_keyframes:
            self._extract_keyframes(video_path, scenes, keyframe_output_dir)

        return scenes

    def _fallback_detect(
        self,
        video_path: Path,
        extract_keyframes: bool,
        keyframe_output_dir: str | Path | None,
    ) -> list[Scene]:
        """Fallback scene detection using OpenCV frame differencing.

        Uses CUDA acceleration when available for faster processing.

        Args:
            video_path: Path to the video file.
            extract_keyframes: Whether to extract keyframes.
            keyframe_output_dir: Directory for keyframes.

        Returns:
            List of detected scenes.
        """
        use_cuda = _use_cuda_for_analysis()
        if use_cuda:
            logger.info("Using CUDA-accelerated scene detection")
            return self._fallback_detect_cuda(video_path, extract_keyframes, keyframe_output_dir)

        return self._fallback_detect_cpu(video_path, extract_keyframes, keyframe_output_dir)

    def _fallback_detect_cpu(
        self,
        video_path: Path,
        extract_keyframes: bool,
        keyframe_output_dir: str | Path | None,
    ) -> list[Scene]:
        """CPU-based fallback scene detection."""
        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        min_scene_frames = int(self.min_scene_duration * fps)

        scenes = []
        scene_starts = [0]
        prev_frame = None
        frame_idx = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            # Convert to grayscale for comparison
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (21, 21), 0)

            if prev_frame is not None:
                # Calculate frame difference
                diff = cv2.absdiff(prev_frame, gray)
                diff_score = np.mean(diff)

                # Detect scene change
                if (
                    diff_score > self.threshold
                    and (frame_idx - scene_starts[-1]) >= min_scene_frames
                ):
                    scene_starts.append(frame_idx)

            prev_frame = gray
            frame_idx += 1

        cap.release()

        # Create scenes from detected boundaries
        for i, start_frame in enumerate(scene_starts):
            end_frame = scene_starts[i + 1] if i + 1 < len(scene_starts) else frame_count

            scenes.append(
                Scene(
                    start_time=start_frame / fps,
                    end_time=end_frame / fps,
                    start_frame=start_frame,
                    end_frame=end_frame,
                )
            )

        # Extract keyframes if requested
        if extract_keyframes:
            self._extract_keyframes(video_path, scenes, keyframe_output_dir)

        return scenes

    def _fallback_detect_cuda(
        self,
        video_path: Path,
        extract_keyframes: bool,
        keyframe_output_dir: str | Path | None,
    ) -> list[Scene]:
        """CUDA-accelerated fallback scene detection.

        Uses GPU for frame processing operations.
        """
        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        min_scene_frames = int(self.min_scene_duration * fps)

        # Create CUDA stream for async operations
        stream = cv2.cuda_Stream()

        # Create GPU matrices and filters
        gpu_frame = cv2.cuda_GpuMat()
        gpu_gray = cv2.cuda_GpuMat()
        gpu_blur = cv2.cuda_GpuMat()
        gpu_prev = cv2.cuda_GpuMat()
        gpu_diff = cv2.cuda_GpuMat()

        # Create Gaussian filter on GPU
        gaussian_filter = cv2.cuda.createGaussianFilter(cv2.CV_8UC1, cv2.CV_8UC1, (21, 21), 0)

        scenes = []
        scene_starts = [0]
        frame_idx = 0
        has_prev = False

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            # Upload frame to GPU
            gpu_frame.upload(frame, stream)

            # Convert to grayscale on GPU
            cv2.cuda.cvtColor(gpu_frame, cv2.COLOR_BGR2GRAY, gpu_gray, stream=stream)

            # Apply Gaussian blur on GPU
            gaussian_filter.apply(gpu_gray, gpu_blur, stream=stream)

            if has_prev:
                # Calculate absolute difference on GPU
                cv2.cuda.absdiff(gpu_blur, gpu_prev, gpu_diff, stream=stream)

                # Download diff to CPU for mean calculation
                # (GPU reduction operations are complex, this is a compromise)
                stream.waitForCompletion()
                diff_cpu = gpu_diff.download()
                diff_score = np.mean(diff_cpu)

                # Detect scene change
                if (
                    diff_score > self.threshold
                    and (frame_idx - scene_starts[-1]) >= min_scene_frames
                ):
                    scene_starts.append(frame_idx)

            # Swap buffers
            gpu_blur.copyTo(gpu_prev, stream)
            has_prev = True
            frame_idx += 1

        cap.release()

        # Create scenes from detected boundaries
        for i, start_frame in enumerate(scene_starts):
            end_frame = scene_starts[i + 1] if i + 1 < len(scene_starts) else frame_count

            scenes.append(
                Scene(
                    start_time=start_frame / fps,
                    end_time=end_frame / fps,
                    start_frame=start_frame,
                    end_frame=end_frame,
                )
            )

        # Extract keyframes if requested
        if extract_keyframes:
            self._extract_keyframes(video_path, scenes, keyframe_output_dir)

        return scenes

    def _extract_keyframes(
        self,
        video_path: Path,
        scenes: list[Scene],
        output_dir: str | Path | None = None,
    ) -> None:
        """Extract keyframe images for each scene.

        Args:
            video_path: Path to the video file.
            scenes: List of scenes to extract keyframes for.
            output_dir: Directory to save keyframes.
        """
        if output_dir is None:
            output_dir = Path(tempfile.gettempdir()) / "immich_memories" / "keyframes"
        else:
            output_dir = Path(output_dir)

        output_dir.mkdir(parents=True, exist_ok=True)

        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30

        for i, scene in enumerate(scenes):
            # Seek to scene midpoint
            midpoint_frame = int(scene.midpoint * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, midpoint_frame)

            ret, frame = cap.read()
            if ret:
                # Save keyframe
                keyframe_path = output_dir / f"{video_path.stem}_scene{i:03d}.jpg"
                cv2.imwrite(str(keyframe_path), frame)
                scene.keyframe_path = str(keyframe_path)

                # Store thumbnail in memory (resized)
                thumbnail = cv2.resize(frame, (320, 180))
                scene.thumbnail = thumbnail

        cap.release()


def detect_scenes(
    video_path: str | Path,
    threshold: float | None = None,
    min_duration: float | None = None,
    extract_keyframes: bool = True,
    keyframe_output_dir: str | Path | None = None,
) -> list[Scene]:
    """Convenience function to detect scenes in a video.

    Args:
        video_path: Path to the video file.
        threshold: Detection threshold.
        min_duration: Minimum scene duration.
        extract_keyframes: Whether to extract keyframes.
        keyframe_output_dir: Directory to save keyframes.

    Returns:
        List of detected scenes.
    """
    detector = SceneDetector(
        threshold=threshold,
        min_scene_duration=min_duration,
    )
    return detector.detect(
        video_path,
        extract_keyframes=extract_keyframes,
        keyframe_output_dir=keyframe_output_dir,
    )


def get_video_info(video_path: str | Path) -> dict:
    """Get basic video information using OpenCV.

    Args:
        video_path: Path to the video file.

    Returns:
        Dictionary with video info (fps, frame_count, duration, width, height).
    """
    cap = cv2.VideoCapture(str(video_path))

    info = {
        "fps": cap.get(cv2.CAP_PROP_FPS),
        "frame_count": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "codec": int(cap.get(cv2.CAP_PROP_FOURCC)),
    }

    if info["fps"] > 0:
        info["duration"] = info["frame_count"] / info["fps"]
    else:
        info["duration"] = 0

    cap.release()
    return info
