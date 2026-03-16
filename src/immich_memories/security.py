"""Security utilities for input validation and sanitization."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Control characters for sanitizing filenames
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def validate_path(
    path: Path | str,
    must_exist: bool = False,
    allowed_extensions: set[str] | None = None,
) -> Path:
    """Resolve a file path and optionally check existence / extension.

    Args:
        path: The path to validate.
        must_exist: If True, raise error if path doesn't exist.
        allowed_extensions: Set of allowed extensions (e.g., {'.mp4', '.mov'}).

    Returns:
        Resolved Path object.

    Raises:
        ValueError: If path fails validation.
    """
    if isinstance(path, str):
        path = Path(path)

    try:
        resolved = path.resolve()
    except (OSError, RuntimeError) as e:
        raise ValueError(f"Cannot resolve path: {e}") from e

    if allowed_extensions is not None:
        ext = resolved.suffix.lower()
        if ext not in allowed_extensions:
            raise ValueError(f"Extension '{ext}' not in allowed list: {allowed_extensions}")

    if must_exist and not resolved.exists():
        raise ValueError(f"Path does not exist: {resolved}")

    return resolved


def validate_video_path(path: Path | str, must_exist: bool = True) -> Path:
    """Validate a video file path."""
    VIDEO_EXTENSIONS = {
        ".mp4",
        ".mov",
        ".avi",
        ".mkv",
        ".webm",
        ".m4v",
        ".wmv",
        ".flv",
        ".mpeg",
        ".mpg",
        ".3gp",
        ".ts",
    }
    return validate_path(path, must_exist=must_exist, allowed_extensions=VIDEO_EXTENSIONS)


def validate_audio_path(path: Path | str, must_exist: bool = True) -> Path:
    """Validate an audio file path."""
    AUDIO_EXTENSIONS = {
        ".mp3",
        ".m4a",
        ".wav",
        ".flac",
        ".ogg",
        ".aac",
        ".wma",
        ".opus",
        ".aiff",
    }
    return validate_path(path, must_exist=must_exist, allowed_extensions=AUDIO_EXTENSIONS)


def validate_image_path(path: Path | str, must_exist: bool = True) -> Path:
    """Validate an image file path."""
    IMAGE_EXTENSIONS = {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        ".webp",
        ".tiff",
        ".tif",
        ".heic",
        ".heif",
    }
    return validate_path(path, must_exist=must_exist, allowed_extensions=IMAGE_EXTENSIONS)


def sanitize_filename(filename: str, max_length: int = 255) -> str:
    """Sanitize a filename for safe filesystem use.

    Args:
        filename: The filename to sanitize.
        max_length: Maximum length for the filename.

    Returns:
        Sanitized filename.
    """
    sanitized = _CONTROL_CHARS.sub("", filename).replace("/", "_").replace("\\", "_").strip(" .")

    # Truncate if too long (preserve extension if possible)
    if len(sanitized) > max_length:
        name, ext = os.path.splitext(sanitized)
        if len(ext) < max_length:
            name = name[: max_length - len(ext)]
            sanitized = name + ext
        else:
            sanitized = sanitized[:max_length]

    if not sanitized:
        sanitized = "unnamed"

    return sanitized


def sanitize_error_message(msg: str) -> str:
    """Strip API keys and auth tokens from error messages before displaying to users.

    Prevents accidental exposure of credentials in UI error displays or logs.
    """
    msg = re.sub(r"x-api-key['\"]?\s*[:=]\s*['\"]?\S+", "x-api-key=***", msg, flags=re.IGNORECASE)
    msg = re.sub(r"Bearer\s+\S+", "Bearer ***", msg, flags=re.IGNORECASE)
    msg = re.sub(r"api[_-]?key['\"]?\s*[:=]\s*['\"]?\S+", "api_key=***", msg, flags=re.IGNORECASE)
    return msg
