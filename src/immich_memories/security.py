"""Security utilities for input validation and sanitization."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

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
        raise PathValidationError(f"Path contains null byte: {path_str!r}")

    # Check for other control characters
    if DANGEROUS_CHARS.search(path_str):
        raise PathValidationError(f"Path contains control characters: {path_str!r}")

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

    # Validate magic bytes for existing files (defense-in-depth against disguised files)
    if resolved.exists() and resolved.is_file() and not validate_magic_bytes(resolved):
        raise PathValidationError(
            f"File magic bytes don't match extension '{resolved.suffix}': {resolved.name}"
        )

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


# Magic bytes for media file validation (prevents disguised file attacks)
# Maps extensions to their expected file signatures
MEDIA_MAGIC_BYTES: dict[str, list[bytes]] = {
    # Video formats
    ".mp4": [b"\x00\x00\x00\x18ftyp", b"\x00\x00\x00\x1cftyp", b"\x00\x00\x00\x20ftyp"],
    ".mov": [b"\x00\x00\x00\x14ftyp", b"\x00\x00\x00\x08wide"],
    ".avi": [b"RIFF"],
    ".mkv": [b"\x1a\x45\xdf\xa3"],
    ".webm": [b"\x1a\x45\xdf\xa3"],
    ".flv": [b"FLV\x01"],
    ".mpeg": [b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"],
    ".mpg": [b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"],
    ".ts": [b"\x47"],
    ".m4v": [b"\x00\x00\x00\x18ftyp", b"\x00\x00\x00\x1cftyp", b"\x00\x00\x00\x20ftyp"],
    ".3gp": [b"\x00\x00\x00\x14ftyp", b"\x00\x00\x00\x18ftyp", b"\x00\x00\x00\x1cftyp"],
    ".wmv": [b"\x30\x26\xb2\x75"],
    # Audio formats
    ".mp3": [b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"ID3"],
    ".wav": [b"RIFF"],
    ".flac": [b"fLaC"],
    ".ogg": [b"OggS"],
    ".m4a": [b"\x00\x00\x00\x18ftyp", b"\x00\x00\x00\x1cftyp", b"\x00\x00\x00\x20ftyp"],
    ".aac": [b"\xff\xf1", b"\xff\xf9"],
    ".aiff": [b"FORM"],
    ".opus": [b"OggS"],
    ".wma": [b"\x30\x26\xb2\x75"],
    # Image formats
    ".jpg": [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".png": [b"\x89PNG\r\n\x1a\n"],
    ".gif": [b"GIF87a", b"GIF89a"],
    ".bmp": [b"BM"],
    ".webp": [b"RIFF"],
    ".tiff": [b"II\x2a\x00", b"MM\x00\x2a"],
    ".tif": [b"II\x2a\x00", b"MM\x00\x2a"],
}


def validate_magic_bytes(path: Path) -> bool:
    """Validate that a file's magic bytes match its extension.

    This prevents attacks where a malicious file (e.g., a script) is disguised
    with a media extension to bypass extension-based validation.

    Args:
        path: Path to the file to validate (must exist)

    Returns:
        True if magic bytes match or extension has no known signature,
        False if magic bytes don't match the expected format.
    """
    ext = path.suffix.lower()
    expected_signatures = MEDIA_MAGIC_BYTES.get(ext)
    if expected_signatures is None:
        # No known signature for this extension, skip check
        return True

    try:
        # Read enough bytes to check the longest signature
        max_sig_len = max(len(sig) for sig in expected_signatures)
        with path.open("rb") as f:
            header = f.read(max_sig_len)

        if not header:
            return False

        return any(header[: len(sig)] == sig for sig in expected_signatures)
    except OSError:
        return False


def sanitize_filename(filename: str, max_length: int = 255) -> str:
    """Sanitize a filename for safe filesystem use.

    Args:
        filename: The filename to sanitize
        max_length: Maximum length for the filename

    Returns:
        Sanitized filename
    """
    # Remove null bytes, control characters, path separators, whitespace and dots
    sanitized = DANGEROUS_CHARS.sub("", filename).replace("/", "_").replace("\\", "_").strip(" .")

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
