"""Video probing and metadata extraction utilities."""

from __future__ import annotations

import contextlib
import json
import logging
import subprocess
from itertools import starmap
from pathlib import Path

from immich_memories.security import validate_video_path

logger = logging.getLogger(__name__)


def get_video_duration(video_path: Path) -> float:
    validated = validate_video_path(video_path, must_exist=True)
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(validated),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    if result.returncode != 0:
        logger.error(f"FFprobe error: {result.stderr}")
        return 0.0

    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def get_main_video_stream_map(video_path: Path) -> str:
    """Find the main (highest-resolution) video stream in a file.

    iPhone Live Photo videos can embed a depth map as stream 0
    (512x512, 1fps, hevc). This function probes all video streams
    and returns the ffmpeg -map argument for the largest one.

    Returns:
        ffmpeg map string like "0:v:0" or "0:1" for use with -map.
    """
    validated = validate_video_path(video_path, must_exist=True)
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v",
        "-show_entries",
        "stream=index,width,height",
        "-of",
        "json",
        str(validated),
    ]
    with contextlib.suppress(Exception):
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            if len(streams) > 1:
                best = max(streams, key=lambda s: s.get("width", 0) * s.get("height", 0))
                idx = best.get("index", 0)
                logger.debug(
                    "Multi-stream file: selected stream %d (%dx%d)",
                    idx,
                    best.get("width", 0),
                    best.get("height", 0),
                )
                return f"0:{idx}"
    return "0:v:0"


def get_video_info(video_path: Path) -> dict:
    """Probes all video streams and picks the highest-resolution one
    (avoids iPhone depth maps in Live Photo videos).
    """
    validated = validate_video_path(video_path, must_exist=True)
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v",
        "-show_entries",
        "stream=index,width,height,r_frame_rate,codec_name,bit_rate,color_space,color_transfer,color_primaries,bits_per_raw_sample",
        "-show_entries",
        "format=duration,size,bit_rate",
        "-of",
        "json",
        str(validated),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    if result.returncode != 0:
        logger.error(f"FFprobe error: {result.stderr}")
        return {}

    try:
        data = json.loads(result.stdout)
        return _parse_probe_streams(data)
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.error(f"Failed to parse video info: {e}")
        return {}


def _validate_url(url: str) -> str:
    """Only allows http/https, rejects shell metacharacters."""
    from urllib.parse import urlparse

    # Check for null bytes
    if "\x00" in url:
        raise ValueError("URL contains null bytes")

    # Parse and validate URL structure
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid URL format: {e}") from e

    # Only allow http/https schemes
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Invalid URL scheme: {parsed.scheme}")

    # Ensure the URL has a valid hostname
    if not parsed.netloc:
        raise ValueError("URL missing hostname")

    # Check for suspicious characters that might indicate injection attempts
    # Shell metacharacters that could cause issues even without shell=True
    suspicious_chars = [";", "|", "&", "$", "`", "\n", "\r"]
    for char in suspicious_chars:
        if char in url:
            raise ValueError(f"URL contains suspicious character: {char!r}")

    return url


def _validate_header(key: str, value: str) -> tuple[str, str]:
    """Rejects header injection (newlines, null bytes, excessive length)."""
    import re

    # Check for null bytes in key first (security critical)
    if "\x00" in key:
        raise ValueError(f"Header key contains null bytes: {key}")

    # Validate header key - only allow alphanumeric, dash, underscore
    if not re.match(r"^[a-zA-Z0-9_-]+$", key):
        raise ValueError(f"Header key contains invalid characters: {key}")

    # Check for null bytes in value
    if "\x00" in value:
        raise ValueError(f"Header {key} value contains null bytes")

    # Check for newlines that could cause header injection
    if "\n" in value or "\r" in value:
        raise ValueError(f"Header {key} value contains newline characters")

    # Limit header value length to prevent abuse
    max_length = 4096
    if len(value) > max_length:
        raise ValueError(f"Header {key} value exceeds maximum length of {max_length}")

    return key, value


