"""Face detection scoring, and the two backends that provide it.

Apple Vision on macOS, OpenCV's Haar cascade everywhere else. Which one is
available is decided once and cached in `_use_vision`, because the probe
shells out and the answer cannot change inside a run.

`SceneScorer` imports these names into `analysis.scoring` and calls them
there, so tests that patch `analysis.scoring.check_vision_available` keep
working. Patching them here would not affect the scorer -- `from x import y`
binds y in the importing module at import time.
"""

from __future__ import annotations

import logging
import platform

import cv2
import numpy as np

logger = logging.getLogger(__name__)

__all__ = [
    "check_vision_available",
    "compute_face_score",
    "init_opencv_cascade",
    "init_vision_detector",
]


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
    except (ImportError, RuntimeError, OSError) as e:
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
    except (OSError, RuntimeError, AttributeError) as e:
        # WHY: AttributeError is what an OpenCV build without the Haar cascade API
        # (OpenCV 5) raises; face scoring must degrade, not take the run down.
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

    if len(faces) == 0:  # noqa: FURB115 — `not faces` crashes on numpy arrays (Linux OpenCV)
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
