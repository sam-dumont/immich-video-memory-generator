"""Video assembly and final export.

This module provides the VideoAssembler class for combining video clips into a
final memory video with title screens, transitions, and audio.

Assembly Pipeline:
    1. Generate title screen (GPU-accelerated if available)
    2. Process video clips (normalize resolution, framerate)
    3. Apply transitions (smart, crossfade, or cuts)
    4. Generate ending screen with dominant color fade
    5. Encode final video (HEVC with HDR preservation)

Transition Types:
    - SMART: Intelligent mix of crossfades and cuts for variety
    - CROSSFADE: Smooth fade transitions between all clips
    - CUT: Hard cuts with proper re-encoding (handles codec mismatches)

All assembly methods properly handle:
    - Different input codecs (H.264, HEVC, etc.)
    - Different resolutions (auto-scaled to common resolution)
    - Different frame rates (normalized)
    - Missing audio streams (silent audio added as needed)

Example:
    ```python
    from immich_memories.processing import (
        VideoAssembler,
        AssemblySettings,
        TitleScreenSettings,
    )

    settings = AssemblySettings(
        transition=TransitionType.CROSSFADE,
        preserve_hdr=True,
        title_screens=TitleScreenSettings(year=2024),
    )

    assembler = VideoAssembler(settings)
    output = assembler.assemble(clips, Path("output.mp4"))
    ```
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from collections import Counter, OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from threading import Thread
from typing import IO, Any

from immich_memories.config import get_config
from immich_memories.processing.clips import ClipSegment
from immich_memories.security import validate_video_path
from immich_memories.tracking.run_database import RunDatabase

logger = logging.getLogger(__name__)


class JobCancelledException(Exception):
    """Raised when a job is cancelled by user request."""

    pass


# Memory optimization: Chunked assembly thresholds
# When clip count exceeds threshold, process in batches to avoid OOM
CHUNKED_ASSEMBLY_THRESHOLD = 8  # Use chunking if > 8 clips
CHUNK_SIZE = 4  # Process 4 clips per batch (keeps FFmpeg memory ~1GB per batch at 4K)
MAX_FACE_CACHE_SIZE = 50  # Max entries in face detection cache to prevent unbounded growth


def _detect_hdr_type(video_path: Path) -> str | None:
    """Detect the HDR type of a video file.

    Returns:
        "hlg" for HLG (iPhone Dolby Vision 8.4)
        "pq" for HDR10/HDR10+ (Samsung, Pixel, etc.)
        None if SDR or unknown
    """
    video_path = validate_video_path(video_path, must_exist=True)
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=color_transfer",
                "-of",
                "json",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            import json

            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            if streams:
                color_trc = streams[0].get("color_transfer", "")
                if color_trc == "arib-std-b67":
                    return "hlg"  # iPhone HLG / Dolby Vision 8.4
                elif color_trc == "smpte2084":
                    return "pq"  # HDR10 / HDR10+ (Samsung, Pixel)
                elif color_trc in ("bt2020-10", "bt2020-12"):
                    return "pq"  # Assume PQ for BT.2020
    except Exception as e:
        logger.debug(f"HDR detection failed for {video_path}: {e}")
    return None


def _get_dominant_hdr_type(clips: list) -> str:
    """Detect the dominant HDR type from a list of clips.

    Returns "hlg" or "pq" based on what most clips use.
    Defaults to "hlg" if detection fails (iPhone is most common).
    """
    hdr_types: dict[str, int] = {"hlg": 0, "pq": 0}

    for clip in clips:
        path = clip.path if hasattr(clip, "path") else clip
        hdr_type = _detect_hdr_type(path)
        if hdr_type:
            hdr_types[hdr_type] += 1

    # Return dominant type, default to HLG if tied or none detected
    if hdr_types["pq"] > hdr_types["hlg"]:
        logger.info(f"Detected HDR10/PQ format (Android/Samsung/Pixel) - {hdr_types['pq']} clips")
        return "pq"
    elif hdr_types["hlg"] > 0:
        logger.info(f"Detected HLG format (iPhone) - {hdr_types['hlg']} clips")
        return "hlg"
    else:
        logger.info("No HDR detected, defaulting to HLG colorspace")
        return "hlg"


def _get_colorspace_filter(hdr_type: str) -> str:
    """Get the setparams filter string for the given HDR type.

    Args:
        hdr_type: "hlg" for HLG, "pq" for HDR10/HDR10+

    Returns:
        FFmpeg setparams filter string
    """
    if hdr_type == "pq":
        # HDR10/HDR10+ (Samsung, Pixel, etc.) - uses PQ/SMPTE2084 transfer
        return ",setparams=colorspace=bt2020nc:color_primaries=bt2020:color_trc=smpte2084"
    else:
        # HLG (iPhone Dolby Vision 8.4) - uses ARIB STD-B67 transfer
        return ",setparams=colorspace=bt2020nc:color_primaries=bt2020:color_trc=arib-std-b67"


def _get_hdr_conversion_filter(source_type: str | None, target_type: str) -> str:
    """Get filter to convert between HDR formats (HLG <-> PQ) or SDR -> HDR.

    Uses zscale for proper colorspace and transfer function conversion.
    Falls back to colorspace filter if zscale unavailable.

    Args:
        source_type: Source HDR type ("hlg", "pq", "sdr", or None for unknown)
        target_type: Target HDR type ("hlg" or "pq")

    Returns:
        FFmpeg filter string for conversion, or empty string if no conversion needed
    """
    # No conversion needed if source matches target
    if source_type == target_type:
        return ""

    # Check if zscale is available (required for proper conversion)
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
        )
        has_zscale = "zscale" in result.stdout
    except Exception:
        has_zscale = False

    # SDR -> HDR conversion (upscale SDR to HDR colorspace)
    if source_type is None or source_type == "sdr":
        if target_type == "hlg":
            # SDR (BT.709) -> HLG (BT.2020)
            if has_zscale:
                logger.debug("Converting SDR -> HLG")
                return ",zscale=transfer=arib-std-b67:transferin=bt709:primaries=bt2020:primariesin=bt709:matrix=bt2020nc:matrixin=bt709"
            else:
                logger.warning("zscale not available - SDR to HDR conversion may look washed out")
                return ""
        elif target_type == "pq":
            # SDR (BT.709) -> PQ/HDR10 (BT.2020)
            if has_zscale:
                logger.debug("Converting SDR -> PQ/HDR10")
                return ",zscale=transfer=smpte2084:transferin=bt709:primaries=bt2020:primariesin=bt709:matrix=bt2020nc:matrixin=bt709"
            else:
                logger.warning("zscale not available - SDR to HDR conversion may look washed out")
                return ""
        return ""

    # HDR -> HDR conversion (HLG <-> PQ)
    if source_type == "hlg" and target_type == "pq":
        # HLG (iPhone) -> PQ (HDR10)
        if has_zscale:
            return ",zscale=transfer=smpte2084:transferin=arib-std-b67:primaries=bt2020:primariesin=bt2020:matrix=bt2020nc:matrixin=bt2020nc"
        else:
            logger.warning("zscale not available - HDR conversion may not be accurate")
            return ""
    elif source_type == "pq" and target_type == "hlg":
        # PQ (HDR10) -> HLG (iPhone)
        if has_zscale:
            return ",zscale=transfer=arib-std-b67:transferin=smpte2084:primaries=bt2020:primariesin=bt2020:matrix=bt2020nc:matrixin=bt2020nc"
        else:
            logger.warning("zscale not available - HDR conversion may not be accurate")
            return ""

    return ""


def _get_clip_hdr_types(clips: list) -> list[str | None]:
    """Get HDR type for each clip in the list.

    Returns:
        List of HDR types ("hlg", "pq", or None) for each clip
    """
    hdr_types = []
    for clip in clips:
        path = clip.path if hasattr(clip, "path") else clip
        hdr_type = _detect_hdr_type(path)
        hdr_types.append(hdr_type)
    return hdr_types


def _get_gpu_encoder_args(
    crf: int = 23, preserve_hdr: bool = False, hdr_type: str = "hlg"
) -> list[str]:
    """Get GPU-accelerated encoder arguments.

    Uses hardware encoding when available:
    - macOS: hevc_videotoolbox (Apple Silicon GPU)
    - NVIDIA: hevc_nvenc (CUDA)
    - Fallback: libx265/libx264 (CPU)

    Args:
        crf: Quality level (lower = better, 0-51)
        preserve_hdr: If True, use 10-bit HDR settings
        hdr_type: "hlg" for iPhone HLG, "pq" for Android HDR10/HDR10+

    Returns:
        List of FFmpeg encoder arguments.
    """
    import sys

    # Select correct transfer function based on HDR type
    # HLG (iPhone): arib-std-b67
    # HDR10/HDR10+ (Android/Samsung/Pixel): smpte2084 (PQ)
    color_trc = "smpte2084" if hdr_type == "pq" else "arib-std-b67"

    # macOS: Use VideoToolbox (GPU accelerated)
    if sys.platform == "darwin":
        # Map CRF to VideoToolbox quality (inverse relationship)
        # CRF 18 = high quality, CRF 28 = lower quality
        # VT quality: 0-100, higher = better
        vt_quality = max(10, min(90, 100 - (crf * 3)))

        if preserve_hdr:
            return [
                "-c:v",
                "hevc_videotoolbox",
                "-q:v",
                str(vt_quality),
                "-pix_fmt",
                "p010le",  # 10-bit
                "-tag:v",
                "hvc1",
                "-colorspace",
                "bt2020nc",
                "-color_primaries",
                "bt2020",
                "-color_trc",
                color_trc,
            ]
        else:
            return [
                "-c:v",
                "hevc_videotoolbox",
                "-q:v",
                str(vt_quality),
                "-tag:v",
                "hvc1",
            ]

    # Check for NVIDIA NVENC
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
        )
        if "hevc_nvenc" in result.stdout:
            if preserve_hdr:
                return [
                    "-c:v",
                    "hevc_nvenc",
                    "-preset",
                    "p4",
                    "-rc",
                    "constqp",
                    "-qp",
                    str(crf),
                    "-pix_fmt",
                    "p010le",  # 10-bit
                    "-tag:v",
                    "hvc1",
                    "-colorspace",
                    "bt2020nc",
                    "-color_primaries",
                    "bt2020",
                    "-color_trc",
                    color_trc,
                ]
            else:
                return [
                    "-c:v",
                    "hevc_nvenc",
                    "-preset",
                    "p4",
                    "-rc",
                    "constqp",
                    "-qp",
                    str(crf),
                    "-tag:v",
                    "hvc1",
                ]
    except Exception:
        pass

    # Fallback to CPU encoding
    if preserve_hdr:
        # x265 transfer parameter name
        x265_transfer = "smpte2084" if hdr_type == "pq" else "arib-std-b67"
        return [
            "-c:v",
            "libx265",
            "-preset",
            "medium",
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p10le",
            "-tag:v",
            "hvc1",
            "-colorspace",
            "bt2020nc",
            "-color_primaries",
            "bt2020",
            "-color_trc",
            color_trc,
            "-x265-params",
            f"hdr-opt=1:repeat-headers=1:colorprim=bt2020:transfer={x265_transfer}:colormatrix=bt2020nc",
        ]
    else:
        return [
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            str(crf),
        ]


def _get_aspect_ratio_filter(
    clip_index: int,
    src_w: int,
    src_h: int,
    target_w: int,
    target_h: int,
    face_center: tuple[float, float] | None,
    pix_fmt: str = "yuv420p",
    target_fps: int = 60,
    rotation_filter: str = "",
    hdr_conversion: str = "",
    colorspace_filter: str = "",
    output_suffix: str = "scaled",
    scale_mode: str = "blur",
) -> str:
    """Generate FFmpeg filter for aspect ratio handling.

    Handles portrait/landscape mismatch with different modes:
    - "blur": Blur background (Instagram-style)
    - "smart_zoom": Smart crop centered on face
    - "black_bars": Simple letterbox/pillarbox with black bars

    Args:
        clip_index: Index of clip (for unique labels)
        src_w: Source video width
        src_h: Source video height
        target_w: Target output width
        target_h: Target output height
        face_center: Tuple of (x, y) normalized 0-1 or None (used for smart_zoom)
        pix_fmt: Pixel format for output
        target_fps: Target frame rate
        rotation_filter: Optional rotation filter prefix (e.g., "transpose=1,")
        hdr_conversion: Optional HDR conversion filter
        colorspace_filter: Optional colorspace filter
        output_suffix: Suffix for output label (default "scaled")
        scale_mode: "blur", "smart_zoom", or "black_bars"

    Returns:
        FFmpeg filter string.
    """
    output_label = f"[v{clip_index}{output_suffix}]"
    common_suffix = f"fps={target_fps},settb=1/{target_fps},format={pix_fmt}{hdr_conversion}{colorspace_filter},setsar=1"

    # Check if aspect ratios are significantly different (>5% difference)
    src_ar = src_w / src_h
    target_ar = target_w / target_h
    ar_diff = abs(src_ar - target_ar) / max(src_ar, target_ar)

    if ar_diff < 0.05:
        # Aspect ratios are similar - simple scale
        return (
            f"[{clip_index}:v]{rotation_filter}setpts=PTS-STARTPTS,"
            f"scale={target_w}:{target_h}:flags=lanczos,"
            f"{common_suffix}{output_label}"
        )

    # Handle different scale modes
    if scale_mode == "black_bars":
        # Simple letterbox/pillarbox with black bars (no blur)
        return (
            f"[{clip_index}:v]{rotation_filter}setpts=PTS-STARTPTS,"
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,"
            f"{common_suffix}{output_label}"
        )

    if scale_mode == "smart_zoom" and face_center is not None:
        # Smart crop centered on face
        crop_filter = _get_smart_crop_filter(
            src_w, src_h, target_w, target_h, face_center[0], face_center[1]
        )
        return (
            f"[{clip_index}:v]{rotation_filter}setpts=PTS-STARTPTS,"
            f"{crop_filter},"
            f"{common_suffix}{output_label}"
        )

    # Default: Blur background (Instagram-style)
    # Also used when smart_zoom is selected but no face is found
    bg_label = f"bg{clip_index}"
    fg_label = f"fg{clip_index}"
    blurred_label = f"blurred{clip_index}"
    scaled_label = f"scaled{clip_index}"

    return (
        f"[{clip_index}:v]{rotation_filter}setpts=PTS-STARTPTS,split[{bg_label}][{fg_label}];"
        f"[{bg_label}]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{target_h},boxblur=luma_radius=150:chroma_radius=150:luma_power=3:chroma_power=3[{blurred_label}];"
        f"[{fg_label}]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease:flags=lanczos[{scaled_label}];"
        f"[{blurred_label}][{scaled_label}]overlay=(W-w)/2:(H-h)/2,"
        f"{common_suffix}{output_label}"
    )


def _get_smart_crop_filter(
    src_w: int,
    src_h: int,
    target_w: int,
    target_h: int,
    face_center_x: float,
    face_center_y: float,
) -> str:
    """Generate FFmpeg crop filter centered on face position.

    Calculates the optimal crop region that:
    1. Maintains the target aspect ratio
    2. Centers on the detected face position
    3. Stays within source video bounds

    Args:
        src_w: Source video width
        src_h: Source video height
        target_w: Target output width
        target_h: Target output height
        face_center_x: Face center X position (normalized 0-1, left to right)
        face_center_y: Face center Y position (normalized 0-1, top to bottom)

    Returns:
        FFmpeg filter string for crop and scale (e.g., "crop=1080:1920:100:50,scale=1080:1920")
    """
    target_ar = target_w / target_h
    src_ar = src_w / src_h

    # Calculate crop dimensions to match target aspect ratio
    if src_ar > target_ar:
        # Source is wider - crop width
        crop_h = src_h
        crop_w = int(src_h * target_ar)
    else:
        # Source is taller - crop height
        crop_w = src_w
        crop_h = int(src_w / target_ar)

    # Convert normalized face position to pixels
    face_x = int(face_center_x * src_w)
    face_y = int(face_center_y * src_h)

    # Center crop on face with bounds checking
    crop_x = max(0, min(face_x - crop_w // 2, src_w - crop_w))
    crop_y = max(0, min(face_y - crop_h // 2, src_h - crop_h))

    return f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={target_w}:{target_h}:flags=lanczos"


def _detect_face_center_in_video(video_path: Path) -> tuple[float, float] | None:
    """Detect average face center position in a video.

    Samples multiple frames throughout the video and detects faces in each.
    Returns the average face position if faces are found, otherwise None.

    Uses Apple Vision Framework on macOS (GPU-accelerated) with OpenCV fallback.

    Args:
        video_path: Path to video file

    Returns:
        Tuple of (center_x, center_y) in normalized 0-1 coordinates, or None if no faces found.
        Coordinates are: X from left (0) to right (1), Y from top (0) to bottom (1).
    """
    import tempfile

    try:
        # Try Apple Vision first (GPU-accelerated on macOS)
        from immich_memories.analysis.apple_vision import VisionFaceDetector

        detector = VisionFaceDetector()
    except ImportError:
        # Fallback to OpenCV
        try:
            import cv2

            detector = None  # Will use OpenCV directly
        except ImportError:
            logger.debug("No face detection available (Apple Vision or OpenCV)")
            return None

    # Get video duration
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
        )
        duration = float(result.stdout.strip())
    except (ValueError, subprocess.SubprocessError):
        duration = 10.0  # Default fallback

    # Sample 3 frames at 20%, 50%, 80% through video
    sample_times = [duration * p for p in [0.2, 0.5, 0.8]]
    all_face_positions: list[tuple[float, float]] = []

    for sample_time in sample_times:
        try:
            # Extract frame to temp file
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                frame_path = Path(tmp.name)

            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    str(sample_time),
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    str(frame_path),
                ],
                capture_output=True,
                timeout=30,
            )

            if not frame_path.exists():
                continue

            if detector is not None:
                # Apple Vision
                import cv2

                _img = cv2.imread(str(frame_path))
                if _img is None:
                    continue
                faces = detector.detect_faces(_img)
                for face in faces:
                    # Apple Vision returns bounding box with origin at bottom-left
                    # Convert to top-left origin normalized coordinates
                    center_x = face.bounds.x + face.bounds.width / 2
                    center_y = 1.0 - (face.bounds.y + face.bounds.height / 2)  # Flip Y
                    all_face_positions.append((center_x, center_y))
            else:
                # OpenCV fallback
                import cv2

                img = cv2.imread(str(frame_path))
                if img is not None:
                    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                    face_cascade = cv2.CascadeClassifier(
                        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                    )
                    faces_cv = face_cascade.detectMultiScale(gray, 1.1, 4)
                    h, w = img.shape[:2]
                    for x, y, fw, fh in faces_cv:
                        center_x = (x + fw / 2) / w
                        center_y = (y + fh / 2) / h
                        all_face_positions.append((center_x, center_y))

            # Cleanup
            frame_path.unlink(missing_ok=True)

        except Exception as e:
            logger.debug(f"Face detection failed for frame at {sample_time}s: {e}")
            continue

    if not all_face_positions:
        return None

    # Average all face positions
    avg_x = sum(p[0] for p in all_face_positions) / len(all_face_positions)
    avg_y = sum(p[1] for p in all_face_positions) / len(all_face_positions)

    logger.debug(
        f"Detected {len(all_face_positions)} faces, average center: ({avg_x:.2f}, {avg_y:.2f})"
    )
    return (avg_x, avg_y)


def aggregate_mood_from_clips(clips: list) -> str | None:
    """Aggregate the dominant mood from a list of clips.

    Analyzes the llm_emotion from each clip and returns the most common mood.
    Maps various emotion labels to the mood categories used by title screens.

    Args:
        clips: List of clips with llm_emotion attributes.

    Returns:
        Dominant mood string (e.g., "happy", "calm") or None if no emotions found.
    """
    # Mapping from LLM emotion labels to title screen mood categories
    emotion_to_mood = {
        # Happy family
        "happy": "happy",
        "joyful": "happy",
        "cheerful": "happy",
        "delighted": "happy",
        "content": "happy",
        # Calm family
        "calm": "calm",
        "serene": "calm",
        "relaxed": "calm",
        "tranquil": "calm",
        # Energetic family
        "energetic": "energetic",
        "excited": "energetic",
        "enthusiastic": "energetic",
        "dynamic": "energetic",
        # Playful family
        "playful": "playful",
        "fun": "playful",
        "silly": "playful",
        "mischievous": "playful",
        # Nostalgic family
        "nostalgic": "nostalgic",
        "sentimental": "nostalgic",
        "wistful": "nostalgic",
        # Romantic family
        "romantic": "romantic",
        "loving": "romantic",
        "tender": "romantic",
        "affectionate": "romantic",
        # Peaceful family
        "peaceful": "peaceful",
        "quiet": "peaceful",
        "gentle": "peaceful",
        # Exciting family
        "exciting": "exciting",
        "thrilling": "exciting",
        "adventurous": "exciting",
    }

    # Collect emotions from clips
    emotions = []
    for clip in clips:
        emotion = getattr(clip, "llm_emotion", None)
        if emotion:
            # Normalize and map to mood category
            emotion_lower = emotion.lower().strip()
            mood = emotion_to_mood.get(emotion_lower, emotion_lower)
            emotions.append(mood)

    if not emotions:
        return None

    # Count and return most common
    mood_counts = Counter(emotions)
    dominant_mood, count = mood_counts.most_common(1)[0]

    logger.info(f"Dominant mood from {len(emotions)} clips: {dominant_mood} ({count} occurrences)")
    return dominant_mood


@dataclass
class FFmpegProgress:
    """Progress information from FFmpeg encoding."""

    frame: int = 0
    fps: float = 0.0
    time_seconds: float = 0.0
    speed: float = 0.0
    percent: float = 0.0
    eta_seconds: float | None = None

    def __str__(self) -> str:
        eta_str = f"{self.eta_seconds:.0f}s" if self.eta_seconds else "calculating..."
        return f"{self.percent:.1f}% @ {self.speed:.1f}x speed, ETA: {eta_str}"


def _parse_ffmpeg_time(time_str: str) -> float:
    """Parse FFmpeg time string (HH:MM:SS.ms) to seconds."""
    try:
        # Handle negative times
        if time_str.startswith("-"):
            return 0.0
        parts = time_str.split(":")
        if len(parts) == 3:
            hours, mins, secs = parts
            return float(hours) * 3600 + float(mins) * 60 + float(secs)
        elif len(parts) == 2:
            mins, secs = parts
            return float(mins) * 60 + float(secs)
        else:
            return float(time_str)
    except (ValueError, IndexError):
        return 0.0


def _parse_ffmpeg_progress(line: str, total_duration: float) -> FFmpegProgress | None:
    """Parse an FFmpeg progress line and return progress info.

    FFmpeg outputs lines like:
    frame=  123 fps= 45 q=28.0 size=    1234kB time=00:00:05.12 bitrate= 123.4kbits/s speed=1.23x
    """
    progress = FFmpegProgress()

    # Extract frame
    frame_match = re.search(r"frame=\s*(\d+)", line)
    if frame_match:
        progress.frame = int(frame_match.group(1))

    # Extract fps
    fps_match = re.search(r"fps=\s*([\d.]+)", line)
    if fps_match:
        progress.fps = float(fps_match.group(1))

    # Extract time (most important for progress)
    time_match = re.search(r"time=\s*([\d:.N/A-]+)", line)
    if time_match:
        time_str = time_match.group(1)
        if time_str != "N/A":
            progress.time_seconds = _parse_ffmpeg_time(time_str)

    # Extract speed
    speed_match = re.search(r"speed=\s*([\d.]+)x", line)
    if speed_match:
        progress.speed = float(speed_match.group(1))

    # Calculate percentage and ETA
    if total_duration > 0 and progress.time_seconds >= 0:
        progress.percent = min(100.0, (progress.time_seconds / total_duration) * 100)

        if progress.speed > 0:
            remaining_time = total_duration - progress.time_seconds
            progress.eta_seconds = remaining_time / progress.speed

    # Only return if we got meaningful data
    if progress.time_seconds > 0 or progress.frame > 0:
        return progress
    return None


def _run_ffmpeg_with_progress(
    cmd: list[str],
    total_duration: float,
    progress_callback: Callable[[float, str], None] | None = None,
) -> subprocess.CompletedProcess:
    """Run FFmpeg command and parse progress output.

    Args:
        cmd: FFmpeg command as list of arguments.
        total_duration: Expected output duration in seconds.
        progress_callback: Callback receiving (percent, status_message).

    Returns:
        CompletedProcess with return code and stderr.
    """
    # Add progress stats to stderr
    if "-stats" not in cmd:
        # Insert after "ffmpeg"
        cmd = cmd[:1] + ["-stats"] + cmd[1:]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        bufsize=1,
    )

    stderr_lines: list[str] = []
    last_progress_time = time.time()

    def read_stderr(pipe: IO[str]) -> None:
        """Read stderr and parse progress."""
        nonlocal last_progress_time

        buffer = ""

        while True:
            char = pipe.read(1)
            if not char:
                break

            buffer += char

            # FFmpeg progress lines end with \r or \n
            if char in ("\r", "\n"):
                line = buffer.strip()
                buffer = ""

                if line:
                    stderr_lines.append(line)

                    # Parse progress (throttle to avoid UI spam)
                    now = time.time()
                    if progress_callback and now - last_progress_time >= 0.5:
                        progress = _parse_ffmpeg_progress(line, total_duration)
                        if progress:
                            last_progress_time = now
                            # Build a clean status message (ETA will be added by UI)
                            time_str = f"{int(progress.time_seconds // 60)}:{int(progress.time_seconds % 60):02d}"
                            status = f"Encoding ({time_str})"
                            if progress.speed > 0:
                                status += f" @ {progress.speed:.1f}x"
                            try:
                                progress_callback(progress.percent, status)
                            except Exception:
                                # Ignore Streamlit threading errors
                                pass

    # Read stderr in a thread
    stderr_thread = Thread(target=read_stderr, args=(process.stderr,))

    # Add Streamlit script context to thread BEFORE starting (required for UI updates)
    try:
        from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

        ctx = get_script_run_ctx()
        if ctx is not None:
            add_script_run_ctx(stderr_thread, ctx)
    except ImportError:
        pass

    stderr_thread.start()

    # Wait for process to complete
    process.wait()
    stderr_thread.join()

    return subprocess.CompletedProcess(
        args=cmd,
        returncode=process.returncode,
        stdout="",
        stderr="\n".join(stderr_lines),
    )


class TransitionType(StrEnum):
    """Video transition types."""

    CUT = "cut"
    CROSSFADE = "crossfade"
    SMART = "smart"  # Mix of cut and crossfade for variety
    NONE = "none"


@dataclass
class TitleScreenSettings:
    """Settings for title screens during assembly."""

    enabled: bool = True

    # Title screen content
    year: int | None = None
    month: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    person_name: str | None = None
    birthday_age: int | None = None
    birthday_month: int | None = None  # Month of person's birthday (1-12) for celebration

    # Visual settings
    locale: str = "en"
    style_mode: str = "auto"  # "auto", "random", or specific style name
    mood: str | None = None  # Video mood for style selection

    # Timing
    title_duration: float = 3.5
    month_divider_duration: float = 2.0
    ending_duration: float = 7.0

    # Features
    show_month_dividers: bool = True
    month_divider_threshold: int = 2  # Minimum clips in a month to show divider
    show_ending_screen: bool = True
    use_first_name_only: bool = True  # Use only first name for titles


@dataclass
class AssemblySettings:
    """Settings for video assembly."""

    transition: TransitionType = TransitionType.CROSSFADE
    transition_duration: float = 0.5
    music_path: Path | None = None
    music_volume: float = 0.3
    # Stem-based ducking paths (from MusicGen/Demucs separation)
    # 2-stem mode (simpler):
    music_vocals_path: Path | None = None  # Vocals/melody stem (gets ducked during speech)
    music_accompaniment_path: Path | None = None  # Drums+bass stem (stays at full volume)
    # 4-stem mode (granular control):
    music_drums_path: Path | None = None  # Drums stem (duck least ~50%)
    music_bass_path: Path | None = None  # Bass stem (duck moderately)
    music_other_path: Path | None = None  # Other instruments stem
    add_date_overlay: bool = False
    date_format: str = "%B %d, %Y"
    output_format: str = "mp4"
    output_codec: str = "h264"
    output_crf: int = 18
    # HDR and quality preservation
    preserve_hdr: bool = True  # Use HEVC with HDR metadata
    preserve_framerate: bool = True  # Keep original frame rate (e.g., 60fps)
    target_framerate: int | None = None  # Force specific frame rate (None = auto)
    # Resolution settings
    auto_resolution: bool = True  # Auto-detect resolution from clips
    target_resolution: tuple[int, int] | None = None  # Override resolution (width, height)
    # Title screens
    title_screens: TitleScreenSettings | None = None
    # Pre-decided transitions (from clips.plan_transitions)
    # If provided, these override the automatic transition decisions
    # Format: list of "fade" or "cut" for each transition between clips
    predecided_transitions: list[str] | None = None
    # Debug mode: preserve intermediate batch files for troubleshooting
    debug_preserve_intermediates: bool = False
    # Aspect ratio handling mode: "blur", "smart_zoom", "black_bars", "exclude"
    scale_mode: str = "blur"


@dataclass
class AssemblyClip:
    """A clip ready for assembly."""

    path: Path
    duration: float
    date: str | None = None
    asset_id: str = ""
    original_segment: ClipSegment | None = None
    # Rotation override: None = auto-detect, 0/90/180/270 = force rotation
    rotation_override: int | None = None
    # LLM analysis results for mood detection
    llm_emotion: str | None = None
    # Title screen flag - ensures fade transitions are used for this clip
    is_title_screen: bool = False
    # Audio analysis results for targeted ducking
    has_speech: bool = False  # Segment contains speech (from audio analysis)
    # Pre-decided outgoing transition (from clips.plan_transitions)
    # "fade" = crossfade to next clip, "cut" = hard cut to next clip
    # None = let assembler decide (title screens always use fade)
    outgoing_transition: str | None = None


def _get_rotation_filter(rotation: int) -> str:
    """Get FFmpeg filter string for rotation.

    Args:
        rotation: Rotation in degrees (0, 90, 180, 270).

    Returns:
        FFmpeg filter string (e.g., "transpose=1" for 90° clockwise).
    """
    rotation_filters = {
        90: "transpose=1",  # 90° clockwise
        180: "hflip,vflip",  # 180° rotation
        270: "transpose=2",  # 90° counter-clockwise (270° clockwise)
    }
    return rotation_filters.get(rotation, "")


class VideoAssembler:
    """Assemble multiple clips into a final video.

    This class handles the complete video assembly pipeline including:
    - Resolution detection and normalization
    - Frame rate detection and normalization
    - Transition application (smart, crossfade, or cuts)
    - HDR metadata preservation (HEVC with BT.2020/HLG)
    - Audio mixing and normalization

    The assembly process has a robust fallback chain:
        SMART transitions -> CROSSFADE -> CUTS (with re-encoding)

    All fallbacks properly handle codec/resolution/framerate mismatches
    by re-encoding through a filter complex rather than using stream copy.

    Attributes:
        settings: AssemblySettings controlling output format and transitions.
    """

    def __init__(self, settings: AssemblySettings | None = None, run_id: str | None = None):
        """Initialize the assembler.

        Args:
            settings: Assembly settings. If None, uses defaults from config.
            run_id: Optional run ID for job tracking and cancellation support.
        """
        self.settings = settings or AssemblySettings()
        self.run_id = run_id
        self._run_db: RunDatabase | None = None

        # Face detection cache: path -> (center_x, center_y) or None
        # Using OrderedDict with size limit to prevent unbounded memory growth
        self._face_cache: OrderedDict[Path, tuple[float, float] | None] = OrderedDict()

        config = get_config()
        if self.settings.output_crf == 18:
            self.settings.output_crf = config.output.crf
        if self.settings.transition_duration == 0.5:
            self.settings.transition_duration = config.defaults.transition_duration

    def _check_cancelled(self) -> None:
        """Check if job cancellation was requested and raise if so."""
        if not self.run_id:
            return
        if self._run_db is None:
            self._run_db = RunDatabase()
        if self._run_db.is_cancel_requested(self.run_id):
            logger.info(f"Assembly job {self.run_id} cancelled by user request")
            raise JobCancelledException(f"Job {self.run_id} cancelled")

    def _get_face_center(self, video_path: Path) -> tuple[float, float] | None:
        """Get face center for a video with caching.

        Args:
            video_path: Path to video file

        Returns:
            Tuple of (center_x, center_y) in normalized 0-1 coordinates, or None
        """
        if video_path in self._face_cache:
            # Move to end (most recently used)
            self._face_cache.move_to_end(video_path)
            return self._face_cache[video_path]

        # Evict oldest entries if cache is full
        while len(self._face_cache) >= MAX_FACE_CACHE_SIZE:
            self._face_cache.popitem(last=False)

        result = _detect_face_center_in_video(video_path)
        self._face_cache[video_path] = result
        return result

    def assemble(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble clips into a final video.

        Args:
            clips: List of clips to assemble.
            output_path: Path for output video.
            progress_callback: Progress callback (0.0 to 1.0).

        Returns:
            Path to assembled video.
        """
        if not clips:
            raise ValueError("No clips provided")

        if len(clips) == 1:
            # Single clip - just copy or add music
            return self._process_single_clip(clips[0], output_path)

        # Use new scalable assembly method for all transition types
        # This method is memory-efficient and scales to any number of clips
        result = self._assemble_scalable(clips, output_path, progress_callback)

        # Add music if specified
        if self.settings.music_path and self.settings.music_path.exists():
            result = self._add_music(result, output_path)

        return result

    def _process_single_clip(self, clip: AssemblyClip, output_path: Path) -> Path:
        """Process a single clip (add music if needed).

        Args:
            clip: The clip to process.
            output_path: Output path.

        Returns:
            Path to output video.
        """
        if self.settings.music_path and self.settings.music_path.exists():
            return self._add_music_to_clip(clip.path, output_path)
        else:
            # Just copy
            import shutil

            shutil.copy2(clip.path, output_path)
            return output_path

    def _assemble_with_cuts(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble clips with hard cuts (re-encodes to handle codec mismatches).

        Args:
            clips: List of clips to assemble.
            output_path: Output path.
            progress_callback: Progress callback.

        Returns:
            Path to output video.
        """
        if len(clips) == 0:
            raise ValueError("No clips to assemble")

        if len(clips) == 1:
            return self._process_single_clip(clips[0], output_path)

        # Determine resolution
        if self.settings.target_resolution:
            out_width, out_height = self.settings.target_resolution
        elif self.settings.auto_resolution:
            out_width, out_height = self._detect_best_resolution(clips)
        else:
            out_width, out_height = (1920, 1080)

        # Detect orientation from first real clip (skip title screens which may differ)
        first_res = None
        for clip in clips:
            res = self._get_video_resolution(clip.path)
            if res:
                first_res = res
                break

        if first_res and first_res[1] > first_res[0]:
            # Portrait - swap dimensions
            out_width, out_height = out_height, out_width
            logger.info(f"Detected portrait orientation, swapping to {out_width}x{out_height}")

        # Determine frame rate
        if self.settings.target_framerate:
            out_fps = self.settings.target_framerate
        elif self.settings.preserve_framerate:
            out_fps = self._detect_max_framerate(clips)
        else:
            out_fps = 30

        logger.info(f"Cuts assembly: {len(clips)} clips, {out_width}x{out_height} @ {out_fps}fps")

        # Build input arguments
        input_args = []
        for clip in clips:
            input_args.extend(["-i", str(clip.path)])

        # Build filter complex for scaling and concatenating
        filter_parts = []

        # Detect HDR type from source clips (HLG for iPhone, PQ for Android/Samsung)
        hdr_type = _get_dominant_hdr_type(clips) if self.settings.preserve_hdr else "hlg"

        # Get per-clip HDR types for mixed content handling
        clip_hdr_types = (
            _get_clip_hdr_types(clips) if self.settings.preserve_hdr else [None] * len(clips)
        )

        # Check for mixed HDR content
        unique_types = {t for t in clip_hdr_types if t is not None}
        if len(unique_types) > 1:
            logger.warning(
                f"Mixed HDR content detected: {unique_types} - converting all to {hdr_type.upper()}"
            )

        # HDR colorspace parameters - auto-detected based on source
        # Use p010le for macOS VideoToolbox, yuv420p10le for software/other encoders
        import sys

        if self.settings.preserve_hdr:
            pix_fmt = "p010le" if sys.platform == "darwin" else "yuv420p10le"
        else:
            pix_fmt = "yuv420p"
        colorspace_filter = _get_colorspace_filter(hdr_type) if self.settings.preserve_hdr else ""

        # Scale each input
        for i in range(len(clips)):
            # HDR conversion filter (only if this clip's HDR type differs from target)
            hdr_conversion = ""
            if self.settings.preserve_hdr and clip_hdr_types[i] != hdr_type:
                hdr_conversion = _get_hdr_conversion_filter(clip_hdr_types[i], hdr_type)
                if hdr_conversion:
                    logger.info(f"Converting clip {i} from {clip_hdr_types[i]} to {hdr_type}")

            scale_filter = (
                f"[{i}:v]setpts=PTS-STARTPTS,"  # Reset timestamps (VFR fix)
                f"scale={out_width}:{out_height}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={out_width}:{out_height}:(ow-iw)/2:(oh-ih)/2:black,"
                f"fps={out_fps},settb=1/{out_fps},format={pix_fmt}{hdr_conversion}{colorspace_filter},setsar=1[v{i}]"
            )
            filter_parts.append(scale_filter)

        # Build concat filter
        video_inputs = "".join(f"[v{i}]" for i in range(len(clips)))
        audio_inputs = "".join(f"[{i}:a]" for i in range(len(clips)))
        filter_parts.append(f"{video_inputs}concat=n={len(clips)}:v=1:a=0[vout]")
        filter_parts.append(f"{audio_inputs}concat=n={len(clips)}:v=0:a=1[aout]")

        filter_complex = ";".join(filter_parts)

        # Choose codec (GPU accelerated when available)
        video_codec_args = _get_gpu_encoder_args(
            crf=self.settings.output_crf,
            preserve_hdr=self.settings.preserve_hdr,
            hdr_type=hdr_type,
        )
        if self.settings.preserve_hdr:
            logger.info(f"Using GPU-accelerated HEVC with {hdr_type.upper()} HDR preservation")
        else:
            logger.info("Using GPU-accelerated encoding")

        cmd = [
            "ffmpeg",
            "-y",
            *input_args,
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            *video_codec_args,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-threads",
            "4",  # Limit thread parallelism for memory
            "-filter_complex_threads",
            "1",  # Single-threaded filter processing
            "-max_muxing_queue_size",
            "1024",  # Limit memory for 4K filter graphs
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        logger.debug(f"Running cuts assembly: {' '.join(cmd)}")

        total_duration = self.estimate_duration(clips)
        result = _run_ffmpeg_with_progress(cmd, total_duration, progress_callback)

        if result.returncode != 0:
            logger.error(f"FFmpeg cuts assembly error: {result.stderr}")
            raise RuntimeError(f"Failed to assemble video with cuts: {result.stderr}")

        return output_path

    def _get_video_resolution(self, video_path: Path) -> tuple[int, int] | None:
        """Get video resolution (width, height) accounting for rotation.

        iPhone videos are often stored as landscape but have rotation metadata
        that makes them portrait when displayed. This function detects rotation
        and swaps dimensions accordingly.

        Args:
            video_path: Path to video file.

        Returns:
            Tuple of (width, height) after applying rotation, or None if detection fails.
        """
        try:
            # Get width, height, and rotation in one call
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height:stream_side_data=rotation",
                "-of",
                "json",
                str(video_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout)
                streams = data.get("streams", [])
                if streams:
                    stream = streams[0]
                    width = stream.get("width", 0)
                    height = stream.get("height", 0)

                    # Check for rotation in side_data_list
                    rotation = 0
                    for side_data in stream.get("side_data_list", []):
                        if "rotation" in side_data:
                            rotation = abs(int(side_data["rotation"]))
                            break

                    # Swap dimensions for 90 or 270 degree rotation (portrait videos)
                    if rotation in (90, 270):
                        width, height = height, width

                    if width and height:
                        return width, height
        except Exception as e:
            logger.debug(f"Failed to detect resolution: {e}")
        return None

    def _detect_best_resolution(self, clips: list[AssemblyClip]) -> tuple[int, int]:
        """Detect the best output resolution based on majority of clips.

        Logic:
        - Count clips at each resolution tier (4K, 1080p, 720p)
        - Detect orientation (portrait vs landscape) from majority
        - Use the resolution that >50% of clips have
        - If no majority, use the highest resolution present

        Args:
            clips: List of clips to analyze.

        Returns:
            Tuple of (width, height) for output resolution.
        """
        resolution_counts = {"4k": 0, "1080p": 0, "720p": 0, "other": 0}
        orientation_counts = {"portrait": 0, "landscape": 0}
        resolutions_found = []

        for clip in clips:
            res = self._get_video_resolution(clip.path)
            if res:
                w, h = res
                # Use the larger dimension to handle portrait/landscape
                max_dim = max(w, h)
                resolutions_found.append(max_dim)

                # Track orientation
                if h > w:
                    orientation_counts["portrait"] += 1
                else:
                    orientation_counts["landscape"] += 1

                if max_dim >= 2160:
                    resolution_counts["4k"] += 1
                elif max_dim >= 1080:
                    resolution_counts["1080p"] += 1
                elif max_dim >= 720:
                    resolution_counts["720p"] += 1
                else:
                    resolution_counts["other"] += 1

        total = len(clips)
        if total == 0:
            logger.info("No clips to analyze, defaulting to 1080p landscape")
            return (1920, 1080)

        # Determine orientation from majority
        is_portrait = orientation_counts["portrait"] > orientation_counts["landscape"]
        orientation_str = "portrait" if is_portrait else "landscape"
        logger.info(
            f"Orientation: {orientation_str} "
            f"({orientation_counts['portrait']} portrait, {orientation_counts['landscape']} landscape)"
        )

        # Resolution tuples based on orientation
        if is_portrait:
            res_4k = (2160, 3840)
            res_1080p = (1080, 1920)
            res_720p = (720, 1280)
        else:
            res_4k = (3840, 2160)
            res_1080p = (1920, 1080)
            res_720p = (1280, 720)

        # Check for majority (>50%)
        if resolution_counts["4k"] > total / 2:
            logger.info(
                f"Auto resolution: 4K {orientation_str} ({resolution_counts['4k']}/{total} clips are 4K)"
            )
            return res_4k
        elif resolution_counts["1080p"] > total / 2:
            logger.info(
                f"Auto resolution: 1080p {orientation_str} ({resolution_counts['1080p']}/{total} clips are 1080p)"
            )
            return res_1080p
        elif resolution_counts["720p"] > total / 2:
            logger.info(
                f"Auto resolution: 720p {orientation_str} ({resolution_counts['720p']}/{total} clips are 720p)"
            )
            return res_720p
        else:
            # No majority - use the highest resolution present
            if resolution_counts["4k"] > 0:
                logger.info(
                    f"Auto resolution: 4K {orientation_str} (highest available, {resolution_counts['4k']}/{total} clips)"
                )
                return res_4k
            elif resolution_counts["1080p"] > 0:
                logger.info(
                    f"Auto resolution: 1080p {orientation_str} (highest available, {resolution_counts['1080p']}/{total} clips)"
                )
                return res_1080p
            else:
                logger.info(f"Auto resolution: 720p {orientation_str} (default)")
                return res_720p

    def _assemble_with_crossfade(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble clips with crossfade transitions.

        Args:
            clips: List of clips.
            output_path: Output path.
            progress_callback: Progress callback.

        Returns:
            Path to output video.
        """
        if len(clips) < 2:
            return self._process_single_clip(clips[0], output_path)

        # Memory optimization: use chunked assembly for many clips at 4K
        # This prevents OOM when processing 44+ clips with xfade transitions
        if len(clips) > CHUNKED_ASSEMBLY_THRESHOLD:
            logger.info(
                f"Using chunked assembly for {len(clips)} clips (threshold: {CHUNKED_ASSEMBLY_THRESHOLD})"
            )
            return self._assemble_chunked(clips, output_path, progress_callback)

        fade_duration = self.settings.transition_duration

        # Determine target resolution
        if self.settings.target_resolution:
            # User specified exact resolution
            target_w, target_h = self.settings.target_resolution
            logger.info(f"Using specified resolution {target_w}x{target_h}")
        elif self.settings.auto_resolution:
            # Auto-detect from clips
            target_w, target_h = self._detect_best_resolution(clips)
        else:
            # Use config default
            config = get_config()
            target_w, target_h = config.output.resolution_tuple
            logger.info(f"Using config resolution {target_w}x{target_h}")

        # Detect if clips are portrait (rotated) - if most are taller than wide, swap
        portrait_count = 0
        for clip in clips:
            res = self._get_video_resolution(clip.path)
            if res and res[1] > res[0]:  # height > width = portrait
                portrait_count += 1

        if portrait_count > len(clips) // 2:
            # Majority portrait - swap dimensions for vertical video
            target_w, target_h = target_h, target_w
            logger.info(f"Detected portrait orientation, swapping to {target_w}x{target_h}")

        # Build complex filter for crossfades
        # This uses the xfade filter available in FFmpeg 4.3+
        inputs = []
        filter_parts = []

        for clip in clips:
            inputs.extend(["-i", str(clip.path)])

        # First, scale all inputs to the target resolution
        # Use scale + pad to handle different aspect ratios (letterbox/pillarbox)
        # For HDR, we need to preserve 10-bit pixel format AND colorspace metadata
        # Use lanczos for high-quality upscaling (better than bicubic for sharpness)
        # IMPORTANT: Normalize fps to 60 for xfade compatibility (30fps clips get frame-doubled)
        # Use p010le for macOS VideoToolbox, yuv420p10le for software/other encoders
        import sys

        if self.settings.preserve_hdr:
            pix_fmt = "p010le" if sys.platform == "darwin" else "yuv420p10le"
        else:
            pix_fmt = "yuv420p"
        target_fps = 60  # Normalize all clips to 60fps for xfade compatibility

        # Detect HDR type from source clips (HLG for iPhone, PQ for Android/Samsung)
        hdr_type = _get_dominant_hdr_type(clips) if self.settings.preserve_hdr else "hlg"

        # Get per-clip HDR types for mixed content handling
        clip_hdr_types = (
            _get_clip_hdr_types(clips) if self.settings.preserve_hdr else [None] * len(clips)
        )

        # Check for mixed HDR content
        unique_types = {t for t in clip_hdr_types if t is not None}
        if len(unique_types) > 1:
            logger.warning(
                f"Mixed HDR content detected: {unique_types} - converting all to {hdr_type.upper()}"
            )

        # HDR colorspace parameters - auto-detected based on source
        colorspace_filter = _get_colorspace_filter(hdr_type) if self.settings.preserve_hdr else ""

        for i, clip in enumerate(clips):
            # Build filter chain for this clip
            # Optional rotation override (applied before scaling)
            rotation_filter = ""
            if clip.rotation_override is not None and clip.rotation_override != 0:
                rotation_filter = _get_rotation_filter(clip.rotation_override) + ","
                logger.info(f"Applying {clip.rotation_override}° rotation to clip {i}")

            # HDR conversion filter (only if this clip's HDR type differs from target)
            hdr_conversion = ""
            if self.settings.preserve_hdr and clip_hdr_types[i] != hdr_type:
                hdr_conversion = _get_hdr_conversion_filter(clip_hdr_types[i], hdr_type)
                if hdr_conversion:
                    logger.info(f"Converting clip {i} from {clip_hdr_types[i]} to {hdr_type}")

            # Get clip resolution for smart aspect ratio handling
            clip_res = self._get_video_resolution(clip.path)
            if clip_res:
                src_w, src_h = clip_res
                # Check if aspect ratio differs significantly (needs zoom/blur)
                src_ar = src_w / src_h
                target_ar = target_w / target_h
                ar_diff = abs(src_ar - target_ar) / max(src_ar, target_ar)

                if ar_diff > 0.05 and not clip.is_title_screen:
                    # Aspect ratios differ - use smart zoom or blur background
                    # Skip face detection for title screens (no faces)
                    face_center = self._get_face_center(clip.path)
                    if face_center:
                        logger.info(
                            f"Clip {i}: Using smart crop centered on face at ({face_center[0]:.2f}, {face_center[1]:.2f})"
                        )
                    else:
                        logger.info(f"Clip {i}: Using blur background (no faces detected)")

                    # Generate filter with smart zoom or blur
                    aspect_filter = _get_aspect_ratio_filter(
                        clip_index=i,
                        src_w=src_w,
                        src_h=src_h,
                        target_w=target_w,
                        target_h=target_h,
                        face_center=face_center,
                        pix_fmt=pix_fmt,
                        target_fps=target_fps,
                        rotation_filter=rotation_filter,
                        hdr_conversion=hdr_conversion,
                        colorspace_filter=colorspace_filter,
                        output_suffix="scaled",
                    )
                    filter_parts.append(aspect_filter)
                    continue  # Skip the standard filter below

            # Standard filter: scale + pad (for same aspect ratio or title screens)
            # CRITICAL: setpts=PTS-STARTPTS resets timestamps (fixes VFR from iPhone)
            # CRITICAL: fps=60 normalizes frame rate for xfade
            # CRITICAL: settb=1/60 sets timebase to match fps - required for xfade to work across mixed sources
            # CRITICAL: setparams preserves HDR colorspace through the filter chain
            # CRITICAL: zscale converts between HLG and PQ for mixed content
            filter_parts.append(
                f"[{i}:v]{rotation_filter}setpts=PTS-STARTPTS,"  # Reset timestamps (VFR fix)
                f"scale={target_w}:{target_h}:"
                f"force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,"
                f"fps={target_fps},settb=1/{target_fps},"  # Normalize fps AND timebase for xfade
                f"format={pix_fmt}{hdr_conversion}{colorspace_filter},setsar=1[v{i}scaled]"
            )

        # Build xfade chain with properly synced audio
        # The key insight: acrossfade expects audio streams to be trimmed and aligned
        current_input = "[v0scaled]"
        total_duration = 0.0

        # First, prepare all audio streams with proper trimming and format normalization
        # CRITICAL: All audio must have same sample rate/channels for acrossfade to work
        # Use amix with anullsrc as fallback to ensure we ALWAYS have audio of exact duration
        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        audio_labels = []
        for i, clip in enumerate(clips):
            if clip.is_title_screen:
                # Title screens: generate silence with matching format
                filter_parts.append(
                    f"anullsrc=r=48000:cl=stereo,atrim=0:{clip.duration},{audio_format}[a{i}prep]"
                )
            else:
                # Regular clips: mix audio with silence fallback to ensure exact duration
                filter_parts.append(
                    f"anullsrc=r=48000:cl=stereo,atrim=0:{clip.duration}[a{i}silence];"
                    f"[{i}:a]{audio_format},asetpts=PTS-STARTPTS[a{i}src];"
                    f"[a{i}silence][a{i}src]amix=inputs=2:duration=first:weights='0 1'[a{i}mixed];"
                    f"[a{i}mixed]atrim=0:{clip.duration},asetpts=PTS-STARTPTS[a{i}prep]"
                )
            audio_labels.append(f"[a{i}prep]")

        # Now build video xfade and audio crossfade chains
        current_audio = audio_labels[0]

        for i, clip in enumerate(clips[:-1]):
            next_idx = i + 1
            next_clip = clips[next_idx]
            output_label = f"[v{i}{next_idx}]"
            audio_label = f"[a{i}{next_idx}]"

            # Calculate offset (when to start transition)
            offset = total_duration + clip.duration - fade_duration

            # Video crossfade using scaled inputs
            # CRITICAL: Add settb after xfade to normalize timebase for next xfade
            filter_parts.append(
                f"{current_input}[v{next_idx}scaled]xfade=transition=fade:"
                f"duration={fade_duration}:offset={offset},settb=1/{target_fps}{output_label}"
            )

            # Audio crossfade - handle title screen transitions specially
            if next_clip.is_title_screen:
                # Fade out audio quickly when transitioning to title
                fast_fade = fade_duration / 2
                filter_parts.append(
                    f"{current_audio}afade=t=out:st={clip.duration - fast_fade}:d={fast_fade}[a{i}faded];"
                    f"[a{i}faded]{audio_labels[next_idx]}acrossfade=d={fade_duration}:c1=tri:c2=tri{audio_label}"
                )
            elif clip.is_title_screen:
                # Fade in audio when coming from title
                fast_fade = fade_duration / 2
                filter_parts.append(
                    f"{current_audio}{audio_labels[next_idx]}acrossfade=d={fade_duration}:c1=tri:c2=tri[a{i}xf];"
                    f"[a{i}xf]afade=t=in:st=0:d={fast_fade}{audio_label}"
                )
            else:
                # Normal audio crossfade
                filter_parts.append(
                    f"{current_audio}{audio_labels[next_idx]}acrossfade=d={fade_duration}:c1=tri:c2=tri{audio_label}"
                )

            current_input = output_label
            current_audio = audio_label
            total_duration = offset

        # Build final command
        filter_complex = ";".join(filter_parts)

        # Choose codec (GPU accelerated when available)
        video_codec_args = _get_gpu_encoder_args(
            crf=self.settings.output_crf,
            preserve_hdr=self.settings.preserve_hdr,
            hdr_type=hdr_type,
        )
        if self.settings.preserve_hdr:
            logger.info(f"Using GPU-accelerated HEVC with {hdr_type.upper()} HDR preservation")
        else:
            logger.info("Using GPU-accelerated encoding")

        # Frame rate handling - we normalized all inputs to target_fps in the filter chain
        # so always output at that rate for consistency
        framerate_args = ["-r", str(target_fps)]
        logger.info(f"Output frame rate: {target_fps}fps (normalized from mixed sources)")

        cmd = [
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            current_input,
            "-map",
            current_audio,
            *video_codec_args,
            *framerate_args,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-threads",
            "4",  # Limit thread parallelism for memory
            "-filter_complex_threads",
            "1",  # Single-threaded filter processing
            "-max_muxing_queue_size",
            "1024",  # Limit memory for 4K filter graphs
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        # Calculate total expected duration for progress tracking
        total_duration = self.estimate_duration(clips)

        logger.debug(f"Running crossfade assembly: {' '.join(cmd)}")
        result = _run_ffmpeg_with_progress(cmd, total_duration, progress_callback)

        if result.returncode != 0:
            # Log just the last 1000 chars of stderr which contains the actual error
            stderr_tail = result.stderr[-1000:] if len(result.stderr) > 1000 else result.stderr
            logger.warning(
                f"Crossfade failed (code {result.returncode}), falling back to cuts. Error: {stderr_tail}"
            )
            # Fall back to cuts (which re-encodes properly)
            return self._assemble_with_cuts(clips, output_path, progress_callback)

        return output_path

    def _decide_transitions(self, clips: list[AssemblyClip]) -> list[str]:
        """Decide which transition type to use between each pair of clips.

        Uses pre-decided transitions from clip.outgoing_transition when available,
        otherwise falls back to smart algorithm:
        - ALWAYS use fade for transitions involving title screens (intro/outro/dividers)
        - Use pre-decided transition if clip has outgoing_transition set
        - For remaining: 70% crossfade, 30% cut with consecutive limits

        Args:
            clips: List of clips to generate transitions for.

        Returns:
            List of transition types ("fade" or "cut") for each transition.
        """
        import random

        num_clips = len(clips)
        if num_clips < 2:
            return []

        num_transitions = num_clips - 1
        transitions = []
        consecutive_fades = 0
        consecutive_cuts = 0
        predecided_used = 0

        for i in range(num_transitions):
            # Get the two clips involved in this transition
            clip_before = clips[i]
            clip_after = clips[i + 1]

            # ALWAYS use fade for title screen transitions (never cut)
            if clip_before.is_title_screen or clip_after.is_title_screen:
                transitions.append("fade")
                consecutive_fades += 1
                consecutive_cuts = 0
                continue

            # Use pre-decided transition if available (from clips.plan_transitions)
            # This respects buffer availability decisions made during extraction
            if clip_before.outgoing_transition is not None:
                transition = clip_before.outgoing_transition
                transitions.append(transition)
                predecided_used += 1
                if transition == "fade":
                    consecutive_fades += 1
                    consecutive_cuts = 0
                else:
                    consecutive_cuts += 1
                    consecutive_fades = 0
                continue

            # Fall back to smart algorithm: 70% crossfade, 30% cut
            use_fade = random.random() < 0.7

            # Force cut if too many consecutive fades
            if consecutive_fades >= 3:
                use_fade = False

            # Force fade if too many consecutive cuts
            if consecutive_cuts >= 2:
                use_fade = True

            if use_fade:
                transitions.append("fade")
                consecutive_fades += 1
                consecutive_cuts = 0
            else:
                transitions.append("cut")
                consecutive_cuts += 1
                consecutive_fades = 0

        logger.info(
            f"Smart transitions: {transitions.count('fade')} crossfades, "
            f"{transitions.count('cut')} cuts"
            + (f" ({predecided_used} pre-decided)" if predecided_used > 0 else "")
        )
        return transitions

    def _assemble_with_smart_transitions(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble clips with a mix of crossfades and cuts for variety.

        Args:
            clips: List of clips.
            output_path: Output path.
            progress_callback: Progress callback.

        Returns:
            Path to output video.
        """
        if len(clips) < 2:
            return self._process_single_clip(clips[0], output_path)

        # Memory optimization: use chunked assembly for many clips at 4K
        # This prevents OOM when processing 44+ clips with xfade transitions
        if len(clips) > CHUNKED_ASSEMBLY_THRESHOLD:
            logger.info(
                f"Using chunked assembly for {len(clips)} clips (threshold: {CHUNKED_ASSEMBLY_THRESHOLD})"
            )
            return self._assemble_chunked(clips, output_path, progress_callback)

        # Decide transitions for each clip pair (takes clips to respect title screen fades)
        transitions = self._decide_transitions(clips)

        fade_duration = self.settings.transition_duration

        # Determine target resolution
        if self.settings.target_resolution:
            # User specified exact resolution
            target_w, target_h = self.settings.target_resolution
            logger.info(f"Using specified resolution {target_w}x{target_h}")
        elif self.settings.auto_resolution:
            # Auto-detect from clips
            target_w, target_h = self._detect_best_resolution(clips)
        else:
            # Use config default
            config = get_config()
            target_w, target_h = config.output.resolution_tuple
            logger.info(f"Using config resolution {target_w}x{target_h}")

        # Detect orientation
        portrait_count = 0
        for clip in clips:
            res = self._get_video_resolution(clip.path)
            if res and res[1] > res[0]:
                portrait_count += 1

        if portrait_count > len(clips) // 2:
            target_w, target_h = target_h, target_w
            logger.info(f"Detected portrait orientation, swapping to {target_w}x{target_h}")

        # Build complex filter
        inputs = []
        filter_parts = []

        for clip in clips:
            inputs.extend(["-i", str(clip.path)])

        # Normalize all inputs (scale, fps, format)
        # Use p010le for macOS VideoToolbox, yuv420p10le for software/other encoders
        import sys

        if self.settings.preserve_hdr:
            pix_fmt = "p010le" if sys.platform == "darwin" else "yuv420p10le"
        else:
            pix_fmt = "yuv420p"
        target_fps = 60

        # Detect HDR type from source clips (HLG for iPhone, PQ for Android/Samsung)
        hdr_type = _get_dominant_hdr_type(clips) if self.settings.preserve_hdr else "hlg"

        # Get per-clip HDR types for mixed content handling
        clip_hdr_types = (
            _get_clip_hdr_types(clips) if self.settings.preserve_hdr else [None] * len(clips)
        )

        # Check for mixed HDR content
        unique_types = {t for t in clip_hdr_types if t is not None}
        if len(unique_types) > 1:
            logger.warning(
                f"Mixed HDR content detected: {unique_types} - converting all to {hdr_type.upper()}"
            )

        # HDR colorspace parameters - auto-detected based on source
        colorspace_filter = _get_colorspace_filter(hdr_type) if self.settings.preserve_hdr else ""

        for i, clip in enumerate(clips):
            # Optional rotation override (applied before scaling)
            rotation_filter = ""
            if clip.rotation_override is not None and clip.rotation_override != 0:
                rotation_filter = _get_rotation_filter(clip.rotation_override) + ","
                logger.info(f"Applying {clip.rotation_override}° rotation to clip {i}")

            # HDR conversion filter (only if this clip's HDR type differs from target)
            hdr_conversion = ""
            if self.settings.preserve_hdr and clip_hdr_types[i] != hdr_type:
                hdr_conversion = _get_hdr_conversion_filter(clip_hdr_types[i], hdr_type)
                if hdr_conversion:
                    logger.info(f"Converting clip {i} from {clip_hdr_types[i]} to {hdr_type}")

            # CRITICAL: setpts=PTS-STARTPTS resets timestamps (fixes VFR from iPhone)
            # CRITICAL: fps=60 normalizes frame rate for xfade
            # CRITICAL: settb=1/60 sets timebase to match fps - required for xfade
            # CRITICAL: setparams preserves HDR colorspace through the filter chain
            # CRITICAL: zscale converts between HLG and PQ for mixed content
            filter_parts.append(
                f"[{i}:v]{rotation_filter}setpts=PTS-STARTPTS,"  # Reset timestamps (VFR fix)
                f"scale={target_w}:{target_h}:"
                f"force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,"
                f"fps={target_fps},settb=1/{target_fps},"
                f"format={pix_fmt}{hdr_conversion}{colorspace_filter},setsar=1[v{i}scaled]"
            )

        # Build transition chain
        # First, prepare all audio streams with format normalization for acrossfade compatibility
        # CRITICAL: All audio must have same sample rate/channels for acrossfade to work
        # Use apad to ensure exact duration even if source is slightly shorter
        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        for i, clip in enumerate(clips):
            if clip.is_title_screen:
                # Title screens: generate silence with matching format
                filter_parts.append(
                    f"anullsrc=r=48000:cl=stereo,atrim=0:{clip.duration},{audio_format}[a{i}prep]"
                )
            else:
                # Regular clips: normalize format, resample for sync, reset timestamps, trim, pad
                # aresample=async=1 helps maintain audio sync at transition boundaries
                filter_parts.append(
                    f"[{i}:a]{audio_format},aresample=async=1,asetpts=PTS-STARTPTS,apad=whole_dur={clip.duration},atrim=0:{clip.duration}[a{i}prep]"
                )

        current_video = "[v0scaled]"
        current_audio = "[a0prep]"
        cumulative_duration = 0.0

        for i, (clip, transition) in enumerate(zip(clips[:-1], transitions, strict=False)):
            next_idx = i + 1
            video_label = f"[v{i}_{next_idx}]"
            audio_label = f"[a{i}_{next_idx}]"

            if transition == "fade":
                # Crossfade transition
                # CRITICAL: Add settb after xfade to normalize timebase for next operation
                offset = cumulative_duration + clip.duration - fade_duration
                filter_parts.append(
                    f"{current_video}[v{next_idx}scaled]xfade=transition=fade:"
                    f"duration={fade_duration}:offset={offset},settb=1/{target_fps}{video_label}"
                )
                # Add asetpts after acrossfade to ensure clean timestamps for next operation
                filter_parts.append(
                    f"{current_audio}[a{next_idx}prep]acrossfade=d={fade_duration},asetpts=PTS-STARTPTS{audio_label}"
                )
                cumulative_duration = offset
            else:
                # Hard cut - just concatenate
                # CRITICAL: Add settb after concat to normalize timebase for next xfade
                filter_parts.append(
                    f"{current_video}[v{next_idx}scaled]concat=n=2:v=1:a=0,settb=1/{target_fps}{video_label}"
                )
                # Add asetpts after concat to reset timestamps and prevent audio desync
                filter_parts.append(
                    f"{current_audio}[a{next_idx}prep]concat=n=2:v=0:a=1,asetpts=PTS-STARTPTS{audio_label}"
                )
                cumulative_duration += clip.duration

            current_video = video_label
            current_audio = audio_label

        filter_complex = ";".join(filter_parts)

        # Choose codec (GPU accelerated when available)
        video_codec_args = _get_gpu_encoder_args(
            crf=self.settings.output_crf,
            preserve_hdr=self.settings.preserve_hdr,
            hdr_type=hdr_type,
        )
        if self.settings.preserve_hdr:
            logger.info(f"Using GPU-accelerated HEVC with {hdr_type.upper()} HDR preservation")
        else:
            logger.info("Using GPU-accelerated encoding")

        framerate_args = ["-r", str(target_fps)]
        logger.info(f"Output frame rate: {target_fps}fps")

        cmd = [
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            current_video,
            "-map",
            current_audio,
            *video_codec_args,
            *framerate_args,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-threads",
            "4",  # Limit thread parallelism for memory
            "-filter_complex_threads",
            "1",  # Single-threaded filter processing
            "-max_muxing_queue_size",
            "1024",  # Limit memory for 4K filter graphs
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        # Calculate total expected duration for progress tracking
        total_duration = self.estimate_duration(clips)

        logger.debug(f"Running smart transition assembly: {' '.join(cmd)}")
        result = _run_ffmpeg_with_progress(cmd, total_duration, progress_callback)

        if result.returncode != 0:
            # Log just the last 1000 chars of stderr which contains the actual error
            stderr_tail = result.stderr[-1000:] if len(result.stderr) > 1000 else result.stderr
            logger.warning(
                f"Smart transitions failed (code {result.returncode}), falling back to crossfade. Error: {stderr_tail}"
            )
            return self._assemble_with_crossfade(clips, output_path, progress_callback)

        return output_path

    def _assemble_chunked(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble many clips using chunked processing to avoid memory exhaustion.

        When processing many clips (44+) at 4K with xfade transitions, FFmpeg's
        filter_complex can exceed available memory. This method divides clips into
        smaller batches, processes each batch separately, then concatenates the
        intermediate files.

        Memory reduction: From 10-20GB (OOM) down to ~2-4GB peak.

        Strategy:
            1. Divide clips into batches of CHUNK_SIZE (8)
            2. Process each batch with xfade transitions → intermediate file
            3. Concatenate intermediate files with xfade transitions
            4. Clean up intermediate files (handled by tempfile)

        Args:
            clips: List of clips to assemble (typically > 12 clips).
            output_path: Final output video path.
            progress_callback: Progress callback for UI updates.

        Returns:
            Path to assembled video.
        """
        import math
        import shutil

        num_clips = len(clips)
        num_batches = math.ceil(num_clips / CHUNK_SIZE)

        logger.info(f"Chunked assembly: {num_clips} clips → {num_batches} batches of ~{CHUNK_SIZE}")

        # Store intermediate files alongside output for debugging
        # They'll be cleaned up after successful assembly
        intermediates_dir = output_path.parent / ".intermediates"
        intermediates_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Intermediate files will be stored in: {intermediates_dir}")

        intermediate_clips: list[AssemblyClip] = []
        try:
            # Process each batch
            for batch_idx in range(num_batches):
                start_idx = batch_idx * CHUNK_SIZE
                end_idx = min(start_idx + CHUNK_SIZE, num_clips)
                batch = clips[start_idx:end_idx]

                if progress_callback:
                    # Reserve 80% for batch processing, 20% for final merge
                    batch_progress = (batch_idx / num_batches) * 0.8
                    progress_callback(
                        batch_progress,
                        f"Processing batch {batch_idx + 1}/{num_batches} ({len(batch)} clips)...",
                    )

                intermediate_path = intermediates_dir / f"batch_{batch_idx:03d}.mp4"

                if len(batch) == 1:
                    # Single clip in batch - just copy it
                    shutil.copy2(batch[0].path, intermediate_path)
                    batch_duration = batch[0].duration
                else:
                    # Process batch with xfade (8 clips = 7 xfades, manageable memory)
                    # Create sub-progress callback that maps batch progress to overall progress
                    # Reserve 80% for batch processing, 20% for final merge
                    batch_base_progress = (batch_idx / num_batches) * 0.8
                    batch_progress_range = (1 / num_batches) * 0.8

                    def make_batch_progress_cb(
                        base: float, range_: float, idx: int, total: int
                    ) -> Callable[[float, str], None]:
                        def batch_progress_cb(pct: float, msg: str) -> None:
                            if progress_callback:
                                # Map batch-internal progress (0.0-1.0) to overall progress
                                overall_pct = base + (pct * range_)
                                progress_callback(overall_pct, f"Batch {idx + 1}/{total}: {msg}")

                        return batch_progress_cb

                    cb = make_batch_progress_cb(
                        batch_base_progress, batch_progress_range, batch_idx, num_batches
                    )
                    self._assemble_batch_direct(
                        batch, intermediate_path, cb if progress_callback else None
                    )

                    # Calculate batch duration accounting for fade overlaps
                    batch_duration = sum(c.duration for c in batch)
                    batch_duration -= self.settings.transition_duration * (len(batch) - 1)

                # Preserve title screen flag if last clip in batch is a title screen
                # (this ensures ending screen gets fade transition in final merge)
                is_title = batch[-1].is_title_screen if batch else False

                intermediate_clips.append(
                    AssemblyClip(
                        path=intermediate_path,
                        duration=batch_duration,
                        date=None,
                        asset_id=f"batch_{batch_idx}",
                        is_title_screen=is_title,
                    )
                )

                logger.info(
                    f"Batch {batch_idx + 1}/{num_batches} complete: {intermediate_path.name}"
                )

                # Check for cancellation after each batch
                self._check_cancelled()

            # Final assembly: concatenate intermediate files with xfade
            if progress_callback:
                progress_callback(0.85, f"Merging {num_batches} batches...")

            logger.info(f"Final merge: {len(intermediate_clips)} intermediate files")

            # Use specialized merge that probes actual durations to fix audio sync
            result = self._merge_intermediate_batches(
                intermediate_clips, output_path, progress_callback
            )

            # Clean up intermediate files on success (unless debug mode)
            if self.settings.debug_preserve_intermediates:
                logger.info(f"Debug mode: preserving intermediate files in {intermediates_dir}")
            else:
                logger.info(f"Cleaning up intermediate files in {intermediates_dir}")
                shutil.rmtree(intermediates_dir, ignore_errors=True)

            return result

        except Exception:
            # Keep intermediate files for debugging on failure
            logger.error(
                f"Chunked assembly failed. Intermediate files preserved in: {intermediates_dir}"
            )
            raise

    def _probe_duration(self, file_path: Path, stream_type: str = "audio") -> float:
        """Probe actual duration of a specific stream using ffprobe.

        Args:
            file_path: Path to the media file.
            stream_type: Stream type to probe ("audio" or "video"). Default "audio".

        Returns:
            Duration in seconds, or 0.0 if probing fails.
        """
        try:
            # Probe the specific stream's duration, not format duration
            # This is important because audio and video durations can differ
            stream_select = "a:0" if stream_type == "audio" else "v:0"
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-select_streams",
                    stream_select,
                    "-show_entries",
                    "stream=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(file_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            duration = result.stdout.strip()
            if duration and duration != "N/A":
                return float(duration)

            # Fallback to format duration if stream duration unavailable
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(file_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return float(result.stdout.strip())
        except (ValueError, subprocess.SubprocessError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Failed to probe {stream_type} duration of {file_path}: {e}")
            return 0.0

    def _probe_framerate(self, path: Path) -> float:
        """Probe the frame rate of a video file.

        Args:
            path: Path to the video file.

        Returns:
            Frame rate as a float (e.g., 30.0, 59.94, 60.0).
            Returns 60.0 as fallback if probing fails.
        """
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=r_frame_rate",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                fps_str = result.stdout.strip()
                # Parse fraction like "30/1" or "60000/1001"
                if "/" in fps_str:
                    num, den = fps_str.split("/")
                    return float(num) / float(den)
                return float(fps_str)
        except (ValueError, subprocess.SubprocessError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Failed to probe framerate of {path}: {e}")
        return 60.0  # Default fallback

    def _has_audio_stream(self, path: Path) -> bool:
        """Check if video file has an audio stream.

        Args:
            path: Path to the video file.

        Returns:
            True if the file has at least one audio stream.
        """
        try:
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index,codec_name,sample_rate,channels",
                "-of",
                "json",
                str(path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                logger.warning(f"Failed to probe audio for {path.name}: {result.stderr[:200]}")
                return False

            data = json.loads(result.stdout)
            streams = data.get("streams", [])

            if not streams:
                logger.debug(f"No audio stream in {path.name}")
                return False

            # Log audio stream info for debugging
            for stream in streams:
                logger.debug(
                    f"Audio stream in {path.name}: "
                    f"codec={stream.get('codec_name')}, "
                    f"rate={stream.get('sample_rate')}, "
                    f"channels={stream.get('channels')}"
                )
            return True
        except (subprocess.SubprocessError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            logger.warning(f"Error checking audio stream for {path.name}: {e}")
            return False

    def _has_video_stream(self, path: Path) -> bool:
        """Check if file has a video stream.

        Args:
            path: Path to the file.

        Returns:
            True if the file has at least one video stream.
        """
        try:
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return bool(result.stdout.strip())
        except (subprocess.SubprocessError, subprocess.TimeoutExpired) as e:
            logger.warning(f"Error checking video stream for {path.name}: {e}")
            return False

    def _merge_intermediate_batches(
        self,
        batches: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Merge intermediate batch files using probed durations for audio sync.

        This method is specifically for merging intermediate batch files during
        chunked assembly. Unlike _assemble_batch_direct, it probes the ACTUAL
        duration from each intermediate file rather than trusting the declared
        duration, which fixes audio desync issues caused by AAC frame alignment
        and encoding artifacts.

        Args:
            batches: List of intermediate batch clips.
            output_path: Output video path.
            progress_callback: Progress callback.

        Returns:
            Path to assembled video.
        """
        if len(batches) < 2:
            if len(batches) == 1:
                import shutil

                shutil.copy2(batches[0].path, output_path)
                return output_path
            raise ValueError("No batches to merge")

        # Probe ACTUAL durations from files - don't trust declared durations
        # IMPORTANT: Probe BOTH audio and video durations as they can differ!
        audio_durations: list[float] = []
        video_durations: list[float] = []
        for batch in batches:
            audio_dur = self._probe_duration(batch.path, stream_type="audio")
            video_dur = self._probe_duration(batch.path, stream_type="video")

            if audio_dur <= 0:
                logger.warning(
                    f"Could not probe audio duration of {batch.path}, using declared {batch.duration}"
                )
                audio_dur = batch.duration
            if video_dur <= 0:
                logger.warning(
                    f"Could not probe video duration of {batch.path}, using declared {batch.duration}"
                )
                video_dur = batch.duration

            # Log mismatches for debugging
            if abs(audio_dur - video_dur) > 0.05:
                logger.warning(
                    f"A/V duration mismatch in {batch.path.name}: audio={audio_dur:.3f}s, video={video_dur:.3f}s"
                )
            if abs(audio_dur - batch.duration) > 0.1:
                logger.info(
                    f"Audio duration mismatch for {batch.path.name}: declared={batch.duration:.3f}s, actual={audio_dur:.3f}s"
                )

            audio_durations.append(audio_dur)
            video_durations.append(video_dur)

        logger.info(
            f"Merging {len(batches)} batches - audio: {[f'{d:.2f}s' for d in audio_durations]}, video: {[f'{d:.2f}s' for d in video_durations]}"
        )

        fade_duration = self.settings.transition_duration

        # Determine target resolution
        if self.settings.target_resolution:
            target_w, target_h = self.settings.target_resolution
        elif self.settings.auto_resolution:
            target_w, target_h = self._detect_best_resolution(batches)
        else:
            config = get_config()
            target_w, target_h = config.output.resolution_tuple

        # Detect orientation from batches
        portrait_count = sum(
            1
            for batch in batches
            if (res := self._get_video_resolution(batch.path)) and res[1] > res[0]
        )
        if portrait_count > len(batches) // 2:
            target_w, target_h = target_h, target_w

        # Build filter complex
        inputs = []
        filter_parts = []

        for batch in batches:
            inputs.extend(["-i", str(batch.path)])

        # Pixel format
        import sys

        if self.settings.preserve_hdr:
            pix_fmt = "p010le" if sys.platform == "darwin" else "yuv420p10le"
        else:
            pix_fmt = "yuv420p"
        target_fps = 60

        hdr_type = _get_dominant_hdr_type(batches) if self.settings.preserve_hdr else "hlg"
        batch_hdr_types = (
            _get_clip_hdr_types(batches) if self.settings.preserve_hdr else [None] * len(batches)
        )

        colorspace_filter = _get_colorspace_filter(hdr_type) if self.settings.preserve_hdr else ""

        # Scale all inputs
        for i, _batch in enumerate(batches):
            hdr_conversion = ""
            if self.settings.preserve_hdr and batch_hdr_types[i] != hdr_type:
                hdr_conversion = _get_hdr_conversion_filter(batch_hdr_types[i], hdr_type)

            filter_parts.append(
                f"[{i}:v]setpts=PTS-STARTPTS,"
                f"scale={target_w}:{target_h}:"
                f"force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,"
                f"fps={target_fps},settb=1/{target_fps},"
                f"format={pix_fmt}{hdr_conversion}{colorspace_filter},setsar=1[v{i}scaled]"
            )

        # Prepare audio with PROBED AUDIO durations (the key fix!)
        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        audio_labels = []
        for i, (_batch, audio_dur) in enumerate(zip(batches, audio_durations, strict=False)):
            # Use ACTUAL AUDIO duration for silence generation and trimming
            filter_parts.append(
                f"anullsrc=r=48000:cl=stereo,atrim=0:{audio_dur}[a{i}silence];"
                f"[{i}:a]{audio_format},asetpts=PTS-STARTPTS[a{i}src];"
                f"[a{i}silence][a{i}src]amix=inputs=2:duration=first:weights='0 1'[a{i}mixed];"
                f"[a{i}mixed]atrim=0:{audio_dur},asetpts=PTS-STARTPTS[a{i}prep]"
            )
            audio_labels.append(f"[a{i}prep]")

        # Build xfade and acrossfade chains using PROBED durations
        # Video uses VIDEO durations, audio uses AUDIO durations for correct sync
        current_video = "[v0scaled]"
        current_audio = audio_labels[0]
        video_offset = 0.0

        for i in range(len(batches) - 1):
            next_idx = i + 1
            video_label = f"[v{i}{next_idx}]"
            audio_label = f"[a{i}{next_idx}]"

            # Use VIDEO duration for video xfade offset
            offset = video_offset + video_durations[i] - fade_duration

            # Video xfade
            filter_parts.append(
                f"{current_video}[v{next_idx}scaled]xfade=transition=fade:"
                f"duration={fade_duration}:offset={offset},settb=1/{target_fps}{video_label}"
            )

            # Audio crossfade
            filter_parts.append(
                f"{current_audio}{audio_labels[next_idx]}acrossfade=d={fade_duration}:c1=tri:c2=tri{audio_label}"
            )

            current_video = video_label
            current_audio = audio_label
            video_offset = offset

        filter_complex = ";".join(filter_parts)

        video_codec_args = _get_gpu_encoder_args(
            crf=self.settings.output_crf,
            preserve_hdr=self.settings.preserve_hdr,
            hdr_type=hdr_type,
        )

        logger.info(f"Final batch merge using encoder args: {video_codec_args}")

        cmd = [
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            current_video,
            "-map",
            current_audio,
            *video_codec_args,
            "-r",
            str(target_fps),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-max_muxing_queue_size",
            "1024",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        total_dur = sum(video_durations) - fade_duration * (len(batches) - 1)
        result = _run_ffmpeg_with_progress(cmd, total_dur, progress_callback)

        if result.returncode != 0:
            stderr_lines = result.stderr.split("\n")
            error_lines = [
                line
                for line in stderr_lines
                if "error" in line.lower() or "Error" in line or "invalid" in line.lower()
            ]
            if error_lines:
                error_msg = "\n".join(error_lines[-10:])
            else:
                error_msg = result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr
            raise RuntimeError(f"FFmpeg batch merge failed (code {result.returncode}): {error_msg}")

        return output_path

    def _assemble_batch_direct(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble a batch of clips directly without chunking check.

        This is the core xfade assembly logic, extracted to avoid infinite
        recursion when _assemble_chunked calls back into crossfade assembly.

        Args:
            clips: List of clips to assemble (should be <= CHUNK_SIZE).
            output_path: Output video path.
            progress_callback: Progress callback.

        Returns:
            Path to assembled video.
        """
        if len(clips) < 2:
            if len(clips) == 1:
                import shutil

                shutil.copy2(clips[0].path, output_path)
                return output_path
            raise ValueError("No clips to assemble")

        # This is the same logic as _assemble_with_crossfade but without
        # the chunking threshold check. We inline the key parts here.
        fade_duration = self.settings.transition_duration

        # Determine target resolution
        if self.settings.target_resolution:
            target_w, target_h = self.settings.target_resolution
        elif self.settings.auto_resolution:
            target_w, target_h = self._detect_best_resolution(clips)
        else:
            config = get_config()
            target_w, target_h = config.output.resolution_tuple

        # Detect orientation
        portrait_count = sum(
            1
            for clip in clips
            if (res := self._get_video_resolution(clip.path)) and res[1] > res[0]
        )
        if portrait_count > len(clips) // 2:
            target_w, target_h = target_h, target_w

        # Build filter complex
        inputs = []
        filter_parts = []

        for clip in clips:
            inputs.extend(["-i", str(clip.path)])

        # Use p010le for macOS VideoToolbox, yuv420p10le for software/other encoders
        import sys

        if self.settings.preserve_hdr:
            pix_fmt = "p010le" if sys.platform == "darwin" else "yuv420p10le"
        else:
            pix_fmt = "yuv420p"
        target_fps = 60

        hdr_type = _get_dominant_hdr_type(clips) if self.settings.preserve_hdr else "hlg"
        clip_hdr_types = (
            _get_clip_hdr_types(clips) if self.settings.preserve_hdr else [None] * len(clips)
        )

        colorspace_filter = _get_colorspace_filter(hdr_type) if self.settings.preserve_hdr else ""

        # Scale all inputs with smart zoom/blur for aspect ratio mismatches
        for i, clip in enumerate(clips):
            rotation_filter = ""
            if clip.rotation_override is not None and clip.rotation_override != 0:
                rotation_filter = _get_rotation_filter(clip.rotation_override) + ","

            hdr_conversion = ""
            if self.settings.preserve_hdr and clip_hdr_types[i] != hdr_type:
                hdr_conversion = _get_hdr_conversion_filter(clip_hdr_types[i], hdr_type)

            # Get clip resolution for smart aspect ratio handling
            clip_res = self._get_video_resolution(clip.path)
            if clip_res:
                src_w, src_h = clip_res
                src_ar = src_w / src_h
                target_ar = target_w / target_h
                ar_diff = abs(src_ar - target_ar) / max(src_ar, target_ar)

                if ar_diff > 0.05 and not clip.is_title_screen:
                    # Use smart zoom or blur background
                    face_center = self._get_face_center(clip.path)
                    aspect_filter = _get_aspect_ratio_filter(
                        clip_index=i,
                        src_w=src_w,
                        src_h=src_h,
                        target_w=target_w,
                        target_h=target_h,
                        face_center=face_center,
                        pix_fmt=pix_fmt,
                        target_fps=target_fps,
                        rotation_filter=rotation_filter,
                        hdr_conversion=hdr_conversion,
                        colorspace_filter=colorspace_filter,
                        output_suffix="scaled",
                    )
                    filter_parts.append(aspect_filter)
                    continue

            # Standard filter for same aspect ratio or title screens
            filter_parts.append(
                f"[{i}:v]{rotation_filter}setpts=PTS-STARTPTS,"
                f"scale={target_w}:{target_h}:"
                f"force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,"
                f"fps={target_fps},settb=1/{target_fps},"
                f"format={pix_fmt}{hdr_conversion}{colorspace_filter},setsar=1[v{i}scaled]"
            )

        # Build xfade chain with properly synced audio
        # The key insight: acrossfade expects audio streams to be trimmed and aligned
        # We need to trim each audio to match video timing
        current_input = "[v0scaled]"
        total_duration = 0.0

        # First, prepare all audio streams with proper trimming and format normalization
        # CRITICAL: All audio must have same sample rate/channels for acrossfade to work
        # Use amix with anullsrc as fallback to ensure we ALWAYS have audio of exact duration
        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        audio_labels = []
        for i, clip in enumerate(clips):
            # Check if this is a title screen (typically silent)
            is_title = clip.is_title_screen

            if is_title:
                # Title screens: generate silence with matching format
                filter_parts.append(
                    f"anullsrc=r=48000:cl=stereo,atrim=0:{clip.duration},{audio_format}[a{i}prep]"
                )
            else:
                # Regular clips: mix audio with silence fallback to ensure exact duration
                # This guarantees we have audio even if the source is short or missing
                # 1. Generate silence of exact duration as fallback
                # 2. Mix with actual audio (volumes: 0 for silence when audio exists, 1 for audio)
                # 3. Trim to exact duration
                filter_parts.append(
                    f"anullsrc=r=48000:cl=stereo,atrim=0:{clip.duration}[a{i}silence];"
                    f"[{i}:a]{audio_format},asetpts=PTS-STARTPTS[a{i}src];"
                    f"[a{i}silence][a{i}src]amix=inputs=2:duration=first:weights='0 1'[a{i}mixed];"
                    f"[a{i}mixed]atrim=0:{clip.duration},asetpts=PTS-STARTPTS[a{i}prep]"
                )
            audio_labels.append(f"[a{i}prep]")

        # Now build video xfade and audio crossfade chains
        current_audio = audio_labels[0]

        for i, clip in enumerate(clips[:-1]):
            next_idx = i + 1
            next_clip = clips[next_idx]
            output_label = f"[v{i}{next_idx}]"
            audio_label = f"[a{i}{next_idx}]"

            offset = total_duration + clip.duration - fade_duration

            # Video xfade
            filter_parts.append(
                f"{current_input}[v{next_idx}scaled]xfade=transition=fade:"
                f"duration={fade_duration}:offset={offset},settb=1/{target_fps}{output_label}"
            )

            # Audio crossfade - handle title screen transitions specially
            if next_clip.is_title_screen:
                # Fade out audio quickly when transitioning to title
                # Use a fast fade (half the transition duration)
                fast_fade = fade_duration / 2
                filter_parts.append(
                    f"{current_audio}afade=t=out:st={clip.duration - fast_fade}:d={fast_fade}[a{i}faded];"
                    f"[a{i}faded]{audio_labels[next_idx]}acrossfade=d={fade_duration}:c1=tri:c2=tri{audio_label}"
                )
            elif clip.is_title_screen:
                # Fade in audio when coming from title
                fast_fade = fade_duration / 2
                filter_parts.append(
                    f"{current_audio}{audio_labels[next_idx]}acrossfade=d={fade_duration}:c1=tri:c2=tri[a{i}xf];"
                    f"[a{i}xf]afade=t=in:st=0:d={fast_fade}{audio_label}"
                )
            else:
                # Normal audio crossfade
                filter_parts.append(
                    f"{current_audio}{audio_labels[next_idx]}acrossfade=d={fade_duration}:c1=tri:c2=tri{audio_label}"
                )

            current_input = output_label
            current_audio = audio_label
            total_duration = offset

        filter_complex = ";".join(filter_parts)

        video_codec_args = _get_gpu_encoder_args(
            crf=self.settings.output_crf,
            preserve_hdr=self.settings.preserve_hdr,
            hdr_type=hdr_type,
        )

        logger.info(f"Batch assembly using encoder args: {video_codec_args}")

        cmd = [
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            current_input,
            "-map",
            current_audio,
            *video_codec_args,
            "-r",
            str(target_fps),
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-max_muxing_queue_size",
            "1024",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        total_dur = self.estimate_duration(clips)
        result = _run_ffmpeg_with_progress(cmd, total_dur, progress_callback)

        if result.returncode != 0:
            # Get the actual error - search for error lines, not just last 1000 chars
            stderr_lines = result.stderr.split("\n")
            error_lines = [
                line
                for line in stderr_lines
                if "error" in line.lower() or "Error" in line or "invalid" in line.lower()
            ]
            if error_lines:
                error_msg = "\n".join(error_lines[-10:])  # Last 10 error lines
            else:
                error_msg = result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr
            raise RuntimeError(
                f"FFmpeg batch assembly failed (code {result.returncode}): {error_msg}"
            )

        return output_path

    # =========================================================================
    # NEW SCALABLE ASSEMBLY METHODS (No Batching)
    # =========================================================================

    def _assemble_scalable(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble clips using scalable transition-only rendering.

        This method is memory-efficient and scales to any number of clips:
        1. Encode each clip individually (constant memory per clip)
        2. Render only transition segments (0.5s each)
        3. Concat all with stream copy (no re-encoding)

        Memory usage: O(1) - constant regardless of clip count.

        Args:
            clips: List of clips to assemble.
            output_path: Output video path.
            progress_callback: Progress callback.

        Returns:
            Path to assembled video.
        """
        import shutil

        if len(clips) < 2:
            if len(clips) == 1:
                shutil.copy2(clips[0].path, output_path)
                return output_path
            raise ValueError("No clips to assemble")

        fade = self.settings.transition_duration
        temp_dir = output_path.parent / ".assembly_temps"
        temp_dir.mkdir(parents=True, exist_ok=True)

        # Determine target resolution ONCE for ALL clips (critical for consistency!)
        if self.settings.target_resolution:
            target_resolution = self.settings.target_resolution
        elif self.settings.auto_resolution:
            target_resolution = self._detect_best_resolution(clips)
        else:
            config = get_config()
            target_resolution = config.output.resolution_tuple

        logger.info(
            f"Scalable assembly: {len(clips)} clips with transition-only rendering "
            f"at {target_resolution[0]}x{target_resolution[1]}"
        )

        try:
            # Step 1: Encode each clip to target format (ALL at same resolution!)
            encoded_clips: list[Path] = []
            for i, clip in enumerate(clips):
                if progress_callback:
                    progress_callback(i / len(clips) * 0.6, f"Encoding clip {i + 1}/{len(clips)}")

                encoded_path = temp_dir / f"clip_{i:03d}.mp4"
                self._encode_single_clip(clip, encoded_path, target_resolution=target_resolution)
                encoded_clips.append(encoded_path)

                logger.debug(f"Encoded clip {i + 1}/{len(clips)}: {encoded_path.name}")

            # Step 2: Probe all clip durations first
            clip_durations = []
            for i, encoded_clip in enumerate(encoded_clips):
                dur = self._probe_duration(encoded_clip, "video")
                clip_durations.append(dur)
                logger.debug(f"Clip {i} duration: {dur:.2f}s")

            # Step 3: Determine which transitions are crossfade vs cut
            transitions = self._get_transition_types(clips)

            # Validate fade transitions - downgrade to cut if clips are too short
            min_duration_for_fade = fade * 2  # Need at least 2x fade duration for safe transition
            for i in range(len(transitions)):
                if transitions[i] == "fade":
                    clip_a_dur = clip_durations[i]
                    clip_b_dur = clip_durations[i + 1] if i + 1 < len(clip_durations) else 0

                    if clip_a_dur < min_duration_for_fade or clip_b_dur < min_duration_for_fade:
                        logger.warning(
                            f"Transition {i}: Downgrading fade to cut - "
                            f"clip durations too short ({clip_a_dur:.2f}s, {clip_b_dur:.2f}s) "
                            f"for {fade}s fade"
                        )
                        transitions[i] = "cut"

            logger.info(
                f"Transitions: {sum(1 for t in transitions if t == 'fade')} fades, {sum(1 for t in transitions if t == 'cut')} cuts"
            )

            # Step 4: Chunked xfade assembly
            # Opening all clips simultaneously in one xfade chain exhausts RAM
            # at 4K (swap fills disk). Instead, process in chunks of 4 clips,
            # then concat the chunks. Transitions within chunks use xfade
            # (frame-perfect). Chunk boundaries use cuts.
            CHUNK_SIZE = 4  # Max simultaneous decoders — safe for low-memory systems

            if len(encoded_clips) <= CHUNK_SIZE:
                # Small enough to process in one go
                if progress_callback:
                    progress_callback(0.7, "Building final assembly...")

                self._assemble_xfade_chain(
                    encoded_clips,
                    clip_durations,
                    transitions,
                    fade,
                    output_path,
                )
            else:
                # Split into chunks, process each with xfade, concat results
                chunks: list[tuple[list[Path], list[float], list[str]]] = []
                i = 0
                while i < len(encoded_clips):
                    end = min(i + CHUNK_SIZE, len(encoded_clips))
                    chunk_clips = encoded_clips[i:end]
                    chunk_durs = clip_durations[i:end]
                    chunk_trans = (
                        transitions[i : end - 1] if end < len(encoded_clips) else transitions[i:]
                    )
                    chunks.append((chunk_clips, chunk_durs, chunk_trans))
                    i = end

                logger.info(
                    f"Chunked assembly: {len(chunks)} chunks of up to {CHUNK_SIZE} clips each"
                )

                chunk_outputs: list[Path] = []
                for ci, (chunk_clips, chunk_durs, chunk_trans) in enumerate(chunks):
                    if progress_callback:
                        progress_callback(
                            0.6 + (ci / len(chunks)) * 0.3,
                            f"Assembling chunk {ci + 1}/{len(chunks)}",
                        )

                    if len(chunk_clips) == 1:
                        # Single clip chunk — no xfade needed
                        chunk_outputs.append(chunk_clips[0])
                    else:
                        chunk_path = temp_dir / f"chunk_{ci:02d}.mp4"
                        self._assemble_xfade_chain(
                            chunk_clips,
                            chunk_durs,
                            chunk_trans,
                            fade,
                            chunk_path,
                        )
                        chunk_outputs.append(chunk_path)

                # Concat the chunks (simple concat filter — no xfade at boundaries)
                if progress_callback:
                    progress_callback(0.95, "Joining chunks...")

                if len(chunk_outputs) == 1:
                    import shutil as _shutil

                    _shutil.copy2(chunk_outputs[0], output_path)
                else:
                    self._concat_with_copy(chunk_outputs, output_path)

            return output_path

        finally:
            # Cleanup temp directory
            if not self.settings.debug_preserve_intermediates:
                shutil.rmtree(temp_dir, ignore_errors=True)
            else:
                logger.info(f"Debug mode: preserving temp files in {temp_dir}")

    def _encode_single_clip(
        self,
        clip: AssemblyClip,
        output_path: Path,
        target_resolution: tuple[int, int] | None = None,
    ) -> None:
        """Encode a single clip to target format with A/V sync guarantee.

        Uses filter_complex with anullsrc fallback to handle clips that may
        have no audio stream or audio with different properties.

        Args:
            clip: The clip to encode.
            output_path: Output path for encoded clip.
            target_resolution: Target (width, height). If None, uses settings.
        """
        # Determine target resolution - prefer explicit parameter for consistency
        if target_resolution:
            target_w, target_h = target_resolution
        elif self.settings.target_resolution:
            target_w, target_h = self.settings.target_resolution
        else:
            # Fallback to config default - auto_resolution should be handled by caller
            config = get_config()
            target_w, target_h = config.output.resolution_tuple

        # Pixel format
        import sys

        if self.settings.preserve_hdr:
            pix_fmt = "p010le" if sys.platform == "darwin" else "yuv420p10le"
        else:
            pix_fmt = "yuv420p"

        target_fps = 60

        # HDR settings
        hdr_type = "hlg"  # Default to HLG (iPhone)
        if self.settings.preserve_hdr:
            clip_hdr = _detect_hdr_type(clip.path)
            if clip_hdr:
                hdr_type = clip_hdr
            colorspace_filter = _get_colorspace_filter(hdr_type)
        else:
            colorspace_filter = ""

        # Handle rotation
        rotation_filter = ""
        if clip.rotation_override is not None and clip.rotation_override != 0:
            rotation_filter = _get_rotation_filter(clip.rotation_override) + ","

        # Check if source has audio and log A/V duration mismatch
        has_audio = self._has_audio_stream(clip.path)
        if has_audio:
            video_dur = self._probe_duration(clip.path, "video")
            audio_dur = self._probe_duration(clip.path, "audio")
            if abs(video_dur - audio_dur) > 0.05:
                logger.warning(
                    f"A/V duration mismatch in {clip.path.name}: "
                    f"video={video_dur:.3f}s, audio={audio_dur:.3f}s, "
                    f"declared={clip.duration:.3f}s"
                )

        # Get encoder args
        video_codec_args = _get_gpu_encoder_args(
            crf=self.settings.output_crf,
            preserve_hdr=self.settings.preserve_hdr,
            hdr_type=hdr_type,
        )

        # Build filter_complex with guaranteed A/V sync
        # CRITICAL: Both video AND audio must be trimmed to EXACT same duration
        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"

        # Detect source framerate to avoid frame doubling artifacts
        # When upsampling 30fps to 60fps, simple fps filter duplicates frames
        # which causes stutter at transition boundaries
        source_fps = self._probe_framerate(clip.path)

        # For sub-50fps sources (24, 25, 30fps), use tmix frame blending
        # This creates interpolated frames instead of duplicates
        if source_fps < 50:
            # tmix blends adjacent frames for smoother 30->60 conversion
            # frames=2 averages pairs of duplicated frames, reducing stutter
            fps_filter = f"fps={target_fps},tmix=frames=2:weights='1 1'"
            logger.debug(
                f"Using tmix frame blending for {source_fps:.1f}fps source: {clip.path.name}"
            )
        else:
            fps_filter = f"fps={target_fps}"

        # Common suffix for all video filters
        common_suffix = (
            f"{fps_filter},settb=1/{target_fps},"
            f"format={pix_fmt}{colorspace_filter},setsar=1,"
            f"trim=0:{clip.duration},setpts=PTS-STARTPTS"
        )

        # Build audio filter part
        # CRITICAL: Final asetpts=PTS-STARTPTS ensures clean timestamps for concat
        # This prevents AAC priming issues from accumulating across clips
        if has_audio:
            audio_filter = (
                f"[0:a]{audio_format},asetpts=PTS-STARTPTS,"
                f"apad=whole_dur={clip.duration},atrim=0:{clip.duration},asetpts=PTS-STARTPTS[aout]"
            )
        else:
            logger.debug(f"No audio in {clip.path.name}, generating silence")
            audio_filter = f"anullsrc=r=48000:cl=stereo,atrim=0:{clip.duration},{audio_format},asetpts=PTS-STARTPTS[aout]"

        # Video filter: process, scale, AND TRIM to exact duration
        # Check scale_mode to determine how to handle aspect ratio mismatch
        use_blur = self.settings.scale_mode == "blur" and not clip.is_title_screen
        use_smart_zoom = self.settings.scale_mode == "smart_zoom" and not clip.is_title_screen

        if use_smart_zoom:
            # Smart zoom: detect face and crop centered on it
            face_center = self._get_face_center(clip.path)
            if face_center:
                # Get source resolution for smart crop calculation
                clip_res = self._get_video_resolution(clip.path)
                if clip_res:
                    src_w, src_h = clip_res
                    crop_filter = _get_smart_crop_filter(
                        src_w, src_h, target_w, target_h, face_center[0], face_center[1]
                    )
                    video_filter = (
                        f"{rotation_filter}setpts=PTS-STARTPTS,{crop_filter},{common_suffix}"
                    )
                    filter_complex = f"[0:v]{video_filter}[vout];{audio_filter}"
                    logger.info(
                        f"Smart zoom: cropping centered on face at ({face_center[0]:.2f}, {face_center[1]:.2f})"
                    )
                else:
                    # Fallback to blur if resolution detection fails
                    use_blur = True
            else:
                # No face detected - fallback to blur
                logger.debug(f"No face detected in {clip.path.name}, using blur background")
                use_blur = True

        if use_blur:
            # Blur background (Instagram-style) for aspect ratio mismatches
            # Create blurred/scaled background, overlay sharp foreground
            filter_complex = (
                f"[0:v]{rotation_filter}setpts=PTS-STARTPTS,split[bg][fg];"
                f"[bg]scale={target_w}:{target_h}:force_original_aspect_ratio=increase:flags=fast_bilinear,"
                f"crop={target_w}:{target_h},boxblur=luma_radius=150:chroma_radius=150:luma_power=3:chroma_power=3[blurred];"
                f"[fg]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease:flags=lanczos[scaled];"
                f"[blurred][scaled]overlay=(W-w)/2:(H-h)/2,{common_suffix}[vout];"
                f"{audio_filter}"
            )
        elif not use_smart_zoom:
            # Black bars (letterbox/pillarbox) - default for title screens or explicit black_bars mode
            video_filter = (
                f"{rotation_filter}setpts=PTS-STARTPTS,"
                f"scale={target_w}:{target_h}:"
                f"force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,"
                f"{common_suffix}"
            )
            filter_complex = f"[0:v]{video_filter}[vout];{audio_filter}"

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(clip.path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            *video_codec_args,
            "-r",
            str(target_fps),
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
            raise RuntimeError(f"Failed to encode clip: {result.stderr[-500:]}")

    def _trim_segment_copy(
        self,
        input_path: Path,
        output_path: Path,
        start: float,
        duration: float,
    ) -> None:
        """Trim a video segment using stream copy (instant, no re-encoding).

        Args:
            input_path: Input video path.
            output_path: Output path for trimmed segment.
            start: Start time in seconds.
            duration: Duration in seconds.
        """
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start),
            "-i",
            str(input_path),
            "-t",
            str(duration),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to trim segment: {result.stderr[-500:]}")

    def _trim_segment_reencode(
        self,
        input_path: Path,
        output_path: Path,
        start: float,
        duration: float,
    ) -> None:
        """Trim a video segment with re-encoding for frame-accurate boundaries.

        Uses filter_complex with anullsrc mixing to guarantee audio output,
        even when trimming causes audio/video misalignment.

        Args:
            input_path: Input video path.
            output_path: Output path for trimmed segment.
            start: Start time in seconds.
            duration: Duration in seconds.
        """
        # Get encoder args matching main encoding settings
        video_codec_args = _get_gpu_encoder_args(
            crf=self.settings.output_crf,
            preserve_hdr=self.settings.preserve_hdr,
        )

        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"

        # Use filter_complex with anullsrc mixing to guarantee audio
        filter_complex = (
            # Video: trim and reset timestamps
            f"[0:v]trim=start={start}:duration={duration},setpts=PTS-STARTPTS[vout];"
            # Generate silence for guaranteed duration
            f"anullsrc=r=48000:cl=stereo,atrim=0:{duration}[silence];"
            # Try to extract audio
            f"[0:a]atrim=start={start}:duration={duration},{audio_format},"
            f"asetpts=PTS-STARTPTS,apad=whole_dur={duration}[asrc];"
            # Mix: silence provides guaranteed duration, source provides content
            # Final atrim + asetpts ensures exact duration and resets timestamps
            # to prevent AAC priming issues during concat
            f"[silence][asrc]amix=inputs=2:duration=longest:weights='0.001 1',"
            f"atrim=0:{duration},asetpts=PTS-STARTPTS[aout]"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            *video_codec_args,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        # Fallback: if audio extraction failed, use silence only
        if result.returncode != 0:
            logger.warning(f"Trim with audio failed, using silence: {result.stderr[-200:]}")

            filter_complex_silent = (
                f"[0:v]trim=start={start}:duration={duration},setpts=PTS-STARTPTS[vout];"
                f"anullsrc=r=48000:cl=stereo,atrim=0:{duration},{audio_format},asetpts=PTS-STARTPTS[aout]"
            )

            cmd_silent = [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-filter_complex",
                filter_complex_silent,
                "-map",
                "[vout]",
                "-map",
                "[aout]",
                *video_codec_args,
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(output_path),
            ]

            result = subprocess.run(cmd_silent, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"Failed to trim segment (reencode): {result.stderr[-500:]}")

    def _render_transition_framewise(
        self,
        seg_a: Path,
        seg_b: Path,
        output_path: Path,
        fade_dur: float,
        target_fps: int = 60,
    ) -> bool:
        """Render crossfade transition frame-by-frame with GPU acceleration.

        Uses PyAV for precise frame timing and Taichi for GPU-accelerated blending.
        Memory efficient: only 2 frames in memory at once (~50MB for 4K).

        GPU backends (auto-selected):
          - Apple Silicon: Metal
          - NVIDIA: CUDA
          - AMD/Intel: Vulkan
          - Fallback: CPU

        Args:
            seg_a: First segment video path (end of clip A).
            seg_b: Second segment video path (start of clip B).
            output_path: Output path for transition segment.
            fade_dur: Duration of the crossfade in seconds.
            target_fps: Target frame rate (default 60fps for smooth transitions).

        Returns:
            True if successful, False if PyAV/GPU not available (fallback needed).

        Raises:
            RuntimeError: If rendering fails after GPU blending is attempted.
        """
        # Try to import PyAV (optional dependency)
        try:
            import av
        except ImportError:
            logger.debug("PyAV not available, falling back to xfade")
            return False

        # Try to import GPU blending (catch all exceptions to be safe)
        use_gpu = False
        blend_frames_gpu_fn: Callable[..., Any] | None = None
        try:
            from immich_memories.processing.transition_blend import (
                blend_frames_gpu as _blend_frames_gpu,
            )
            from immich_memories.processing.transition_blend import (
                is_gpu_blending_available,
            )

            blend_frames_gpu_fn = _blend_frames_gpu

            use_gpu = is_gpu_blending_available()
        except Exception as e:
            # Catch any exception (ImportError, RuntimeError from Taichi, etc.)
            logger.debug(f"GPU blending not available: {e}")
            use_gpu = False
            blend_frames_gpu_fn = None

        import numpy as np

        # Detect HDR
        is_hdr = _detect_hdr_type(seg_a) is not None

        try:
            # Open input containers
            container_a = av.open(str(seg_a))
            container_b = av.open(str(seg_b))
            stream_a = container_a.streams.video[0]
            stream_b = container_b.streams.video[0]

            # Get dimensions
            width = stream_a.width
            height = stream_a.height

            # Calculate total frames for the transition
            total_frames = int(fade_dur * target_fps)
            if total_frames < 2:
                total_frames = 2

            logger.debug(
                f"Frame-by-frame transition: {width}x{height}, {total_frames} frames, "
                f"HDR={is_hdr}, GPU={use_gpu}"
            )

            # Decode all frames from both segments
            # For short transitions (0.3-0.5s), this is ~18-30 frames = ~50-100MB for 4K
            dtype: type[np.uint8] | type[np.uint16]
            if is_hdr:
                pix_fmt = "rgb48le"  # 16-bit RGB for HDR
                dtype = np.uint16
            else:
                pix_fmt = "rgb24"  # 8-bit RGB for SDR
                dtype = np.uint8

            frames_a = []
            for frame in container_a.decode(stream_a):
                frames_a.append(frame.to_ndarray(format=pix_fmt))

            frames_b = []
            for frame in container_b.decode(stream_b):
                frames_b.append(frame.to_ndarray(format=pix_fmt))

            container_a.close()
            container_b.close()

            # Ensure we have enough frames
            if len(frames_a) < total_frames or len(frames_b) < total_frames:
                logger.warning(
                    f"Not enough frames for transition: A={len(frames_a)}, B={len(frames_b)}, "
                    f"need={total_frames}. Adjusting."
                )
                total_frames = min(len(frames_a), len(frames_b), total_frames)
                if total_frames < 2:
                    logger.warning("Not enough frames for frame-by-frame transition")
                    return False

            # Select frames to use (evenly distributed across segment)
            # This handles case where segment has more frames than needed
            indices_a = np.linspace(0, len(frames_a) - 1, total_frames, dtype=int)
            indices_b = np.linspace(0, len(frames_b) - 1, total_frames, dtype=int)

            # Create output container
            output_container = av.open(str(output_path), mode="w")

            # Configure output stream based on platform and HDR
            import sys

            if sys.platform == "darwin":
                # macOS: Use VideoToolbox HEVC
                output_stream = output_container.add_stream("hevc_videotoolbox", rate=target_fps)
                if is_hdr:
                    output_stream.pix_fmt = "p010le"
                    output_stream.options = {
                        "tag": "hvc1",
                        "colorspace": "bt2020nc",
                        "color_primaries": "bt2020",
                        "color_trc": "arib-std-b67",
                    }
                else:
                    output_stream.pix_fmt = "yuv420p"
                    output_stream.options = {"tag": "hvc1"}
            else:
                # Other platforms: Use libx265
                output_stream = output_container.add_stream("libx265", rate=target_fps)
                if is_hdr:
                    output_stream.pix_fmt = "yuv420p10le"
                else:
                    output_stream.pix_fmt = "yuv420p"
                output_stream.options = {"crf": str(self.settings.output_crf), "preset": "fast"}

            output_stream.width = width
            output_stream.height = height

            # Blend and encode frame-by-frame
            for i in range(total_frames):
                alpha = i / (total_frames - 1) if total_frames > 1 else 0.5

                frame_a = frames_a[indices_a[i]]
                frame_b = frames_b[indices_b[i]]

                # GPU-accelerated blend (Metal/CUDA/Vulkan/CPU)
                if use_gpu and blend_frames_gpu_fn is not None:
                    blended = blend_frames_gpu_fn(frame_a, frame_b, alpha)
                else:
                    # Numpy fallback
                    blended = (
                        (1.0 - alpha) * frame_a.astype(np.float32)
                        + alpha * frame_b.astype(np.float32)
                    ).astype(dtype)

                # Convert to PyAV frame
                # PyAV expects rgb24 or rgb48le for from_ndarray, then we convert to yuv
                av_frame = av.VideoFrame.from_ndarray(blended, format=pix_fmt)
                av_frame.pts = i

                # Encode
                for packet in output_stream.encode(av_frame):
                    output_container.mux(packet)

            # Flush encoder
            for packet in output_stream.encode():
                output_container.mux(packet)

            output_container.close()

            # Add audio crossfade using FFmpeg
            self._add_audio_crossfade(seg_a, seg_b, output_path, fade_dur)

            logger.debug(f"Frame-by-frame transition completed: {output_path}")
            return True

        except Exception as e:
            logger.warning(f"Frame-by-frame transition failed: {e}")
            # Clean up partial output
            if output_path.exists():
                output_path.unlink()
            return False

    def _add_audio_crossfade(
        self,
        seg_a: Path,
        seg_b: Path,
        video_path: Path,
        fade_dur: float,
    ) -> None:
        """Add crossfaded audio to the video using FFmpeg.

        Takes the video file (with no audio or placeholder audio) and muxes in
        crossfaded audio from the two source segments.

        Args:
            seg_a: First segment (audio source A).
            seg_b: Second segment (audio source B).
            video_path: Video file to add audio to (modified in place).
            fade_dur: Duration of the audio crossfade.
        """
        temp_output = video_path.with_suffix(".tmp.mp4")

        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"

        # Try acrossfade first (cleanest result)
        filter_complex = (
            f"[1:a]{audio_format}[a1];"
            f"[2:a]{audio_format}[a2];"
            f"[a1][a2]acrossfade=d={fade_dur}:c1=tri:c2=tri,"
            f"atrim=0:{fade_dur},asetpts=PTS-STARTPTS[aout]"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),  # Video (may have no audio)
            "-i",
            str(seg_a),  # Audio source A
            "-i",
            str(seg_b),  # Audio source B
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(temp_output),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            # Fallback: use amix with fades
            logger.debug(f"acrossfade failed, trying amix: {result.stderr[-200:]}")

            filter_complex_fallback = (
                f"[1:a]{audio_format},afade=t=out:st=0:d={fade_dur}[afade_a];"
                f"[2:a]{audio_format},afade=t=in:st=0:d={fade_dur}[afade_b];"
                f"[afade_a][afade_b]amix=inputs=2:duration=first,"
                f"atrim=0:{fade_dur},asetpts=PTS-STARTPTS[aout]"
            )

            cmd_fallback = [
                "ffmpeg",
                "-y",
                "-i",
                str(video_path),
                "-i",
                str(seg_a),
                "-i",
                str(seg_b),
                "-filter_complex",
                filter_complex_fallback,
                "-map",
                "0:v",
                "-map",
                "[aout]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                "-shortest",
                str(temp_output),
            ]

            result = subprocess.run(cmd_fallback, capture_output=True, text=True)

            if result.returncode != 0:
                # Last resort: generate silent audio
                logger.warning(f"Audio crossfade failed, using silence: {result.stderr[-200:]}")

                cmd_silent = [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(video_path),
                    "-f",
                    "lavfi",
                    "-i",
                    f"anullsrc=r=48000:cl=stereo:d={fade_dur}",
                    "-map",
                    "0:v",
                    "-map",
                    "1:a",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-movflags",
                    "+faststart",
                    "-shortest",
                    str(temp_output),
                ]

                result = subprocess.run(cmd_silent, capture_output=True, text=True)
                if result.returncode != 0:
                    logger.error(f"Failed to add any audio: {result.stderr[-200:]}")
                    return  # Keep video without audio rather than failing

        # Replace original with temp
        if temp_output.exists():
            temp_output.replace(video_path)

    def _extract_segment_for_transition(
        self,
        src: Path,
        output: Path,
        start: float,
        duration: float,
    ) -> None:
        """Extract a segment from a video for use in a transition.

        Uses filter_complex with anullsrc mixing to GUARANTEE audio output,
        even when seeking causes audio/video timestamp misalignment.

        Args:
            src: Source video path.
            output: Output path for extracted segment.
            start: Start time in seconds.
            duration: Duration to extract.
        """
        video_codec_args = _get_gpu_encoder_args(
            crf=self.settings.output_crf,
            preserve_hdr=self.settings.preserve_hdr,
        )

        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"

        # ALWAYS use filter_complex with anullsrc to guarantee audio output.
        # The problem with -ss + -af is that seeking can cause audio/video
        # timestamp misalignment, resulting in no audio samples being processed.
        #
        # Solution: Generate silence as a guaranteed audio source, then mix
        # with whatever audio we can extract (if any). This ensures we always
        # have audio output even if the source audio is misaligned or missing.

        # Use -ss BEFORE -i for fast seeking, then use filters for precise extraction
        # This is more reliable than -ss after -i which can cause audio issues
        filter_complex = (
            # Video: trim to exact segment, reset timestamps
            f"[0:v]trim=start={start}:duration={duration},setpts=PTS-STARTPTS[vout];"
            # Generate silence for guaranteed duration
            f"anullsrc=r=48000:cl=stereo,atrim=0:{duration}[silence];"
            # Try to extract audio (may fail if seeking lands between audio frames)
            f"[0:a]atrim=start={start}:duration={duration},{audio_format},"
            f"asetpts=PTS-STARTPTS,apad=whole_dur={duration}[asrc];"
            # Mix: silence provides guaranteed duration, source audio provides content
            # weights='0.001 1' means silence is nearly muted, source audio is full volume
            # duration=longest ensures we get the full duration even if source is short
            # Final atrim + asetpts ensures exact duration and clean timestamps for concat
            f"[silence][asrc]amix=inputs=2:duration=longest:weights='0.001 1',"
            f"atrim=0:{duration},asetpts=PTS-STARTPTS[aout]"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            *video_codec_args,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        # If filter_complex failed (e.g., atrim on audio failed), retry with silence only
        if result.returncode != 0:
            logger.warning(
                f"Extraction with audio failed, retrying with silence: {result.stderr[-200:]}"
            )

            filter_complex_silent = (
                f"[0:v]trim=start={start}:duration={duration},setpts=PTS-STARTPTS[vout];"
                f"anullsrc=r=48000:cl=stereo,atrim=0:{duration},{audio_format},asetpts=PTS-STARTPTS[aout]"
            )

            cmd_silent = [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-filter_complex",
                filter_complex_silent,
                "-map",
                "[vout]",
                "-map",
                "[aout]",
                *video_codec_args,
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(output),
            ]

            result = subprocess.run(cmd_silent, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"Failed to extract segment: {result.stderr[-500:]}")

        # Verify the result has both streams and proper duration
        has_video_out = self._has_video_stream(output)
        has_audio_out = self._has_audio_stream(output)
        if not has_video_out or not has_audio_out:
            logger.error(
                f"Segment extraction incomplete: {output.name} - "
                f"video={has_video_out}, audio={has_audio_out}"
            )
        else:
            # Log actual durations for debugging
            out_video_dur = self._probe_duration(output, "video")
            out_audio_dur = self._probe_duration(output, "audio")
            logger.debug(
                f"Extracted {output.name}: requested={duration:.3f}s, "
                f"actual video={out_video_dur:.3f}s, audio={out_audio_dur:.3f}s"
            )

    def _render_transition_segment(
        self,
        src_a: Path,
        a_start: float,
        a_duration: float,
        src_b: Path,
        b_start: float,
        b_duration: float,
        output_path: Path,
    ) -> None:
        """Render a crossfade transition segment using pre-extracted segments.

        This approach extracts each segment to a clean temporary file first,
        then applies xfade to those clean files. This avoids timestamp and
        filter complexity issues that cause "nothing written" errors.

        Args:
            src_a: First source video path.
            a_start: Start time in src_a.
            a_duration: Duration to extract from src_a.
            src_b: Second source video path.
            b_start: Start time in src_b.
            b_duration: Duration to extract from src_b.
            output_path: Output path for transition segment.
        """
        import shutil

        # Safety validation - ensure we have valid positions
        if a_start < 0:
            logger.error(f"Invalid a_start={a_start}, src_a duration may be too short")
            raise ValueError(f"Transition source A start position is negative: {a_start}")
        if b_start < 0:
            logger.error(f"Invalid b_start={b_start}")
            raise ValueError(f"Transition source B start position is negative: {b_start}")
        if a_duration <= 0 or b_duration <= 0:
            raise ValueError(f"Invalid duration: a={a_duration}, b={b_duration}")

        fade_dur = a_duration  # Both should be equal (the fade duration)

        # Probe actual durations and apply safety margin to avoid seeking past content
        actual_a_dur = self._probe_duration(src_a, "video")
        actual_b_dur = self._probe_duration(src_b, "video")

        # Check audio streams for better diagnostics
        has_audio_a = self._has_audio_stream(src_a)
        has_audio_b = self._has_audio_stream(src_b)

        # Apply safety margin (0.1s) to avoid edge-of-video issues
        safety_margin = 0.1
        max_a_start = max(0, actual_a_dur - fade_dur - safety_margin)
        if a_start > max_a_start:
            logger.warning(
                f"Transition seek adjusted: a_start {a_start:.2f}s -> {max_a_start:.2f}s "
                f"(src_a duration: {actual_a_dur:.2f}s, fade: {fade_dur:.2f}s)"
            )
            a_start = max_a_start

        logger.debug(
            f"Rendering transition: {src_a.name}[{a_start:.2f}s] -> {src_b.name}[{b_start:.2f}s], "
            f"fade={fade_dur:.2f}s, has_audio: a={has_audio_a}, b={has_audio_b}, "
            f"durations: a={actual_a_dur:.2f}s, b={actual_b_dur:.2f}s"
        )

        # Get encoder args for final output
        hdr_type = "hlg"
        if self.settings.preserve_hdr:
            hdr_a = _detect_hdr_type(src_a)
            if hdr_a:
                hdr_type = hdr_a

        video_codec_args = _get_gpu_encoder_args(
            crf=self.settings.output_crf,
            preserve_hdr=self.settings.preserve_hdr,
            hdr_type=hdr_type,
        )

        # Create temp directory for segment extraction
        temp_dir = output_path.parent / f".trans_{output_path.stem}"
        temp_dir.mkdir(exist_ok=True)

        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"

        try:
            # Step 1: Extract segment A (end of clip A)
            seg_a = temp_dir / "seg_a.mp4"
            self._extract_segment_for_transition(src_a, seg_a, a_start, a_duration)

            # Step 2: Extract segment B (start of clip B)
            seg_b = temp_dir / "seg_b.mp4"
            self._extract_segment_for_transition(src_b, seg_b, b_start, b_duration)

            # Verify extracted segments have audio (should be guaranteed now)
            seg_a_audio = self._has_audio_stream(seg_a)
            seg_b_audio = self._has_audio_stream(seg_b)
            logger.debug(f"Extracted segments have audio: seg_a={seg_a_audio}, seg_b={seg_b_audio}")

            # Step 3: Try frame-by-frame rendering first (precise, no stutter)
            # This uses PyAV + Taichi GPU for frame-accurate blending
            if self._render_transition_framewise(seg_a, seg_b, output_path, fade_dur):
                logger.debug("Frame-by-frame transition successful")
                return  # Success! Skip xfade fallback

            # Fallback: Use FFmpeg xfade (may have stutter with mixed framerates)
            logger.debug("Falling back to FFmpeg xfade for transition")

            # Get resolutions - xfade REQUIRES both inputs to have SAME resolution
            res_a = self._get_video_resolution(seg_a)
            res_b = self._get_video_resolution(seg_b)

            # Use the larger resolution as target (to avoid quality loss)
            if res_a and res_b:
                target_w = max(res_a[0], res_b[0])
                target_h = max(res_a[1], res_b[1])
            elif res_a:
                target_w, target_h = res_a
            elif res_b:
                target_w, target_h = res_b
            else:
                # Fallback to 1080p
                target_w, target_h = 1920, 1080

            logger.debug(
                f"Transition resolution: seg_a={res_a}, seg_b={res_b}, target={target_w}x{target_h}"
            )

            # Step 3: xfade the two clean segments
            # CRITICAL: Scale both inputs to same resolution before xfade!
            # xfade requires identical resolution for both inputs
            scale_filter = (
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
            )
            filter_complex = (
                f"[0:v]{scale_filter}[v0scaled];"
                f"[1:v]{scale_filter}[v1scaled];"
                # CRITICAL: trim + setpts after xfade to ensure exact duration and clean timestamps
                # This prevents frame timing issues that cause stutter/jump during transitions
                f"[v0scaled][v1scaled]xfade=transition=fade:duration={fade_dur}:offset=0,trim=0:{fade_dur},setpts=PTS-STARTPTS[vout];"
                # CRITICAL: atrim after acrossfade to force exact duration
                # This prevents AAC frame padding from extending audio past video duration
                # which causes timestamp discontinuities during concat
                f"[0:a][1:a]acrossfade=d={fade_dur}:c1=tri:c2=tri,atrim=0:{fade_dur},asetpts=PTS-STARTPTS[aout]"
            )

            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(seg_a),
                "-i",
                str(seg_b),
                "-filter_complex",
                filter_complex,
                "-map",
                "[vout]",
                "-map",
                "[aout]",
                *video_codec_args,
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
                # If acrossfade failed, try manual audio crossfade with amix
                logger.warning(
                    f"acrossfade failed (seg_a_audio={seg_a_audio}, seg_b_audio={seg_b_audio}), "
                    f"trying amix fallback: {result.stderr[-200:]}"
                )

                # Fallback: fade out A, fade in B, mix together with silence base
                # This should work even if audio streams have issues
                filter_complex_fallback = (
                    # Scale both videos to same resolution
                    f"[0:v]{scale_filter}[v0scaled];"
                    f"[1:v]{scale_filter}[v1scaled];"
                    # Video trim + setpts after xfade ensures exact duration and clean timestamps
                    f"[v0scaled][v1scaled]xfade=transition=fade:duration={fade_dur}:offset=0,trim=0:{fade_dur},setpts=PTS-STARTPTS[vout];"
                    # Create silence base for exact duration
                    f"anullsrc=r=48000:cl=stereo,atrim=0:{fade_dur}[silence];"
                    # Fade out audio A
                    f"[0:a]{audio_format},afade=t=out:st=0:d={fade_dur}[afade_a];"
                    # Fade in audio B
                    f"[1:a]{audio_format},afade=t=in:st=0:d={fade_dur}[afade_b];"
                    # Mix faded audios together
                    f"[afade_a][afade_b]amix=inputs=2:duration=first[amixed];"
                    # Mix with silence to ensure duration, then trim to exact length
                    # atrim prevents AAC frame padding from extending audio past video
                    f"[silence][amixed]amix=inputs=2:duration=first:weights='0 1',atrim=0:{fade_dur},asetpts=PTS-STARTPTS[aout]"
                )

                cmd_fallback = [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(seg_a),
                    "-i",
                    str(seg_b),
                    "-filter_complex",
                    filter_complex_fallback,
                    "-map",
                    "[vout]",
                    "-map",
                    "[aout]",
                    *video_codec_args,
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-movflags",
                    "+faststart",
                    str(output_path),
                ]

                result = subprocess.run(cmd_fallback, capture_output=True, text=True)
                if result.returncode != 0:
                    # Last resort: video xfade with silent audio
                    logger.warning(
                        f"amix fallback also failed, using silent audio: {result.stderr[-200:]}"
                    )

                    filter_complex_silent = (
                        # Scale both videos to same resolution
                        f"[0:v]{scale_filter}[v0scaled];"
                        f"[1:v]{scale_filter}[v1scaled];"
                        # Video trim + setpts after xfade ensures exact duration and clean timestamps
                        f"[v0scaled][v1scaled]xfade=transition=fade:duration={fade_dur}:offset=0,trim=0:{fade_dur},setpts=PTS-STARTPTS[vout];"
                        # atrim and asetpts ensure exact duration and reset timestamps
                        f"anullsrc=r=48000:cl=stereo,atrim=0:{fade_dur},{audio_format},asetpts=PTS-STARTPTS[aout]"
                    )

                    cmd_silent = [
                        "ffmpeg",
                        "-y",
                        "-i",
                        str(seg_a),
                        "-i",
                        str(seg_b),
                        "-filter_complex",
                        filter_complex_silent,
                        "-map",
                        "[vout]",
                        "-map",
                        "[aout]",
                        *video_codec_args,
                        "-c:a",
                        "aac",
                        "-b:a",
                        "128k",
                        "-t",
                        str(fade_dur),
                        "-movflags",
                        "+faststart",
                        str(output_path),
                    ]

                    result = subprocess.run(cmd_silent, capture_output=True, text=True)
                    if result.returncode != 0:
                        raise RuntimeError(f"Failed to render transition: {result.stderr[-500:]}")

        finally:
            # Cleanup temp files
            if not self.settings.debug_preserve_intermediates:
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _concat_with_inline_trim(
        self,
        encoded_clips: list[Path],
        clip_durations: list[float],
        transitions: list[str],
        transition_segments: dict[int, Path],
        fade: float,
        output_path: Path,
    ) -> None:
        """Concatenate clips and transitions with inline trimming.

        Instead of separately re-encoding trimmed clips (which creates frame
        mismatches at boundaries), this method trims clips during decode in
        the concat filter. Transition segments are passed through as-is.

        The concat filter decodes all inputs and re-encodes once, producing
        a single clean output with consistent HEVC headers.

        Args:
            encoded_clips: Pre-encoded clip paths.
            clip_durations: Duration of each clip.
            transitions: "fade" or "cut" for each boundary.
            transition_segments: Map of boundary index to transition path.
            fade: Fade duration in seconds.
            output_path: Output path.
        """
        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"

        # Build the list of inputs and corresponding filter operations.
        # Each encoded clip may need trimming; transition segments are used as-is.
        input_files: list[Path] = []
        filter_parts: list[str] = []
        concat_labels_v: list[str] = []
        concat_labels_a: list[str] = []
        input_idx = 0

        for i, encoded_clip in enumerate(encoded_clips):
            clip_dur = clip_durations[i]
            is_first = i == 0
            is_last = i == len(encoded_clips) - 1

            prev_is_fade = not is_first and transitions[i - 1] == "fade"
            next_is_fade = not is_last and transitions[i] == "fade"

            trim_start = fade if prev_is_fade else 0
            trim_end = clip_dur - fade if next_is_fade else clip_dur
            trim_dur = trim_end - trim_start

            # Add clip as input
            input_files.append(encoded_clip)
            idx = input_idx
            input_idx += 1

            # Trim video and audio inline during decode
            if trim_start > 0 or trim_end < clip_dur:
                filter_parts.append(
                    f"[{idx}:v]trim=start={trim_start}:duration={trim_dur},"
                    f"setpts=PTS-STARTPTS[v{idx}]"
                )
                has_audio = self._has_audio_stream(encoded_clip)
                if has_audio:
                    filter_parts.append(
                        f"[{idx}:a]atrim=start={trim_start}:duration={trim_dur},"
                        f"{audio_format},asetpts=PTS-STARTPTS[a{idx}]"
                    )
                else:
                    filter_parts.append(
                        f"anullsrc=r=48000:cl=stereo,"
                        f"atrim=0:{trim_dur},{audio_format},"
                        f"asetpts=PTS-STARTPTS[a{idx}]"
                    )
            else:
                filter_parts.append(f"[{idx}:v]setpts=PTS-STARTPTS[v{idx}]")
                has_audio = self._has_audio_stream(encoded_clip)
                if has_audio:
                    filter_parts.append(f"[{idx}:a]{audio_format},asetpts=PTS-STARTPTS[a{idx}]")
                else:
                    filter_parts.append(
                        f"anullsrc=r=48000:cl=stereo,"
                        f"atrim=0:{clip_dur},{audio_format},"
                        f"asetpts=PTS-STARTPTS[a{idx}]"
                    )

            concat_labels_v.append(f"[v{idx}]")
            concat_labels_a.append(f"[a{idx}]")

            # Add transition segment if present
            if i in transition_segments:
                input_files.append(transition_segments[i])
                t_idx = input_idx
                input_idx += 1

                filter_parts.append(f"[{t_idx}:v]setpts=PTS-STARTPTS[v{t_idx}]")
                filter_parts.append(f"[{t_idx}:a]{audio_format},asetpts=PTS-STARTPTS[a{t_idx}]")

                concat_labels_v.append(f"[v{t_idx}]")
                concat_labels_a.append(f"[a{t_idx}]")

        # Build concat filter
        n_segments = len(concat_labels_v)
        concat_input = "".join(
            f"{concat_labels_v[i]}{concat_labels_a[i]}" for i in range(n_segments)
        )
        filter_parts.append(f"{concat_input}concat=n={n_segments}:v=1:a=1[vout][aout]")

        filter_complex = ";".join(filter_parts)

        # Build inputs
        inputs: list[str] = []
        for f in input_files:
            inputs.extend(["-i", str(f)])

        video_codec_args = _get_gpu_encoder_args(
            crf=self.settings.output_crf,
            preserve_hdr=self.settings.preserve_hdr,
        )

        cmd = [
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            *video_codec_args,
            "-r",
            "60",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-max_muxing_queue_size",
            "4096",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        logger.info(
            f"Concat with inline trim: {len(encoded_clips)} clips + "
            f"{len(transition_segments)} transitions = {n_segments} segments"
        )
        logger.debug(f"Filter ({len(filter_complex)} chars): {filter_complex[:300]}...")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
        if result.returncode != 0:
            raise RuntimeError(f"Concat with inline trim failed: {result.stderr[-500:]}")

    def _concat_with_copy(self, segments: list[Path], output_path: Path) -> None:
        """Concatenate video segments using the concat filter (decode + re-encode).

        Uses FFmpeg's concat filter (-filter_complex) which fully decodes all
        inputs before concatenating and re-encoding. This guarantees a single
        consistent HEVC SPS/PPS header throughout the output, eliminating the
        garbled frames that occur when the concat demuxer pastes together
        segments with different encoder parameters.

        Slower than stream copy but produces clean, artifact-free output.

        Args:
            segments: List of segment paths to concatenate.
            output_path: Output path for concatenated video.
        """
        n = len(segments)
        if n == 0:
            raise ValueError("No segments to concatenate")

        if n == 1:
            import shutil

            shutil.copy2(segments[0], output_path)
            return

        # Build inputs
        inputs: list[str] = []
        for seg in segments:
            inputs.extend(["-i", str(seg)])

        # Build concat filter: [0:v][0:a][1:v][1:a]...concat=n=N:v=1:a=1
        filter_parts = []
        for i in range(n):
            filter_parts.append(f"[{i}:v]")
            filter_parts.append(f"[{i}:a]")
        filter_parts.append(f"concat=n={n}:v=1:a=1[vout][aout]")
        filter_complex = "".join(filter_parts)

        # Get encoder args matching the rest of the pipeline
        video_codec_args = _get_gpu_encoder_args(
            crf=self.settings.output_crf,
            preserve_hdr=self.settings.preserve_hdr,
        )

        cmd = [
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            *video_codec_args,
            "-r",
            "60",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        logger.info(f"Concat filter: {n} segments → {output_path.name}")
        logger.debug(f"Concat command: {' '.join(cmd)}")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to concatenate: {result.stderr[-500:]}")

    def _assemble_xfade_chain(
        self,
        encoded_clips: list[Path],
        clip_durations: list[float],
        transitions: list[str],
        fade: float,
        output_path: Path,
    ) -> None:
        """Assemble pre-encoded clips using a single xfade filter chain.

        All clips are already normalized (same resolution, fps, pixel format).
        This method builds a single FFmpeg filter_complex that chains xfade
        operations, ensuring frame-perfect transitions with no decode/re-encode
        mismatches at boundaries.

        For cuts, clips are concatenated directly via the concat filter.
        For fades, xfade is used with the overlap handled inline.

        Args:
            encoded_clips: Pre-encoded clip paths.
            clip_durations: Duration of each clip in seconds.
            transitions: "fade" or "cut" for each boundary.
            fade: Fade duration in seconds.
            output_path: Output path.
        """
        n = len(encoded_clips)
        if n == 1:
            import shutil

            shutil.copy2(encoded_clips[0], output_path)
            return

        # Build inputs
        inputs: list[str] = []
        for clip in encoded_clips:
            inputs.extend(["-i", str(clip)])

        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        filter_parts = []

        # Prepare audio streams (normalize format, ensure exact duration)
        for i in range(n):
            has_audio = self._has_audio_stream(encoded_clips[i])
            if has_audio:
                filter_parts.append(f"[{i}:a]{audio_format},asetpts=PTS-STARTPTS[a{i}]")
            else:
                filter_parts.append(
                    f"anullsrc=r=48000:cl=stereo,atrim=0:{clip_durations[i]},{audio_format}[a{i}]"
                )

        # Build xfade chain for video, acrossfade chain for audio
        current_video = f"[{0}:v]"
        current_audio = f"[a{0}]"
        # Track cumulative offset for xfade timing
        # offset = sum of clip durations up to current point minus accumulated fade overlaps
        cumulative_duration = clip_durations[0]

        for i in range(n - 1):
            next_idx = i + 1
            next_video = f"[{next_idx}:v]"
            next_audio = f"[a{next_idx}]"

            if transitions[i] == "fade":
                # xfade offset = cumulative duration minus fade duration
                offset = cumulative_duration - fade
                v_out = f"[vx{i}]"
                a_out = f"[ax{i}]"

                filter_parts.append(
                    f"{current_video}{next_video}xfade=transition=fade:"
                    f"duration={fade}:offset={offset}{v_out}"
                )
                filter_parts.append(
                    f"{current_audio}{next_audio}acrossfade=d={fade}:c1=tri:c2=tri{a_out}"
                )

                current_video = v_out
                current_audio = a_out
                # Next clip's content starts after the overlap
                cumulative_duration = offset + clip_durations[next_idx]
            else:
                # Cut transition: concat the two segments
                # Use a simple concat for this pair
                v_out = f"[vc{i}]"
                a_out = f"[ac{i}]"

                filter_parts.append(f"{current_video}{next_video}concat=n=2:v=1:a=0{v_out}")
                filter_parts.append(f"{current_audio}{next_audio}concat=n=2:v=0:a=1{a_out}")

                current_video = v_out
                current_audio = a_out
                cumulative_duration += clip_durations[next_idx]

        filter_complex = ";".join(filter_parts)

        video_codec_args = _get_gpu_encoder_args(
            crf=self.settings.output_crf,
            preserve_hdr=self.settings.preserve_hdr,
        )

        cmd = [
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            current_video,
            "-map",
            current_audio,
            *video_codec_args,
            "-r",
            "60",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        logger.info(f"Xfade assembly: {n} clips, filter length: {len(filter_complex)}")
        logger.debug(f"Filter: {filter_complex[:500]}...")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"Xfade assembly failed: {result.stderr[-500:]}")

        logger.info(f"Assembly complete: {output_path}")

    def _get_transition_types(self, clips: list[AssemblyClip]) -> list[str]:
        """Get the transition type for each clip boundary.

        Args:
            clips: List of clips.

        Returns:
            List of "fade" or "cut" for each boundary (len = len(clips) - 1).
        """
        # Use predecided transitions if available
        if self.settings.predecided_transitions:
            return self.settings.predecided_transitions

        # Otherwise determine based on settings
        transitions = []
        for i in range(len(clips) - 1):
            clip = clips[i]
            next_clip = clips[i + 1]

            # Title screens always use fade
            if (
                clip.is_title_screen
                or next_clip.is_title_screen
                or self.settings.transition == TransitionType.CROSSFADE
            ):
                transitions.append("fade")
            elif self.settings.transition == TransitionType.CUT:
                transitions.append("cut")
            elif self.settings.transition == TransitionType.SMART:
                # Use outgoing_transition if set, otherwise default to fade
                trans = getattr(clip, "outgoing_transition", None) or "fade"
                transitions.append(trans)
            else:
                transitions.append("cut")

        return transitions

    def _add_music(self, video_path: Path, output_path: Path) -> Path:
        """Add background music to video with intelligent ducking.

        Uses stem-based ducking when stems are available (from MusicGen/Demucs):
        - 4-stem mode: drums duck 50%, bass 60%, melody 75%, other 70%
        - 2-stem mode: vocals/melody ducked, accompaniment at full volume

        Falls back to simple amix when no stems available.

        Args:
            video_path: Input video path.
            output_path: Output path.

        Returns:
            Path to output video.
        """
        # Check for 4-stem mode first (more granular control)
        drums = self.settings.music_drums_path
        bass = self.settings.music_bass_path
        vocals = self.settings.music_vocals_path
        other = self.settings.music_other_path

        if (
            drums
            and drums.exists()
            and bass
            and bass.exists()
            and vocals
            and vocals.exists()
            and other
            and other.exists()
        ):
            # Use 4-stem ducking for granular control
            return self._add_music_with_4stems(video_path, output_path, drums, bass, vocals, other)

        # Check for 2-stem mode
        accompaniment = self.settings.music_accompaniment_path
        if vocals and vocals.exists() and accompaniment and accompaniment.exists():
            # Use 2-stem ducking
            return self._add_music_with_stems(video_path, output_path, vocals, accompaniment)

        # Fallback to simple mixing
        return self._add_music_simple(video_path, output_path)

    def _add_music_with_stems(
        self,
        video_path: Path,
        output_path: Path,
        vocals_path: Path,
        accompaniment_path: Path,
    ) -> Path:
        """Add music with stem-based ducking (ducks vocals during speech).

        Args:
            video_path: Input video path.
            output_path: Output path.
            vocals_path: Path to vocals/melody stem.
            accompaniment_path: Path to drums+bass stem.

        Returns:
            Path to output video.
        """
        from immich_memories.audio.mixer import (
            DuckingConfig,
            MixConfig,
            mix_audio_with_stem_ducking,
        )

        logger.info("Using stem-based audio ducking (vocals duck during speech, drums stay full)")

        # Convert volume (0.0-1.0) to dB
        # 0.3 volume ≈ -10dB, 0.5 ≈ -6dB, 1.0 = 0dB
        import math

        volume_db = 20 * math.log10(max(0.01, self.settings.music_volume))

        config = MixConfig(
            ducking=DuckingConfig(
                music_volume_db=volume_db,
                threshold=0.02,  # Sensitive to speech
                ratio=6.0,  # Strong ducking
                attack_ms=50.0,  # Fast attack
                release_ms=500.0,  # Smooth release
            ),
            fade_in_seconds=2.0,
            fade_out_seconds=3.0,
        )

        try:
            return mix_audio_with_stem_ducking(
                video_path=video_path,
                vocals_path=vocals_path,
                accompaniment_path=accompaniment_path,
                output_path=output_path,
                config=config,
                duck_vocals_db=-12.0,  # Duck vocals 12dB during speech
            )
        except Exception as e:
            logger.warning(f"Stem-based mixing failed, falling back to simple mix: {e}")
            return self._add_music_simple(video_path, output_path)

    def _add_music_with_4stems(
        self,
        video_path: Path,
        output_path: Path,
        drums_path: Path,
        bass_path: Path,
        vocals_path: Path,
        other_path: Path,
    ) -> Path:
        """Add music with 4-stem ducking (granular control per instrument).

        Ducking levels during speech:
        - Drums: -3dB (~50% reduction) - keeps energy
        - Bass: -6dB (~60% reduction)
        - Vocals/melody: -12dB (~75% reduction) - avoids competing with speech
        - Other: -9dB (~70% reduction)

        Args:
            video_path: Input video path.
            output_path: Output path.
            drums_path: Path to drums stem.
            bass_path: Path to bass stem.
            vocals_path: Path to vocals/melody stem.
            other_path: Path to other instruments stem.

        Returns:
            Path to output video.
        """
        from immich_memories.audio.mixer import (
            DuckingConfig,
            MixConfig,
            StemDuckingLevels,
            mix_audio_with_4stem_ducking,
        )

        logger.info("Using 4-stem audio ducking (drums 50%, bass 60%, melody 75%, other 70%)")

        # Convert volume (0.0-1.0) to dB
        import math

        volume_db = 20 * math.log10(max(0.01, self.settings.music_volume))

        config = MixConfig(
            ducking=DuckingConfig(
                music_volume_db=volume_db,
                threshold=0.02,  # Sensitive to speech
                ratio=6.0,  # Strong ducking
                attack_ms=50.0,  # Fast attack
                release_ms=500.0,  # Smooth release
            ),
            fade_in_seconds=2.0,
            fade_out_seconds=3.0,
        )

        # Custom ducking levels per stem
        ducking_levels = StemDuckingLevels(
            drums_db=-3.0,  # ~50% reduction
            bass_db=-6.0,  # ~60% reduction
            vocals_db=-12.0,  # ~75% reduction
            other_db=-9.0,  # ~70% reduction
        )

        try:
            return mix_audio_with_4stem_ducking(
                video_path=video_path,
                drums_path=drums_path,
                bass_path=bass_path,
                vocals_path=vocals_path,
                other_path=other_path,
                output_path=output_path,
                config=config,
                ducking_levels=ducking_levels,
            )
        except Exception as e:
            logger.warning(f"4-stem mixing failed, falling back to simple mix: {e}")
            return self._add_music_simple(video_path, output_path)

    def _add_music_simple(self, video_path: Path, output_path: Path) -> Path:
        """Add music with simple volume mixing (no ducking).

        Args:
            video_path: Input video path.
            output_path: Output path.

        Returns:
            Path to output video.
        """
        if video_path == output_path:
            temp_output = output_path.with_suffix(".temp.mp4")
        else:
            temp_output = output_path

        music_vol = self.settings.music_volume

        # Mix original audio with music
        filter_complex = (
            f"[1:a]volume={music_vol}[music];[0:a][music]amix=inputs=2:duration=first[aout]"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(self.settings.music_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(temp_output),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            logger.warning(f"Failed to add music: {result.stderr}")
            return video_path

        if temp_output != output_path:
            import shutil

            shutil.move(temp_output, output_path)

        return output_path

    def _add_music_to_clip(self, clip_path: Path, output_path: Path) -> Path:
        """Add music to a single clip.

        Args:
            clip_path: Input clip path.
            output_path: Output path.

        Returns:
            Path to output video.
        """
        return self._add_music(clip_path, output_path)

    def _detect_framerate(self, video_path: Path) -> float | None:
        """Detect frame rate of a video file.

        Args:
            video_path: Path to video file.

        Returns:
            Frame rate in fps, or None if detection fails.
        """
        try:
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=r_frame_rate",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                # Parse fraction like "60/1" or "30000/1001"
                fps_str = result.stdout.strip()
                if "/" in fps_str:
                    num, den = fps_str.split("/")
                    return float(num) / float(den)
                return float(fps_str)
        except Exception as e:
            logger.debug(f"Failed to detect frame rate: {e}")
        return None

    def _detect_max_framerate(self, clips: list[AssemblyClip]) -> int:
        """Detect the maximum frame rate from a list of clips.

        Args:
            clips: List of clips to analyze.

        Returns:
            Maximum frame rate (rounded to nearest common value), default 30.
        """
        max_fps = 30.0
        for clip in clips[:20]:  # Sample first 20 clips for speed
            fps = self._detect_framerate(clip.path)
            if fps and fps > max_fps:
                max_fps = fps

        # Round to nearest common frame rate
        if max_fps >= 55:
            return 60
        elif max_fps >= 45:
            return 50
        elif max_fps >= 25:
            return 30
        else:
            return 24

    def estimate_duration(self, clips: list[AssemblyClip]) -> float:
        """Estimate final video duration.

        Args:
            clips: List of clips.

        Returns:
            Estimated duration in seconds.
        """
        if not clips:
            return 0.0

        total = sum(clip.duration for clip in clips)

        # Subtract transition overlaps
        if self.settings.transition == TransitionType.CROSSFADE and len(clips) > 1:
            overlap = self.settings.transition_duration * (len(clips) - 1)
            total -= overlap

        return max(0, total)

    def _parse_clip_date(self, clip: AssemblyClip) -> date | None:
        """Parse the date from an AssemblyClip.

        Args:
            clip: The clip to extract date from.

        Returns:
            Date object or None if not available.
        """
        if not clip.date:
            return None

        try:
            # Try common date formats (ISO first, then human-readable)
            for fmt in [
                "%Y-%m-%d",  # ISO format (preferred)
                "%Y-%m-%dT%H:%M:%S",  # ISO with time
                "%Y-%m-%d %H:%M:%S",  # ISO with time (space)
                "%B %d, %Y",  # Human-readable (e.g., "October 15, 2025")
                "%b %d, %Y",  # Short month (e.g., "Oct 15, 2025")
            ]:
                try:
                    return datetime.strptime(clip.date, fmt).date()
                except ValueError:
                    continue
            # Fallback: try parsing just the first 10 chars as date
            return datetime.strptime(clip.date[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            logger.debug(f"Could not parse date: {clip.date}")
            return None

    def _detect_month_changes(
        self,
        clips: list[AssemblyClip],
    ) -> list[tuple[int, int, int]]:
        """Detect where month changes occur in the clip list.

        Args:
            clips: List of clips with dates.

        Returns:
            List of (insert_index, month, year) for each month change.
        """
        month_changes: list[tuple[int, int, int]] = []
        current_month: tuple[int, int] | None = None
        month_clip_counts: dict[tuple[int, int], int] = {}

        # Count clips per month and detect changes (including first month)
        clips_with_dates = 0
        for i, clip in enumerate(clips):
            clip_date = self._parse_clip_date(clip)
            if clip_date is None:
                continue

            clips_with_dates += 1
            month_key = (clip_date.year, clip_date.month)

            # Count clips in this month
            month_clip_counts[month_key] = month_clip_counts.get(month_key, 0) + 1

            # Detect month change OR first month
            if current_month is None or month_key != current_month:
                month_changes.append((i, clip_date.month, clip_date.year))
                if current_month is None:
                    logger.info(f"First month detected at clip {i}: {month_key}")
                else:
                    logger.info(
                        f"Month change detected at clip {i}: {current_month} -> {month_key}"
                    )

            current_month = month_key

        logger.info(
            f"Month detection: {clips_with_dates}/{len(clips)} clips have dates, {len(month_changes)} month changes found"
        )
        if month_clip_counts:
            logger.info(f"Clips per month: {month_clip_counts}")

        return month_changes

    def _get_orientation_from_clips(self, clips: list[AssemblyClip]) -> str:
        """Detect video orientation from clips.

        Args:
            clips: List of clips.

        Returns:
            Orientation string: "landscape", "portrait", or "square".
        """
        portrait_count = 0
        landscape_count = 0

        for clip in clips[:10]:  # Sample first 10 clips
            res = self._get_video_resolution(clip.path)
            if res:
                w, h = res
                if h > w:
                    portrait_count += 1
                elif w > h:
                    landscape_count += 1

        if portrait_count > landscape_count:
            return "portrait"
        elif landscape_count > portrait_count:
            return "landscape"
        return "landscape"  # Default

    def _get_resolution_tier(self, clips: list[AssemblyClip]) -> str:
        """Detect resolution tier from clips.

        Args:
            clips: List of clips.

        Returns:
            Resolution tier: "720p", "1080p", or "4k".
        """
        max_height = 0

        for clip in clips[:10]:  # Sample first 10 clips
            res = self._get_video_resolution(clip.path)
            if res:
                max_height = max(max_height, max(res))

        if max_height >= 2160:
            return "4k"
        elif max_height >= 1080:
            return "1080p"
        return "720p"

    def assemble_with_titles(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble clips with title screens, month dividers, and ending screen.

        This is the main entry point for full video assembly with all visual
        enhancements. It generates and integrates:
        - Opening title screen
        - Month divider screens (when month changes)
        - Ending screen with color fade

        Args:
            clips: List of clips to assemble.
            output_path: Path for output video.
            progress_callback: Progress callback (0.0 to 1.0).

        Returns:
            Path to assembled video.
        """
        if not clips:
            raise ValueError("No clips provided")

        title_settings = self.settings.title_screens
        if title_settings is None or not title_settings.enabled:
            # No title screens - use regular assembly
            return self.assemble(clips, output_path, progress_callback)

        # Import title screen module (lazy import to avoid circular deps)
        try:
            from immich_memories.titles import (
                TitleScreenConfig,
                TitleScreenGenerator,
            )
        except ImportError as e:
            logger.warning(f"Title screens not available: {e}")
            return self.assemble(clips, output_path, progress_callback)

        # Detect orientation and resolution from clips
        orientation = self._get_orientation_from_clips(clips)
        resolution_tier = self._get_resolution_tier(clips)

        logger.info(f"Generating title screens ({orientation}, {resolution_tier})")

        # Create title screen config
        title_config = TitleScreenConfig(
            enabled=True,
            title_duration=title_settings.title_duration,
            month_divider_duration=title_settings.month_divider_duration,
            ending_duration=title_settings.ending_duration,
            locale=title_settings.locale,
            style_mode=title_settings.style_mode,
            show_month_dividers=title_settings.show_month_dividers,
            month_divider_threshold=title_settings.month_divider_threshold,
            orientation=orientation,
            resolution=resolution_tier,
        )

        # Create output directory for title screens
        title_output_dir = output_path.parent / ".title_screens"
        title_output_dir.mkdir(parents=True, exist_ok=True)

        # Determine mood: use explicit setting, or auto-detect from clips
        mood = title_settings.mood
        if mood is None:
            # Auto-detect dominant mood from clip emotions
            mood = aggregate_mood_from_clips(clips)
            if mood:
                logger.info(f"Auto-detected mood from clips: {mood}")
            else:
                logger.info("No mood detected from clips, using default style")

        # Create generator
        generator = TitleScreenGenerator(
            config=title_config,
            mood=mood,
            output_dir=title_output_dir,
        )

        # Build the final clip list with title screens
        final_clips: list[AssemblyClip] = []

        # 1. Generate and add opening title screen
        if progress_callback:
            progress_callback(0.0, "Generating title screen...")

        title_screen = generator.generate_title_screen(
            year=title_settings.year,
            month=title_settings.month,
            start_date=title_settings.start_date,
            end_date=title_settings.end_date,
            person_name=title_settings.person_name,
            birthday_age=title_settings.birthday_age,
        )
        final_clips.append(
            AssemblyClip(
                path=title_screen.path,
                duration=title_screen.duration,
                date=None,
                asset_id="title_screen",
                is_title_screen=True,  # Ensures fade transition
            )
        )
        logger.info(f"Generated title screen: {title_screen.path}")

        # 2. Detect month changes and insert dividers
        month_changes = self._detect_month_changes(clips)
        month_divider_paths: dict[tuple[int, int], Path] = {}

        if title_settings.show_month_dividers and month_changes:
            if progress_callback:
                progress_callback(0.05, "Generating month dividers...")

            for _, month, year in month_changes:
                key = (year, month)
                if key not in month_divider_paths:
                    # Check if this is the birthday month
                    is_birthday = (
                        title_settings.birthday_month is not None
                        and month == title_settings.birthday_month
                    )
                    divider = generator.generate_month_divider(
                        month, year, is_birthday_month=is_birthday
                    )
                    month_divider_paths[key] = divider.path
                    logger.info(
                        f"Generated month divider: {month}/{year}"
                        + (" (birthday!)" if is_birthday else "")
                    )

        # 3. Build clip list with month dividers inserted
        current_month: tuple[int, int] | None = None

        for clip in clips:
            clip_date = self._parse_clip_date(clip)

            if clip_date:
                month_key = (clip_date.year, clip_date.month)

                # Insert month divider if:
                # - First month (current_month is None) OR
                # - Month changed from previous clip
                if (
                    title_settings.show_month_dividers
                    and (current_month is None or month_key != current_month)
                    and month_key in month_divider_paths
                ):
                    divider_path = month_divider_paths[month_key]
                    final_clips.append(
                        AssemblyClip(
                            path=divider_path,
                            duration=title_settings.month_divider_duration,
                            date=None,
                            asset_id=f"month_divider_{month_key[1]:02d}",
                            is_title_screen=True,  # Ensures fade transition
                        )
                    )

                current_month = month_key

            final_clips.append(clip)

        # 4. Generate and add ending screen
        if title_settings.show_ending_screen:
            if progress_callback:
                progress_callback(0.1, "Generating ending screen...")

            # Extract video paths for dominant color extraction
            video_paths = [clip.path for clip in clips]
            ending_screen = generator.generate_ending_screen(video_clips=video_paths)
            final_clips.append(
                AssemblyClip(
                    path=ending_screen.path,
                    duration=ending_screen.duration,
                    date=None,
                    asset_id="ending_screen",
                    is_title_screen=True,  # Ensures fade transition
                )
            )
            logger.info(f"Generated ending screen: {ending_screen.path}")

        # 5. Assemble everything
        if progress_callback:
            progress_callback(0.15, "Assembling video...")

        logger.info(f"Assembling {len(final_clips)} clips (including title screens)")

        # Title screens ALWAYS need fade transitions (no cuts)
        # If CUT mode is selected, upgrade to SMART which respects is_title_screen flag
        original_transition = self.settings.transition
        if self.settings.transition == TransitionType.CUT:
            logger.info("Upgrading CUT to SMART transitions (title screens require fades)")
            self.settings.transition = TransitionType.SMART

        try:
            return self.assemble(final_clips, output_path, progress_callback)
        finally:
            # Restore original setting
            self.settings.transition = original_transition


def assemble_montage(
    clips: list[Path],
    output_path: Path,
    transition: TransitionType = TransitionType.CROSSFADE,
    transition_duration: float = 0.5,
    music_path: Path | None = None,
    music_volume: float = 0.3,
    music_vocals_path: Path | None = None,
    music_accompaniment_path: Path | None = None,
) -> Path:
    """Convenience function to assemble a video montage.

    Args:
        clips: List of clip paths.
        output_path: Output video path.
        transition: Transition type.
        transition_duration: Transition duration in seconds.
        music_path: Optional music file path.
        music_volume: Music volume (0-1).
        music_vocals_path: Optional vocals/melody stem for ducking.
        music_accompaniment_path: Optional drums+bass stem (stays full during speech).

    Returns:
        Path to assembled video.
    """
    from immich_memories.processing.clips import get_video_duration

    # Convert paths to AssemblyClips
    assembly_clips = []
    for path in clips:
        duration = get_video_duration(path)
        assembly_clips.append(
            AssemblyClip(
                path=path,
                duration=duration,
            )
        )

    settings = AssemblySettings(
        transition=transition,
        transition_duration=transition_duration,
        music_path=music_path,
        music_volume=music_volume,
        music_vocals_path=music_vocals_path,
        music_accompaniment_path=music_accompaniment_path,
    )

    assembler = VideoAssembler(settings)
    return assembler.assemble(assembly_clips, output_path)


def create_preview(
    clips: list[AssemblyClip],
    output_path: Path,
    preview_duration: float = 30.0,
) -> Path:
    """Create a quick preview of the assembly.

    Only includes the first N seconds.

    Args:
        clips: List of clips.
        output_path: Output path.
        preview_duration: Maximum preview duration.

    Returns:
        Path to preview video.
    """
    # Truncate clips to fit preview duration
    preview_clips = []
    remaining_duration = preview_duration

    for clip in clips:
        if remaining_duration <= 0:
            break

        if clip.duration <= remaining_duration:
            preview_clips.append(clip)
            remaining_duration -= clip.duration
        else:
            # Truncate this clip
            from immich_memories.processing.clips import extract_clip

            truncated_path = extract_clip(
                clip.path,
                start_time=0,
                end_time=remaining_duration,
            )
            preview_clips.append(
                AssemblyClip(
                    path=truncated_path,
                    duration=remaining_duration,
                )
            )
            break

    # Assemble preview with faster settings
    settings = AssemblySettings(
        transition=TransitionType.CUT,  # Faster
        output_crf=28,  # Lower quality for speed
    )

    assembler = VideoAssembler(settings)
    return assembler.assemble(preview_clips, output_path)