def _parse_probe_streams(data: dict) -> dict:
    """Parse ffprobe JSON output into a normalized metadata dictionary.

    Picks the highest-resolution video stream to avoid iPhone depth maps
    (512x512 HEVC at 1fps) that appear as the first stream in some Live Photos.

    Args:
        data: Parsed JSON output from ffprobe.

    Returns:
        Dictionary with normalized video metadata.
    """
    streams = data.get("streams", [{}])
    # Pick the stream with the highest resolution (avoids depth maps)
    stream: dict = max(
        streams,
        key=lambda s: s.get("width", 0) * s.get("height", 0),
        default={},
    )
    fmt = data.get("format", {})

    fps = _parse_frame_rate(stream)
    bit_depth = _parse_bit_depth(stream)
    rotation = _parse_rotation(stream)

    return {
        "width": stream.get("width", 0),
        "height": stream.get("height", 0),
        "fps": fps,
        "codec": stream.get("codec_name", ""),
        "bitrate": int(fmt.get("bit_rate", 0)) if fmt.get("bit_rate") else 0,
        "duration": float(fmt.get("duration", 0)) if fmt.get("duration") else 0,
        "size": int(fmt.get("size", 0)) if fmt.get("size") else 0,
        # HDR metadata
        "color_space": stream.get("color_space"),
        "color_transfer": stream.get("color_transfer"),
        "color_primaries": stream.get("color_primaries"),
        "bit_depth": bit_depth,
        # Rotation metadata
        "rotation": rotation,
        # Stream index — used to select the right stream in ffmpeg commands
        # (important for Live Photos where depth map may be stream 0)
        "video_stream_index": stream.get("index", 0),
    }


def _parse_frame_rate(stream: dict) -> float:
    if "r_frame_rate" not in stream:
        return 0.0
    parts = stream["r_frame_rate"].split("/")
    if len(parts) == 2 and int(parts[1]) > 0:
        return int(parts[0]) / int(parts[1])
    return 0.0


def _parse_bit_depth(stream: dict) -> int | None:
    if "bits_per_raw_sample" not in stream:
        return None
    try:
        return int(stream["bits_per_raw_sample"])
    except (ValueError, TypeError):
        return None


def _parse_rotation(stream: dict) -> int:
    for side_data in stream.get("side_data_list", []):
        if "rotation" in side_data:
            with contextlib.suppress(ValueError, TypeError):
                return abs(int(side_data["rotation"]))
            break
    return 0


def probe_video_url(url: str, headers: dict[str, str] | None = None) -> dict:
    """Probe video metadata from a URL without downloading the full file."""
    # Validate URL (security: prevent injection attacks)
    try:
        validated_url = _validate_url(url)
    except ValueError as e:
        logger.error(f"Invalid URL for ffprobe: {e}")
        return {}

    # Build ffprobe command for URL — probe ALL video streams so we can pick
    # the highest-resolution one (iPhone Live Photos have depth maps as stream 0)
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v",
        "-show_entries",
        "stream=index,width,height,r_frame_rate,codec_name,bit_rate,color_space,color_transfer,color_primaries,bits_per_raw_sample:stream_side_data=rotation",
        "-show_entries",
        "format=duration,size,bit_rate",
        "-of",
        "json",
    ]

    # Add headers if provided (with validation)
    if headers:
        try:
            validated_headers = list(starmap(_validate_header, headers.items()))
            header_str = "\r\n".join(f"{k}: {v}" for k, v in validated_headers)
            cmd.extend(["-headers", header_str])
        except ValueError as e:
            logger.error(f"Invalid header for ffprobe: {e}")
            return {}

    cmd.append(validated_url)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            stderr_preview = result.stderr[:200] if result.stderr else "No stderr"
            logger.debug(f"FFprobe stderr: {stderr_preview}")
            logger.error(f"FFprobe failed to probe video URL (exit code {result.returncode})")
            return {}

        data = json.loads(result.stdout)
        return _parse_probe_streams(data)
    except subprocess.TimeoutExpired:
        logger.error("FFprobe timeout while probing video URL")
        return {}
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.error(f"Failed to parse video info from URL: {e}")
        return {}
