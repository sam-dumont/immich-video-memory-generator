"""Security utilities for input validation and sanitization."""

from __future__ import annotations

import os
import re
from pathlib import Path

# Characters that could cause issues in shell contexts (even without shell=True)
DANGEROUS_CHARS = re.compile(r"[\x00-\x1f\x7f]")  # Control characters including null bytes


class PathValidationError(ValueError):
    """Raised when path validation fails."""

    pass


def validate_path(
    path: Path | str,
    must_exist: bool = False,
    allowed_extensions: set[str] | None = None,
    base_dir: Path | None = None,
) -> Path:
    """Validate and sanitize a file path for safe use in subprocess calls.

    Args:
        path: The path to validate
        must_exist: If True, raise error if path doesn't exist
        allowed_extensions: Set of allowed extensions (e.g., {'.mp4', '.mov'})
        base_dir: If provided, ensure path is within this directory

    Returns:
        Validated Path object

    Raises:
        PathValidationError: If path fails validation
    """
    if isinstance(path, str):
        path = Path(path)

    path_str = str(path)

    # Check for null bytes (command injection via null byte truncation)
    if "\x00" in path_str:
        raise PathValidationError(f"Path contains null byte: {repr(path_str)}")

    # Check for other control characters
    if DANGEROUS_CHARS.search(path_str):
        raise PathValidationError(f"Path contains control characters: {repr(path_str)}")

    # Resolve to absolute path (handles .., symlinks, etc.)
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError) as e:
        raise PathValidationError(f"Cannot resolve path: {e}") from e

    # Check base directory constraint (prevent path traversal)
    if base_dir is not None:
        base_resolved = base_dir.resolve()
        try:
            resolved.relative_to(base_resolved)
        except ValueError as e:
            raise PathValidationError(
                f"Path '{resolved}' is outside allowed directory '{base_resolved}'"
            ) from e

    # Check extension if specified
    if allowed_extensions is not None:
        ext = resolved.suffix.lower()
        if ext not in allowed_extensions:
            raise PathValidationError(
                f"Extension '{ext}' not in allowed list: {allowed_extensions}"
            )

    # Check existence if required
    if must_exist and not resolved.exists():
        raise PathValidationError(f"Path does not exist: {resolved}")

    return resolved


def validate_video_path(path: Path | str, must_exist: bool = True) -> Path:
    """Validate a video file path.

    Args:
        path: The video path to validate
        must_exist: If True, raise error if path doesn't exist

    Returns:
        Validated Path object
    """
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
    """Validate an audio file path.

    Args:
        path: The audio path to validate
        must_exist: If True, raise error if path doesn't exist

    Returns:
        Validated Path object
    """
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
    """Validate an image file path.

    Args:
        path: The image path to validate
        must_exist: If True, raise error if path doesn't exist

    Returns:
        Validated Path object
    """
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
        filename: The filename to sanitize
        max_length: Maximum length for the filename

    Returns:
        Sanitized filename
    """
    # Remove null bytes and control characters
    sanitized = DANGEROUS_CHARS.sub("", filename)

    # Replace path separators
    sanitized = sanitized.replace("/", "_").replace("\\", "_")

    # Remove leading/trailing whitespace and dots
    sanitized = sanitized.strip(" .")

    # Truncate if too long (preserve extension if possible)
    if len(sanitized) > max_length:
        name, ext = os.path.splitext(sanitized)
        if len(ext) < max_length:
            name = name[: max_length - len(ext)]
            sanitized = name + ext
        else:
            sanitized = sanitized[:max_length]

    # Ensure non-empty
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
