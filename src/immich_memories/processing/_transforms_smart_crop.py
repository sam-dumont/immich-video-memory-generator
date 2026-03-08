"""Smart crop and face detection logic for video transforms.

Helper module for transforms.py — contains face detection (Apple Vision
and OpenCV backends) and smart crop region calculation.
"""

from __future__ import annotations

import logging
import platform
from pathlib import Path

import cv2
import numpy as np

from immich_memories.processing._transforms_ffmpeg import (
    CropRegion,
    apply_crop_transform,
    get_video_dimensions,
    transform_fill,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Apple Vision availability check
# ---------------------------------------------------------------------------

_vision_available: bool | None = None


def _check_vision_available() -> bool:
    """Check if Apple Vision framework is available."""
    global _vision_available
    if _vision_available is not None:
        return _vision_available

    if platform.system() != "Darwin":
        _vision_available = False
        return False

    try:
        from immich_memories.analysis.apple_vision import is_vision_available

        _vision_available = is_vision_available()
        return _vision_available
    except ImportError:
        _vision_available = False
        return False


# ---------------------------------------------------------------------------
# Face detector initialisation
# ---------------------------------------------------------------------------


def init_face_detectors() -> tuple[bool, object | None, object | None]:
    """Initialise face detection backends.

    Returns:
        Tuple of (use_vision, vision_detector, face_cascade).
    """
    use_vision = False
    vision_detector = None
    face_cascade = None

    # Try Apple Vision first (Mac only)
    if _check_vision_available():
        try:
            from immich_memories.analysis.apple_vision import VisionFaceDetector

            vision_detector = VisionFaceDetector(detect_landmarks=False)
            use_vision = True
            logger.info("Using Apple Vision for smart crop face detection")
        except Exception as e:
            logger.debug(f"Vision detector not available: {e}")

    # Fallback to OpenCV
    if not use_vision:
        try:
            cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            face_cascade = cv2.CascadeClassifier(cascade_path)
        except Exception as e:
            logger.warning(f"Could not load face cascade: {e}")

    return use_vision, vision_detector, face_cascade


# ---------------------------------------------------------------------------
# Face detection in video
# ---------------------------------------------------------------------------


def detect_faces_in_video(
    video_path: Path,
    use_vision: bool,
    vision_detector: object | None,
    face_cascade: object | None,
    sample_frames: int = 5,
) -> list[tuple[float, float]]:
    """Detect faces in a video and return their normalised positions.

    Uses Apple Vision on Mac (GPU accelerated), falls back to OpenCV.

    Args:
        video_path: Path to video.
        use_vision: Whether to use Apple Vision.
        vision_detector: Vision face detector instance.
        face_cascade: OpenCV cascade classifier instance.
        sample_frames: Number of frames to sample.

    Returns:
        List of (x, y) positions normalised to 0-1.
    """
    if not use_vision and face_cascade is None:
        return []

    cap = cv2.VideoCapture(str(video_path))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_indices = np.linspace(0, frame_count - 1, sample_frames, dtype=int)

    positions: list[tuple[float, float]] = []

    for idx in frame_indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if not ret:
            continue

        h, w = frame.shape[:2]

        if use_vision and vision_detector is not None:
            faces = vision_detector.detect_faces(frame, min_confidence=0.3)
            for face in faces:
                positions.append(face.center)
        elif face_cascade is not None:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(30, 30),
            )
            for x, y, fw, fh in faces:
                center_x = (x + fw / 2) / w
                center_y = (y + fh / 2) / h
                positions.append((center_x, center_y))

    cap.release()
    return positions


# ---------------------------------------------------------------------------
# Smart crop calculation
# ---------------------------------------------------------------------------


def calculate_smart_crop(
    src_w: int,
    src_h: int,
    target_resolution: tuple[int, int],
    face_positions: list[tuple[float, float]],
) -> CropRegion:
    """Calculate crop region that keeps faces centred.

    Args:
        src_w: Source width.
        src_h: Source height.
        target_resolution: Target (width, height).
        face_positions: Normalised face positions.

    Returns:
        CropRegion to apply.
    """
    target_w, target_h = target_resolution
    target_ar = target_w / target_h
    src_ar = src_w / src_h

    # Calculate face centroid
    if face_positions:
        avg_x = sum(p[0] for p in face_positions) / len(face_positions)
        avg_y = sum(p[1] for p in face_positions) / len(face_positions)
    else:
        avg_x, avg_y = 0.5, 0.5

    # Determine crop dimensions
    if src_ar > target_ar:
        crop_h = src_h
        crop_w = int(src_h * target_ar)
    else:
        crop_w = src_w
        crop_h = int(src_w / target_ar)

    # Position crop centred on faces (with bounds checking)
    face_x = int(avg_x * src_w)
    face_y = int(avg_y * src_h)

    crop_x = max(0, min(face_x - crop_w // 2, src_w - crop_w))
    crop_y = max(0, min(face_y - crop_h // 2, src_h - crop_h))

    return CropRegion(
        x=crop_x,
        y=crop_y,
        width=crop_w,
        height=crop_h,
    )


# ---------------------------------------------------------------------------
# High-level smart crop transform
# ---------------------------------------------------------------------------


def transform_smart_crop(
    input_path: Path,
    output_path: Path,
    target_resolution: tuple[int, int],
    face_positions: list[tuple[float, float]] | None,
    use_vision: bool,
    vision_detector: object | None,
    face_cascade: object | None,
) -> Path:
    """Transform using smart crop that keeps faces centred.

    Args:
        input_path: Input video path.
        output_path: Output video path.
        target_resolution: Target (width, height).
        face_positions: Known face positions (or None to auto-detect).
        use_vision: Whether to use Apple Vision.
        vision_detector: Vision face detector instance.
        face_cascade: OpenCV cascade classifier instance.

    Returns:
        Path to transformed video.
    """
    if face_positions is None:
        face_positions = detect_faces_in_video(
            input_path, use_vision, vision_detector, face_cascade
        )

    if not face_positions:
        return transform_fill(input_path, output_path, target_resolution)

    src_w, src_h = get_video_dimensions(input_path)
    if src_w == 0 or src_h == 0:
        return transform_fill(input_path, output_path, target_resolution)

    crop_region = calculate_smart_crop(src_w, src_h, target_resolution, face_positions)

    return apply_crop_transform(input_path, output_path, crop_region, target_resolution)
