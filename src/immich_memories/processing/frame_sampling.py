"""Sampling still frames from a video: one implementation, cached.

Written three times before this: the mood analyser at scale=512, the title
colour sampler at scale=320, and the analysis preview builder. The first two
are the same algorithm — probe the duration, take evenly spaced timestamps,
run one ffmpeg per frame — and neither kept the result, so every run decoded
the same video again.

The width genuinely differs by caller: a colour palette does not need what a
vision model needs. That is a parameter, not a reason to write it twice.

Frames are cached under a key covering everything that changes the pixels:
the video's identity and size, how many frames, and how wide. A cache that
cannot be written costs decodes, never the run.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_FALLBACK_DURATION_SECONDS = 60.0


def even_timestamps(duration: float, count: int) -> list[float]:
    """Evenly spaced sample points, avoiding the very first and last frame.

    duration*i/(count+1) rather than i/count: the first and last frame of a
    clip are the ones most likely to be a fade, a black frame or a cut.
    """
    usable = duration if duration > 0 else _FALLBACK_DURATION_SECONDS
    return [usable * i / (count + 1) for i in range(1, count + 1)]


def probe_duration(video: Path) -> float:
    """Length in seconds, or 0.0 when the container will not say."""
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, path is not shell-interpreted
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return float(result.stdout.strip())
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        logger.debug("Could not read duration of %s (%s)", video.name, type(exc).__name__)
        return 0.0


def _extract_one(video: Path, timestamp: float, width: int, out_path: Path) -> bool:
    """One frame at one timestamp. -ss before -i so ffmpeg seeks instead of decoding."""
    try:
        subprocess.run(  # noqa: S603 - fixed argv, paths are not shell-interpreted
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(timestamp),
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                "-vf",
                f"scale={width}:-1",
                str(out_path),
            ],
            capture_output=True,
            check=True,
            timeout=60,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("Frame at %.1fs failed (%s)", timestamp, type(exc).__name__)
        return False
    return out_path.exists()


def _key_for(video: Path, count: int, width: int) -> str:
    """What identifies these frames: which video, how many, how wide.

    Size and mtime stand in for content — hashing gigabytes to decide whether
    to skip a decode would cost more than the decode.
    """
    try:
        stat = video.stat()
        identity = f"{video.name}:{stat.st_size}:{int(stat.st_mtime)}"
    except OSError:
        identity = video.name
    return hashlib.sha256(f"{identity}:{count}:{width}".encode()).hexdigest()[:16]


def sample_frames(
    video: Path,
    *,
    count: int,
    width: int,
    cache_dir: Path | None,
) -> list[Path]:
    """Evenly spaced frames from a video, decoded at most once per (video, count, width).

    Returns the frames that could be extracted, in order. A video that yields
    nothing returns an empty list rather than raising: a caller that cannot see
    the pictures should degrade, not fail.
    """
    target = _prepared_dir(cache_dir, _key_for(video, count, width))
    if target is not None:
        cached = sorted(target.glob("frame_*.jpg"))
        if len(cached) == count:
            logger.debug("Reusing %d cached frame(s) for %s", count, video.name)
            return cached

    out_dir = target if target is not None else _scratch_dir(video)
    if out_dir is None:
        return []

    frames = []
    for index, timestamp in enumerate(even_timestamps(probe_duration(video), count)):
        out_path = out_dir / f"frame_{index:03d}.jpg"
        if _extract_one(video, timestamp, width, out_path):
            frames.append(out_path)
    return frames


def _prepared_dir(cache_dir: Path | None, key: str) -> Path | None:
    if cache_dir is None:
        return None
    try:
        target = Path(cache_dir) / key
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.debug("Frame cache unwritable (%s): decoding again", exc)
        return None
    return target


def _scratch_dir(video: Path) -> Path | None:
    """Somewhere to put frames nobody will keep."""
    import tempfile

    try:
        return Path(tempfile.mkdtemp(prefix=f"frames-{video.stem}-"))
    except OSError:
        return None
