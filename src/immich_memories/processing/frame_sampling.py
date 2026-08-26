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
SEGMENT_RENDER_VERSION = "1"


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
    duration = probe_duration(video)
    return list(
        sample_segment_frames(
            video,
            start_time=0.0,
            end_time=duration if duration > 0 else _FALLBACK_DURATION_SECONDS,
            count=count,
            width=width,
            cache_dir=cache_dir,
        )
    )


def sample_segment_frames(
    video: Path,
    *,
    start_time: float,
    end_time: float,
    count: int,
    width: int,
    cache_dir: Path | None,
    render_version: str | None = None,
) -> tuple[Path, ...]:
    """Sample a bounded video segment, caching only complete usable frame sets."""
    if count <= 0 or width <= 0 or end_time <= start_time or not _usable_video(video):
        return ()
    version = render_version or SEGMENT_RENDER_VERSION
    key = _segment_key_for(video, start_time, end_time, count, width, version)
    target = _prepared_dir(cache_dir, key)
    if target is not None:
        cached = _usable_cached_frames(target, count)
        if len(cached) == count:
            logger.debug("Reusing %d segment frame(s) for %s", count, video.name)
            return tuple(cached)

    out_dir = target if target is not None else _scratch_dir(video)
    if out_dir is None:
        return ()
    timestamps = [start_time + moment for moment in even_timestamps(end_time - start_time, count)]
    frames: list[Path] = []
    for index, timestamp in enumerate(timestamps):
        out_path = out_dir / f"frame_{index:03d}.jpg"
        if _extract_one(video, timestamp, width, out_path) or (
            timestamp != start_time and _extract_one(video, start_time, width, out_path)
        ):
            frames.append(out_path)
    return tuple(frames)


def _segment_key_for(
    video: Path,
    start_time: float,
    end_time: float,
    count: int,
    width: int,
    render_version: str,
) -> str:
    """Identify pixels by resolved source metadata and every rendering choice."""
    try:
        stat = video.stat()
        identity = f"{video.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        identity = str(video.resolve())
    payload = f"{identity}:{start_time:.9f}:{end_time:.9f}:{count}:{width}:{render_version}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _usable_video(video: Path) -> bool:
    """Reject incomplete downloads and empty inputs before invoking FFmpeg."""
    try:
        return video.is_file() and video.suffix != ".part" and video.stat().st_size > 0
    except OSError:
        return False


def _usable_cached_frames(target: Path, count: int) -> list[Path]:
    frames: list[Path] = []
    for frame in sorted(target.glob("frame_*.jpg")):
        try:
            if frame.is_file() and frame.suffix != ".part" and frame.stat().st_size > 0:
                frames.append(frame)
        except OSError:
            continue
    return frames if len(frames) == count else []


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
