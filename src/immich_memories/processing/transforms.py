"""Aspect ratio and scaling transforms."""

from __future__ import annotations

import logging
import platform
import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

import cv2
import numpy as np

from immich_memories.config import get_config
from immich_memories.processing.hardware import (
    HWAccelBackend,
    HWAccelCapabilities,
    detect_hardware_acceleration,
    get_ffmpeg_encoder,
    get_ffmpeg_hwaccel_args,
)

logger = logging.getLogger(__name__)

# Check for Apple Vision availability
_vision_available = None


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


# Cache hardware capabilities
_hw_caps: HWAccelCapabilities | None = None


def _get_hw_caps() -> HWAccelCapabilities:
    """Get cached hardware capabilities."""
    global _hw_caps
    if _hw_caps is None:
        _hw_caps = detect_hardware_acceleration()
    return _hw_caps


def _build_encode_args(
    hw_caps: HWAccelCapabilities | None,
    config,
    codec: str = "h264",
) -> list[str]:
    """Build FFmpeg encoding arguments with hardware acceleration if available.

    Args:
        hw_caps: Hardware capabilities.
        config: Configuration object.
        codec: Video codec.

    Returns:
        List of FFmpeg arguments.
    """
    args = []

    if hw_caps and hw_caps.has_encoding and config.hardware.enabled:
        encoder, encoder_args = get_ffmpeg_encoder(
            hw_caps,
            codec=codec,
            preset=config.hardware.encoder_preset,
        )
        args.extend(["-c:v", encoder])
        args.extend(encoder_args)

        # Quality parameter varies by encoder
        if "nvenc" in encoder:
            args.extend(["-cq", str(config.output.crf)])
        elif "videotoolbox" not in encoder:  # VideoToolbox uses -q:v set in encoder_args
            if "vaapi" in encoder or "qsv" in encoder:
                args.extend(["-global_quality", str(config.output.crf)])
            else:
                args.extend(["-crf", str(config.output.crf)])

        logger.debug(f"Using hardware encoder: {encoder}")
    else:
        args.extend(["-c:v", "libx264"])
        args.extend(["-preset", "medium"])
        args.extend(["-crf", str(config.output.crf)])

    args.extend(["-c:a", "aac", "-b:a", "128k"])
    args.extend(["-movflags", "+faststart"])

    return args


class ScaleMode(str, Enum):
    """Scaling mode for aspect ratio conversion."""

    FIT = "fit"  # Letterbox/pillarbox with blur background
    FILL = "fill"  # Crop to fill
    SMART_CROP = "smart_crop"  # Crop keeping faces centered


class Orientation(str, Enum):
    """Output orientation."""

    LANDSCAPE = "landscape"  # 16:9
    PORTRAIT = "portrait"  # 9:16
    SQUARE = "square"  # 1:1


ASPECT_RATIOS = {
    Orientation.LANDSCAPE: (16, 9),
    Orientation.PORTRAIT: (9, 16),
    Orientation.SQUARE: (1, 1),
}


