"""Apple Vision framework integration for face detection on macOS.

Uses CoreML and the Vision framework via PyObjC for GPU-accelerated
face detection on Apple Silicon and Intel Macs.
"""

from __future__ import annotations

import logging
import platform
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

logger = logging.getLogger(__name__)

# Check if we're on macOS
IS_MACOS = platform.system() == "Darwin"

# Lazy imports for macOS frameworks
_vision_available = None
_Vision = None
_Quartz = None


@dataclass
class FaceDetection:
    """A detected face with bounding box and landmarks."""

    # Bounding box (normalized 0-1 coordinates, origin bottom-left in Vision)
    x: float
    y: float
    width: float
    height: float
    confidence: float = 1.0

    # Optional landmarks
    left_eye: tuple[float, float] | None = None
    right_eye: tuple[float, float] | None = None
    nose: tuple[float, float] | None = None
    mouth: tuple[float, float] | None = None

    @property
    def center(self) -> tuple[float, float]:
        """Get center point (normalized, origin top-left for compatibility)."""
        # Vision uses bottom-left origin, convert to top-left
        center_x = self.x + self.width / 2
        center_y = 1.0 - (self.y + self.height / 2)  # Flip Y
        return (center_x, center_y)

    @property
    def center_vision(self) -> tuple[float, float]:
        """Get center point in Vision coordinates (bottom-left origin)."""
        return (self.x + self.width / 2, self.y + self.height / 2)

    @property
    def area(self) -> float:
        """Get face area as fraction of frame."""
        return self.width * self.height


def _check_vision_available() -> bool:
    """Check if Vision framework is available."""
    global _vision_available, _Vision, _Quartz

    if _vision_available is not None:
        return _vision_available

    if not IS_MACOS:
        _vision_available = False
        return False

    try:
        import Quartz
        import Vision

        _Vision = Vision
        _Quartz = Quartz
        _vision_available = True
        logger.info("Apple Vision framework available")
        return True
    except ImportError as e:
        logger.debug(f"Vision framework not available: {e}")
        _vision_available = False
        return False


@lru_cache(maxsize=1)
def is_vision_available() -> bool:
    """Check if Apple Vision framework is available for face detection.

    Returns:
        True if Vision framework can be used.
    """
    return _check_vision_available()


from immich_memories.analysis.apple_vision_image import (  # noqa: E402
    create_cg_image_from_numpy as _create_cg_image_from_numpy,
)


class VisionFaceDetector:
    """Face detector using Apple Vision framework.

    Uses the Neural Engine on Apple Silicon for fast, GPU-accelerated
    face detection with optional landmark detection.
    """

    def __init__(self, detect_landmarks: bool = False):
        """Initialize the Vision face detector.

        Args:
            detect_landmarks: Whether to detect facial landmarks.
        """
        if not is_vision_available():
            raise RuntimeError("Apple Vision framework not available")

        self.detect_landmarks = detect_landmarks
        self._request = None
        self._handler = None

    def detect_faces(
        self,
        image: np.ndarray,
        min_confidence: float = 0.5,
    ) -> list[FaceDetection]:
        """Detect faces in an image using Vision framework.

        Args:
            image: BGR image from OpenCV (numpy array).
            min_confidence: Minimum confidence threshold.

        Returns:
            List of detected faces.
        """
        import Vision

        # Create CGImage from numpy array
        cg_image = _create_cg_image_from_numpy(image)
        if cg_image is None:
            logger.warning("Failed to create CGImage from numpy array")
            return []

        # Create request handler
        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)

        # Create face detection request
        if self.detect_landmarks:
            request = Vision.VNDetectFaceLandmarksRequest.alloc().init()
        else:
            request = Vision.VNDetectFaceRectanglesRequest.alloc().init()

        # Perform detection
        success, error = handler.performRequests_error_([request], None)

        if not success or error:
            logger.warning(f"Vision face detection failed: {error}")
            # Cleanup before early return
            del request
            del handler
            del cg_image
            return []

        # Process results
        results = request.results()
        if not results:
            # Cleanup before early return
            del request
            del handler
            del cg_image
            return []

        faces = []
        for observation in results:
            # Get bounding box (normalized, origin at bottom-left)
            bbox = observation.boundingBox()
            confidence = observation.confidence()

            if confidence < min_confidence:
                continue

            face = FaceDetection(
                x=bbox.origin.x,
                y=bbox.origin.y,
                width=bbox.size.width,
                height=bbox.size.height,
                confidence=confidence,
            )

            # Extract landmarks if available
            if self.detect_landmarks and hasattr(observation, "landmarks"):
                landmarks = observation.landmarks()
                if landmarks:
                    face = self._extract_landmarks(face, landmarks)

            faces.append(face)

        # Explicit cleanup of Vision framework objects to prevent memory leaks
        # PyObjC objects may not be freed immediately when going out of scope
        del request
        del handler
        del cg_image

        return faces

    def _extract_landmarks(
        self,
        face: FaceDetection,
        landmarks,
    ) -> FaceDetection:
        """Extract landmark positions from Vision landmarks.

        Args:
            face: Face detection to update.
            landmarks: VNFaceLandmarks2D object.

        Returns:
            Updated face detection with landmarks.
        """
        # Left eye
        if landmarks.leftEye():
            points = landmarks.leftEye().normalizedPoints()
            if points:
                # Get center of eye region
                center = self._get_landmark_center(points)
                face.left_eye = center

        # Right eye
        if landmarks.rightEye():
            points = landmarks.rightEye().normalizedPoints()
            if points:
                center = self._get_landmark_center(points)
                face.right_eye = center

        # Nose
        if landmarks.nose():
            points = landmarks.nose().normalizedPoints()
            if points:
                center = self._get_landmark_center(points)
                face.nose = center

        # Mouth (outer lips)
        if landmarks.outerLips():
            points = landmarks.outerLips().normalizedPoints()
            if points:
                center = self._get_landmark_center(points)
                face.mouth = center

        return face

    def _get_landmark_center(self, points) -> tuple[float, float]:
        """Get center point of landmark region.

        Args:
            points: Array of CGPoint objects.

        Returns:
            Center (x, y) normalized coordinates.
        """
        if not points:
            return (0.5, 0.5)

        # Points is a tuple of CGPoint
        x_sum = sum(p.x for p in points)
        y_sum = sum(p.y for p in points)
        count = len(points)

        return (x_sum / count, y_sum / count)
