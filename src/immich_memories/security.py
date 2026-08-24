"""Security utilities for input validation and sanitization."""

from __future__ import annotations

import contextlib
import logging
import os
import re
import stat
import tempfile
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Control characters for sanitizing filenames
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
# Field names that hold a secret, wherever they appear in the config tree.
CREDENTIAL_FIELD_NAMES = frozenset({"api_key", "password", "client_secret", "trigger_token"})


def write_secret_file(path: Path, text: str) -> None:
    """Write a file that only its owner can read, from the moment it exists.

    `open("w")`/`write_text` create with the process umask -- 0644 on a normal
    system -- so narrowing the file afterwards with chmod leaves the secret
    world-readable in between. On a shared box or a NAS that window is the whole
    exposure, and it is invisible after the fact because the file looks correct
    by the time anyone inspects it. Creating at 0600 and renaming into place also
    means a crash mid-write cannot leave a truncated secret behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


def _assert_private(path: Path) -> None:
    """Refuse a path someone else could have prepared for us."""
    info = path.lstat()
    owned = info.st_uid == os.getuid()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or not owned:
        raise RuntimeError(f"{path} is not a private directory")
    if stat.S_IMODE(info.st_mode) & (stat.S_IRWXG | stat.S_IRWXO):
        raise RuntimeError(f"{path} is not a private directory (group/other access)")


def private_temp_dir(name: str) -> Path:
    """A scratch directory under the system temp dir that only this user can enter.

    `tempfile.gettempdir()` is shared and world-writable, so a fixed path under
    it is a name another user can claim first -- as a symlink we then write
    through, since `ffmpeg -y` follows one, or as a directory they can read.
    Scoping by uid and refusing anything we do not own closes both.

    `lstat` rather than `stat`: the question is what the path itself is, and a
    symlink to a directory we own would answer "directory" through `stat`.
    """
    root = Path(tempfile.gettempdir()) / f"immich-memories-{os.getuid()}"
    target = root / name
    root.mkdir(mode=0o700, exist_ok=True)
    _assert_private(root)
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    _assert_private(target)
    return target


def configured_secret_values(config: BaseModel) -> tuple[str, ...]:
    """Return configured credentials and secret-bearing notification URLs."""
    secrets: set[str] = set()
    pending: list[object] = [config]
    while pending:
        value = pending.pop()
        if isinstance(value, BaseModel):
            for field_name in type(value).model_fields:
                field_value = getattr(value, field_name)
                if field_name in CREDENTIAL_FIELD_NAMES:
                    if isinstance(field_value, str) and field_value:
                        secrets.add(field_value)
                else:
                    pending.append(field_value)
        elif isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, (list, tuple, set, frozenset)):
            pending.extend(value)

    notifications = getattr(config, "notifications", None)
    for url in getattr(notifications, "urls", ()):
        if isinstance(url, str) and url:
            secrets.add(url)
    return tuple(sorted(secrets, key=len, reverse=True))


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
