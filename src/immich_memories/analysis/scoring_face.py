"""Face detection scoring for video frames.

Supports Apple Vision framework (GPU-accelerated on Mac) with
OpenCV cascade classifier fallback.
"""

from __future__ import annotations

import logging
import platform

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Check for Apple Vision framework availability
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
    """Compute face score using Apple Vision framework.

    Args:
        frame: BGR image.
        vision_detector: VisionFaceDetector instance.

    Returns:
        Tuple of (score, list of face center positions).
    """
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
    """Compute face score using OpenCV cascade classifier.

    Args:
        frame: BGR image.
        w: Frame width.
        h: Frame height.
        face_cascade: OpenCV CascadeClassifier instance (or None).

    Returns:
        Tuple of (score, list of face center positions).
    """
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
