"""Video downscaling for faster analysis.

Downscales videos to a lower resolution (e.g., 480p) before analysis to
dramatically speed up processing. Analysis at 480p is ~80x faster than 4K
while still providing sufficient detail for face detection and motion analysis.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from immich_memories.security import validate_video_path

logger = logging.getLogger(__name__)


def _get_fast_encoder_args() -> list[str]:
    """Get fast encoder arguments with GPU acceleration when available.

    Returns encoder optimized for speed (analysis temp files).
    """
    # macOS: Use VideoToolbox hardware encoder
    if sys.platform == "darwin":
        return [
            "-c:v",
            "h264_videotoolbox",
            "-q:v",
            "65",  # Lower quality OK for analysis (faster)
        ]

    # Other platforms: Check for available encoders
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        encoders = result.stdout

        # Try NVIDIA NVENC (GPU accelerated)
        if "h264_nvenc" in encoders:
            return [
                "-c:v",
                "h264_nvenc",
                "-preset",
                "p1",  # Fastest preset
                "-rc",
                "constqp",
                "-qp",
                "28",
            ]

        # Try VAAPI (Linux GPU)
        if "h264_vaapi" in encoders:
            return [
                "-c:v",
                "h264_vaapi",
                "-qp",
                "28",
            ]

        # Try Intel QSV
        if "h264_qsv" in encoders:
            return [
                "-c:v",
                "h264_qsv",
                "-preset",
                "veryfast",
            ]
    except (subprocess.SubprocessError, OSError, ValueError):
        pass

    # Fallback to CPU libx264
    return [
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "28",
    ]


DEFAULT_ANALYSIS_HEIGHT = 480  # 480p for analysis


def get_downscaled_path(original_path: Path, target_height: int = DEFAULT_ANALYSIS_HEIGHT) -> Path:
    """Stores in same directory with _{height}p suffix."""
    stem = original_path.stem
    suffix = original_path.suffix
    return original_path.parent / f"{stem}_{target_height}p{suffix}"


def get_video_height(video_path: Path) -> int:
    """Returns 0 if unable to determine."""
    try:
        validate_video_path(video_path, must_exist=True)
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=height",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return int(result.stdout.strip())
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        logger.debug(f"Could not get video height: {e}")
        return 0


def needs_downscaling(video_path: Path, target_height: int = DEFAULT_ANALYSIS_HEIGHT) -> bool:
    """Only downscales if video is significantly larger than target (1.5x)."""
    video_height = get_video_height(video_path)

    if video_height <= 0:
        # Can't determine height, assume no downscaling needed
        return False

    # Only downscale if video is significantly larger than target
    return video_height > target_height * 1.5


def downscale_video(
    source_path: Path,
    target_height: int = DEFAULT_ANALYSIS_HEIGHT,
    output_path: Path | None = None,
) -> Path:
    """Downscale video to target height for analysis.

    Uses ffmpeg with ultrafast preset for quick transcoding.
    Maintains aspect ratio. Audio is stripped since it's not needed for analysis.

    Args:
        source_path: Path to source video.
        target_height: Target height in pixels (width scales proportionally).
        output_path: Optional output path. If None, uses standard naming.

    Returns:
        Path to downscaled video, or original if no downscaling needed.
    """
    if output_path is None:
        output_path = get_downscaled_path(source_path, target_height)

    # Check if already exists
    if output_path.exists():
        logger.debug(f"Downscaled version already exists: {output_path}")
        return output_path

    # Check if source needs downscaling
    if not needs_downscaling(source_path, target_height):
        logger.debug(f"Video already small enough, using original: {source_path}")
        return source_path

    validate_video_path(source_path, must_exist=True)
    logger.info(f"Downscaling {source_path.name} to {target_height}p for faster analysis...")

    # ffmpeg command for fast downscaling with GPU acceleration
    # -vf scale=-2:{height} maintains aspect ratio and ensures even dimensions
    # -an strips audio (not needed for analysis)
    encoder_args = _get_fast_encoder_args()
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-vf",
        f"scale=-2:{target_height}",
        *encoder_args,
        "-movflags",
        "+faststart",
        "-an",  # No audio needed for analysis
        "-threads",
        "2",  # Limit CPU usage (for CPU fallback)
        "-loglevel",
        "error",
        str(output_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )

        if result.returncode != 0:
            logger.warning(f"Downscaling failed: {result.stderr}")
            # Return original if downscaling fails
            return source_path

        if output_path.exists():
            original_size = source_path.stat().st_size / (1024 * 1024)
            new_size = output_path.stat().st_size / (1024 * 1024)
            logger.info(
                f"Downscaled {source_path.name}: {original_size:.1f}MB -> {new_size:.1f}MB "
                f"({100 * new_size / original_size:.0f}% of original)"
            )
            return output_path

        logger.warning(f"Downscaled file not created: {output_path}")
        return source_path

    except subprocess.TimeoutExpired:
        logger.warning(f"Downscaling timed out for {source_path.name}")
        return source_path
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"Downscaling error: {e}")
        return source_path


def cleanup_downscaled(
    video_path: Path,
    target_height: int = DEFAULT_ANALYSIS_HEIGHT,
) -> None:
    """Remove downscaled version if it exists."""
    downscaled = get_downscaled_path(video_path, target_height)
    if downscaled.exists() and downscaled != video_path:
        try:
            downscaled.unlink()
            logger.debug(f"Cleaned up downscaled video: {downscaled}")
        except OSError as e:
            logger.warning(f"Failed to cleanup downscaled video: {e}")