@dataclass
class CropRegion:
    """A crop region within a frame."""

    x: int
    y: int
    width: int
    height: int

    @property
    def center(self) -> tuple[int, int]:
        """Get center point."""
        return (self.x + self.width // 2, self.y + self.height // 2)

    def to_ffmpeg_filter(self) -> str:
        """Convert to FFmpeg crop filter string."""
        return f"crop={self.width}:{self.height}:{self.x}:{self.y}"


class AspectRatioTransformer:
    """Transform videos to different aspect ratios."""

    def __init__(
        self,
        target_orientation: Orientation = Orientation.LANDSCAPE,
        scale_mode: ScaleMode = ScaleMode.FIT,
        target_resolution: tuple[int, int] | None = None,
    ):
        """Initialize the transformer.

        Args:
            target_orientation: Target aspect ratio.
            scale_mode: How to handle aspect ratio mismatch.
            target_resolution: Target resolution (width, height).
        """
        self.target_orientation = target_orientation
        self.scale_mode = scale_mode

        if target_resolution is None:
            config = get_config()
            self.target_resolution = config.output.resolution_tuple
        else:
            self.target_resolution = target_resolution

        # Adjust resolution for orientation
        w, h = self.target_resolution
        ar = ASPECT_RATIOS[target_orientation]
        if target_orientation == Orientation.PORTRAIT:
            self.target_resolution = (h * ar[0] // ar[1], h)
        elif target_orientation == Orientation.SQUARE:
            self.target_resolution = (min(w, h), min(w, h))

        # Load face detector for smart crop
        # Use Apple Vision on Mac (GPU accelerated), fallback to OpenCV
        self._use_vision = False
        self._vision_detector = None
        self._face_cascade = None

        if scale_mode == ScaleMode.SMART_CROP:
            # Try Apple Vision first (Mac only)
            if _check_vision_available():
                try:
                    from immich_memories.analysis.apple_vision import VisionFaceDetector

                    self._vision_detector = VisionFaceDetector(detect_landmarks=False)
                    self._use_vision = True
                    logger.info("Using Apple Vision for smart crop face detection")
                except Exception as e:
                    logger.debug(f"Vision detector not available: {e}")

            # Fallback to OpenCV
            if not self._use_vision:
                try:
                    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                    self._face_cascade = cv2.CascadeClassifier(cascade_path)
                except Exception as e:
                    logger.warning(f"Could not load face cascade: {e}")

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
        if output_path is None:
            output_dir = Path(tempfile.gettempdir()) / "immich_memories" / "transformed"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"transformed_{input_path.stem}.mp4"

        if self.scale_mode == ScaleMode.FIT:
            return self._transform_fit(input_path, output_path)
        elif self.scale_mode == ScaleMode.FILL:
            return self._transform_fill(input_path, output_path)
        elif self.scale_mode == ScaleMode.SMART_CROP:
            return self._transform_smart_crop(input_path, output_path, face_positions)
        else:
            return self._transform_fit(input_path, output_path)

    def _get_video_dimensions(self, video_path: Path) -> tuple[int, int]:
        """Get video dimensions."""
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(video_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return (0, 0)

        try:
            parts = result.stdout.strip().split(",")
            return (int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            return (0, 0)

    def _transform_fit(self, input_path: Path, output_path: Path) -> Path:
        """Transform using letterbox/pillarbox with blurred background.

        Uses hardware acceleration for encoding when available.

        Args:
            input_path: Input video path.
            output_path: Output video path.

        Returns:
            Path to output video.
        """
        target_w, target_h = self.target_resolution
        config = get_config()
        hw_caps = _get_hw_caps() if config.hardware.enabled else None

        # Build command
        cmd = ["ffmpeg", "-y"]

        # Add hardware decode if available
        if hw_caps and hw_caps.has_decoding and config.hardware.gpu_decode:
            hwaccel_args = get_ffmpeg_hwaccel_args(hw_caps, operation="decode")
            cmd.extend(hwaccel_args)

        cmd.extend(["-i", str(input_path)])

        # Complex filter for blur background + scaled overlay
        # Note: Complex filters typically run on CPU, but encoding uses GPU
        filter_complex = (
            f"[0:v]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h},boxblur=luma_radius=150:chroma_radius=150:luma_power=3:chroma_power=3[bg];"
            f"[0:v]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
        )

        cmd.extend(["-filter_complex", filter_complex])

        # Add hardware encoding args
        encode_args = _build_encode_args(hw_caps, config)
        cmd.extend(encode_args)

        cmd.append(str(output_path))

        logger.debug(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            # Retry without hardware acceleration if it failed
            if hw_caps and hw_caps.has_encoding:
                logger.warning("Hardware encoding failed, falling back to software")
                return self._transform_fit_software(input_path, output_path)
            logger.error(f"FFmpeg error: {result.stderr}")
            raise RuntimeError(f"Failed to transform video: {result.stderr}")

        return output_path

    def _transform_fit_software(self, input_path: Path, output_path: Path) -> Path:
        """Fallback software-only transform for letterbox/pillarbox."""
        target_w, target_h = self.target_resolution
        config = get_config()

        filter_complex = (
            f"[0:v]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h},boxblur=luma_radius=150:chroma_radius=150:luma_power=3:chroma_power=3[bg];"
            f"[0:v]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-filter_complex",
            filter_complex,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            str(config.output.crf),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to transform video: {result.stderr}")
        return output_path

    def _transform_fill(self, input_path: Path, output_path: Path) -> Path:
        """Transform using center crop to fill frame.

        Uses hardware acceleration when available.

        Args:
            input_path: Input video path.
            output_path: Output video path.

        Returns:
            Path to output video.
        """
        target_w, target_h = self.target_resolution
        config = get_config()
        hw_caps = _get_hw_caps() if config.hardware.enabled else None

        # Build command
        cmd = ["ffmpeg", "-y"]

        # Add hardware decode if available
        if hw_caps and hw_caps.has_decoding and config.hardware.gpu_decode:
            hwaccel_args = get_ffmpeg_hwaccel_args(hw_caps, operation="decode")
            cmd.extend(hwaccel_args)

        cmd.extend(["-i", str(input_path)])

        # Scale up and crop to target
        # For NVIDIA, can use scale_cuda for GPU-accelerated scaling
        if hw_caps and hw_caps.backend == HWAccelBackend.NVIDIA and hw_caps.supports_scaling:
            # NVIDIA CUDA filter chain
            filter_str = (
                f"hwupload_cuda,"
                f"scale_cuda={target_w}:{target_h}:force_original_aspect_ratio=increase,"
                f"hwdownload,format=nv12,"
                f"crop={target_w}:{target_h}"
            )
        else:
            filter_str = (
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
                f"crop={target_w}:{target_h}"
            )

        cmd.extend(["-vf", filter_str])

        # Add hardware encoding args
        encode_args = _build_encode_args(hw_caps, config)
        cmd.extend(encode_args)

        cmd.append(str(output_path))

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            # Fallback to software if hardware failed
            if hw_caps and (hw_caps.has_encoding or hw_caps.supports_scaling):
                logger.warning("Hardware processing failed, falling back to software")
                return self._transform_fill_software(input_path, output_path)
            raise RuntimeError(f"Failed to transform video: {result.stderr}")

        return output_path

    def _transform_fill_software(self, input_path: Path, output_path: Path) -> Path:
        """Fallback software-only transform for fill mode."""
        target_w, target_h = self.target_resolution
        config = get_config()

        filter_str = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h}"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-vf",
            filter_str,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            str(config.output.crf),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to transform video: {result.stderr}")
        return output_path

    def _transform_smart_crop(
        self,
        input_path: Path,
        output_path: Path,
        face_positions: list[tuple[float, float]] | None = None,
    ) -> Path:
        """Transform using smart crop that keeps faces centered.

        Args:
            input_path: Input video path.
            output_path: Output video path.
            face_positions: Known face positions.

        Returns:
            Path to output video.
        """
        # If no face positions provided, detect them
        if face_positions is None:
            face_positions = self._detect_faces_in_video(input_path)

        if not face_positions:
            # Fall back to center crop if no faces found
            return self._transform_fill(input_path, output_path)

        # Calculate crop region based on face positions
        src_w, src_h = self._get_video_dimensions(input_path)
        if src_w == 0 or src_h == 0:
            return self._transform_fill(input_path, output_path)

        crop_region = self._calculate_smart_crop(src_w, src_h, face_positions)

        return self._apply_crop_transform(input_path, output_path, crop_region)

    def _detect_faces_in_video(
        self,
        video_path: Path,
        sample_frames: int = 5,
    ) -> list[tuple[float, float]]:
        """Detect faces in a video and return their normalized positions.

        Uses Apple Vision on Mac (GPU accelerated), falls back to OpenCV.

        Args:
            video_path: Path to video.
            sample_frames: Number of frames to sample.

        Returns:
            List of (x, y) positions normalized to 0-1.
        """
        # Check if any detector is available
        if not self._use_vision and self._face_cascade is None:
            return []

        cap = cv2.VideoCapture(str(video_path))
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        frame_indices = np.linspace(0, frame_count - 1, sample_frames, dtype=int)

        positions = []

        for idx in frame_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret:
                continue

            h, w = frame.shape[:2]

            # Use Apple Vision if available (GPU accelerated)
            if self._use_vision and self._vision_detector is not None:
                faces = self._vision_detector.detect_faces(frame, min_confidence=0.3)
                for face in faces:
                    # face.center already returns top-left origin coordinates
                    positions.append(face.center)
            elif self._face_cascade is not None:
                # Fallback to OpenCV
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = self._face_cascade.detectMultiScale(
                    gray,
                    scaleFactor=1.1,
                    minNeighbors=5,
                    minSize=(30, 30),
                )
                for x, y, fw, fh in faces:
                    # Normalize to 0-1
                    center_x = (x + fw / 2) / w
                    center_y = (y + fh / 2) / h
                    positions.append((center_x, center_y))

        cap.release()
        return positions

    def _calculate_smart_crop(
        self,
        src_w: int,
        src_h: int,
        face_positions: list[tuple[float, float]],
    ) -> CropRegion:
        """Calculate crop region that keeps faces centered.

        Args:
            src_w: Source width.
            src_h: Source height.
            face_positions: Normalized face positions.

        Returns:
            CropRegion to apply.
        """
        target_w, target_h = self.target_resolution
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
            # Source is wider - crop width
            crop_h = src_h
            crop_w = int(src_h * target_ar)
        else:
            # Source is taller - crop height
            crop_w = src_w
            crop_h = int(src_w / target_ar)

        # Position crop centered on faces (with bounds checking)
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

    def _apply_crop_transform(
        self,
        input_path: Path,
        output_path: Path,
        crop: CropRegion,
    ) -> Path:
        """Apply crop and scale to target resolution.

        Uses hardware acceleration when available.

        Args:
            input_path: Input video path.
            output_path: Output video path.
            crop: Crop region to apply.

        Returns:
            Path to output video.
        """
        target_w, target_h = self.target_resolution
        config = get_config()
        hw_caps = _get_hw_caps() if config.hardware.enabled else None

        # Build command
        cmd = ["ffmpeg", "-y"]

        # Add hardware decode if available
        if hw_caps and hw_caps.has_decoding and config.hardware.gpu_decode:
            hwaccel_args = get_ffmpeg_hwaccel_args(hw_caps, operation="decode")
            cmd.extend(hwaccel_args)

        cmd.extend(["-i", str(input_path)])

        # Crop and scale filter
        filter_str = (
            f"crop={crop.width}:{crop.height}:{crop.x}:{crop.y},scale={target_w}:{target_h}"
        )

        cmd.extend(["-vf", filter_str])

        # Add hardware encoding args
        encode_args = _build_encode_args(hw_caps, config)
        cmd.extend(encode_args)

        cmd.append(str(output_path))

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            # Fallback to software
            if hw_caps and hw_caps.has_encoding:
                logger.warning("Hardware encoding failed, falling back to software")
                return self._apply_crop_transform_software(input_path, output_path, crop)
            raise RuntimeError(f"Failed to transform video: {result.stderr}")

        return output_path

    def _apply_crop_transform_software(
        self,
        input_path: Path,
        output_path: Path,
        crop: CropRegion,
    ) -> Path:
        """Fallback software-only crop transform."""
        target_w, target_h = self.target_resolution
        config = get_config()

        filter_str = (
            f"crop={crop.width}:{crop.height}:{crop.x}:{crop.y},scale={target_w}:{target_h}"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-vf",
            filter_str,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            str(config.output.crf),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to transform video: {result.stderr}")
        return output_path


def apply_aspect_ratio_transform(
    input_path: Path,
    output_path: Path | None = None,
    orientation: Literal["landscape", "portrait", "square"] = "landscape",
    scale_mode: Literal["fit", "fill", "smart_crop"] = "fit",
    resolution: tuple[int, int] | None = None,
) -> Path:
    """Convenience function to transform a video's aspect ratio.

    Args:
        input_path: Path to input video.
        output_path: Path for output video.
        orientation: Target orientation.
        scale_mode: Scaling mode.
        resolution: Target resolution.

    Returns:
        Path to transformed video.
    """
    transformer = AspectRatioTransformer(
        target_orientation=Orientation(orientation),
        scale_mode=ScaleMode(scale_mode),
        target_resolution=resolution,
    )
    return transformer.transform(input_path, output_path)


def add_date_overlay(
    input_path: Path,
    output_path: Path,
    date_text: str,
    position: Literal["bottom-left", "bottom-right", "top-left", "top-right"] = "bottom-right",
    font_size: int = 24,
    opacity: float = 0.7,
) -> Path:
    """Add a date overlay to a video.

    Args:
        input_path: Path to input video.
        output_path: Path for output video.
        date_text: Text to display.
        position: Corner position.
        font_size: Font size in points.
        opacity: Text opacity (0-1).

    Returns:
        Path to output video.
    """
    config = get_config()

    # Calculate position
    positions = {
        "bottom-left": "x=20:y=h-th-20",
        "bottom-right": "x=w-tw-20:y=h-th-20",
        "top-left": "x=20:y=20",
        "top-right": "x=w-tw-20:y=20",
    }
    pos_str = positions.get(position, positions["bottom-right"])

    # Build filter
    filter_str = (
        f"drawtext=text='{date_text}':"
        f"fontsize={font_size}:"
        f"fontcolor=white@{opacity}:"
        f"shadowcolor=black@0.5:"
        f"shadowx=2:shadowy=2:"
        f"{pos_str}"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        filter_str,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        str(config.output.crf),
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"Failed to add date overlay: {result.stderr}")

    return output_path
