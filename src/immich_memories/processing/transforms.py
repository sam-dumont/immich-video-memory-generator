"""Aspect ratio and scaling transforms.

Public API surface — delegates heavy lifting to helper modules:
  - transforms_ffmpeg.py   (fit / fill / crop FFmpeg pipelines, CropRegion)
  - transforms_smart_crop.py (face detection, smart crop calculation)
"""

from __future__ import annotations

import logging
import tempfile
from enum import StrEnum
from pathlib import Path
from typing import Literal

from immich_memories.config_models import HardwareAccelConfig
from immich_memories.processing.transforms_ffmpeg import (
    CropRegion,
    apply_crop_transform,
    get_video_dimensions,
    transform_fill,
    transform_fit,
)
from immich_memories.processing.transforms_ffmpeg import (
    add_date_overlay as _add_date_overlay,
)
from immich_memories.processing.transforms_smart_crop import (
    calculate_smart_crop,
    detect_faces_in_video,
    init_face_detectors,
    transform_smart_crop,
)
from immich_memories.security import validate_video_path

# Re-export helpers so existing ``from transforms import …`` keeps working.
__all__ = [
    "ASPECT_RATIOS",
    "AspectRatioTransformer",
    "CropRegion",
    "Orientation",
    "ScaleMode",
    "add_date_overlay",
    "apply_aspect_ratio_transform",
    "apply_crop_transform",
    "calculate_smart_crop",
    "detect_faces_in_video",
    "get_video_dimensions",
    "transform_fill",
    "transform_fit",
    "transform_smart_crop",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums & constants
# ---------------------------------------------------------------------------


class ScaleMode(StrEnum):
    """Scaling mode for aspect ratio conversion."""

    FIT = "fit"  # Letterbox/pillarbox with blur background
    FILL = "fill"  # Crop to fill
    SMART_CROP = "smart_crop"  # Crop keeping faces centered


class Orientation(StrEnum):
    """Output orientation."""

    LANDSCAPE = "landscape"  # 16:9
    PORTRAIT = "portrait"  # 9:16
    SQUARE = "square"  # 1:1


ASPECT_RATIOS = {
    Orientation.LANDSCAPE: (16, 9),
    Orientation.PORTRAIT: (9, 16),
    Orientation.SQUARE: (1, 1),
}


# ---------------------------------------------------------------------------
# AspectRatioTransformer
# ---------------------------------------------------------------------------


class AspectRatioTransformer:
    """Transform videos to different aspect ratios."""

    def __init__(
        self,
        target_orientation: Orientation = Orientation.LANDSCAPE,
        scale_mode: ScaleMode = ScaleMode.FIT,
        *,
        target_resolution: tuple[int, int],
        hardware_config: HardwareAccelConfig,
        output_crf: int,
    ):
        """Initialize the transformer.

        Args:
            target_orientation: Target aspect ratio.
            scale_mode: How to handle aspect ratio mismatch.
            target_resolution: Target resolution (width, height).
            hardware_config: Hardware acceleration settings.
            output_crf: CRF quality value for encoding.
        """
        self.target_orientation = target_orientation
        self.scale_mode = scale_mode
        self.target_resolution = target_resolution
        self.hardware_config = hardware_config
        self.output_crf = output_crf

        # Adjust resolution for orientation
        w, h = self.target_resolution
        ar = ASPECT_RATIOS[target_orientation]
        if target_orientation == Orientation.PORTRAIT:
            self.target_resolution = (h * ar[0] // ar[1], h)
        elif target_orientation == Orientation.SQUARE:
            self.target_resolution = (min(w, h), min(w, h))

        # Load face detector for smart crop
        self._use_vision = False
        self._vision_detector = None
        self._face_cascade = None

        if scale_mode == ScaleMode.SMART_CROP:
            (
                self._use_vision,
                self._vision_detector,
                self._face_cascade,
            ) = init_face_detectors()

    def get_target_size(self) -> tuple[int, int]:
        """Get the target output size."""
        return self.target_resolution

    def transform(
        self,
        input_path: Path,
        output_path: Path | None = None,
        face_positions: list[tuple[float, float]] | None = None,
    ) -> Path:
        """Transform a video to the target aspect ratio.

        Args:
            input_path: Path to input video.
            output_path: Path for output video.
            face_positions: Known face positions (normalized 0-1 coordinates).

        Returns:
            Path to transformed video.
        """
        input_path = validate_video_path(input_path, must_exist=True)

        if output_path is None:
            output_dir = Path(tempfile.gettempdir()) / "immich_memories" / "transformed"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"transformed_{input_path.stem}.mp4"

        if self.scale_mode == ScaleMode.FIT:
            return transform_fit(
                input_path,
                output_path,
                self.target_resolution,
                self.hardware_config,
                self.output_crf,
            )
        elif self.scale_mode == ScaleMode.FILL:
            return transform_fill(
                input_path,
                output_path,
                self.target_resolution,
                self.hardware_config,
                self.output_crf,
            )
        elif self.scale_mode == ScaleMode.SMART_CROP:
            return transform_smart_crop(
                input_path,
                output_path,
                self.target_resolution,
                face_positions,
                self._use_vision,
                self._vision_detector,
                self._face_cascade,
                hardware_config=self.hardware_config,
                output_crf=self.output_crf,
            )
        return transform_fit(
            input_path,
            output_path,
            self.target_resolution,
            self.hardware_config,
            self.output_crf,
        )

    # Keep legacy private helpers as thin delegates so subclasses still work.

    def _get_video_dimensions(self, video_path: Path) -> tuple[int, int]:
        """Get video dimensions."""
        return get_video_dimensions(video_path)

    def _transform_fit(self, input_path: Path, output_path: Path) -> Path:
        return transform_fit(
            input_path,
            output_path,
            self.target_resolution,
            self.hardware_config,
            self.output_crf,
        )

    def _transform_fit_software(self, input_path: Path, output_path: Path) -> Path:
        return transform_fit(
            input_path,
            output_path,
            self.target_resolution,
            self.hardware_config,
            self.output_crf,
        )

    def _transform_fill(self, input_path: Path, output_path: Path) -> Path:
        return transform_fill(
            input_path,
            output_path,
            self.target_resolution,
            self.hardware_config,
            self.output_crf,
        )

    def _transform_fill_software(self, input_path: Path, output_path: Path) -> Path:
        return transform_fill(
            input_path,
            output_path,
            self.target_resolution,
            self.hardware_config,
            self.output_crf,
        )

    def _transform_smart_crop(
        self,
        input_path: Path,
        output_path: Path,
        face_positions: list[tuple[float, float]] | None = None,
    ) -> Path:
        return transform_smart_crop(
            input_path,
            output_path,
            self.target_resolution,
            face_positions,
            self._use_vision,
            self._vision_detector,
            self._face_cascade,
            hardware_config=self.hardware_config,
            output_crf=self.output_crf,
        )

    def _detect_faces_in_video(
        self,
        video_path: Path,
        sample_frames: int = 5,
    ) -> list[tuple[float, float]]:
        return detect_faces_in_video(
            video_path,
            self._use_vision,
            self._vision_detector,
            self._face_cascade,
            sample_frames,
        )

    def _calculate_smart_crop(
        self,
        src_w: int,
        src_h: int,
        face_positions: list[tuple[float, float]],
    ) -> CropRegion:
        return calculate_smart_crop(src_w, src_h, self.target_resolution, face_positions)

    def _apply_crop_transform(
        self,
        input_path: Path,
        output_path: Path,
        crop: CropRegion,
    ) -> Path:
        return apply_crop_transform(
            input_path,
            output_path,
            crop,
            self.target_resolution,
            self.hardware_config,
            self.output_crf,
        )

    def _apply_crop_transform_software(
        self,
        input_path: Path,
        output_path: Path,
        crop: CropRegion,
    ) -> Path:
        return apply_crop_transform(
            input_path,
            output_path,
            crop,
            self.target_resolution,
            self.hardware_config,
            self.output_crf,
        )


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


def apply_aspect_ratio_transform(
    input_path: Path,
    output_path: Path | None = None,
    orientation: Literal["landscape", "portrait", "square"] = "landscape",
    scale_mode: Literal["fit", "fill", "smart_crop"] = "fit",
    *,
    resolution: tuple[int, int],
    hardware_config: HardwareAccelConfig,
    output_crf: int,
) -> Path:
    """Convenience function to transform a video's aspect ratio.

    Args:
        input_path: Path to input video.
        output_path: Path for output video.
        orientation: Target orientation.
        scale_mode: Scaling mode.
        resolution: Target resolution.
        hardware_config: Hardware acceleration settings.
        output_crf: CRF quality value for encoding.

    Returns:
        Path to transformed video.
    """
    return AspectRatioTransformer(
        target_orientation=Orientation(orientation),
        scale_mode=ScaleMode(scale_mode),
        target_resolution=resolution,
        hardware_config=hardware_config,
        output_crf=output_crf,
    ).transform(input_path, output_path)


def add_date_overlay(
    input_path: Path,
    output_path: Path,
    date_text: str,
    output_crf: int,
    position: Literal["bottom-left", "bottom-right", "top-left", "top-right"] = "bottom-right",
    font_size: int = 24,
    opacity: float = 0.7,
) -> Path:
    """Add a date overlay to a video.

    Args:
        input_path: Path to input video.
        output_path: Path for output video.
        date_text: Text to display.
        output_crf: CRF quality value for encoding.
        position: Corner position.
        font_size: Font size in points.
        opacity: Text opacity (0-1).

    Returns:
        Path to output video.
    """
    return _add_date_overlay(
        input_path, output_path, date_text, output_crf, position, font_size, opacity
    )
