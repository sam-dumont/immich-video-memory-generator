"""Thread-safe, caller-owned cache for comprehensive source-media probes."""

from __future__ import annotations

import json
import math
import subprocess
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from immich_memories.security import validate_video_path


class ProbeError(RuntimeError):
    """Base error raised when a source file cannot be inspected."""


class ProbePathError(ProbeError):
    """The source path cannot be resolved or is not a supported video path."""


class ProbeMissingFileError(ProbeError):
    """The source file disappeared before it could be inspected."""


class ProbeStatError(ProbeError):
    """The source file could not be stat'ed."""


class ProbeProcessError(ProbeError):
    """ffprobe could not inspect the source file."""


class ProbeMetadataError(ProbeError):
    """ffprobe returned malformed metadata."""


@dataclass(frozen=True, slots=True)
class VideoStreamProbe:
    """Immutable normalized metadata for one video stream."""

    index: int
    codec: str
    duration_seconds: float
    bitrate: int
    width: int
    height: int
    fps: float
    color_space: str | None
    color_transfer: str | None
    color_primaries: str | None
    bit_depth: int | None
    rotation: int


@dataclass(frozen=True, slots=True)
class AudioStreamProbe:
    """Immutable normalized metadata for one audio stream."""

    index: int
    codec: str
    duration_seconds: float
    bitrate: int
    sample_rate: int
    channels: int


@dataclass(frozen=True, slots=True)
class ProbeKey:
    """Canonical filesystem identity for one cache entry.

    Symlink aliases intentionally share an entry because ``path`` is resolved.
    Size or nanosecond mtime changes create a new identity automatically.
    """

    path: Path
    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class VideoProbe:
    """Normalized metadata returned by one comprehensive JSON ffprobe call."""

    duration_seconds: float
    video_duration_seconds: float
    audio_duration_seconds: float
    width: int
    height: int
    fps: float
    codec: str
    bitrate: int
    size_bytes: int
    color_space: str | None
    color_transfer: str | None
    color_primaries: str | None
    bit_depth: int | None
    rotation: int
    video_stream_index: int
    has_video: bool
    has_audio: bool
    audio_codec: str | None
    audio_bitrate: int
    video_streams: tuple[VideoStreamProbe, ...]
    audio_streams: tuple[AudioStreamProbe, ...]

    @property
    def main_video(self) -> VideoStreamProbe | None:
        return max(
            self.video_streams,
            key=lambda stream: stream.width * stream.height,
            default=None,
        )

    @property
    def primary_audio(self) -> AudioStreamProbe | None:
        return self.audio_streams[0] if self.audio_streams else None

    @property
    def max_audio_bitrate(self) -> int:
        return max((stream.bitrate for stream in self.audio_streams), default=0)

    @property
    def resolution(self) -> tuple[int, int] | None:
        if self.width <= 0 or self.height <= 0:
            return None
        if self.rotation in (90, 270):
            return self.height, self.width
        return self.width, self.height

    @property
    def hdr_type(self) -> str | None:
        if self.color_primaries != "bt2020":
            return None
        if self.color_transfer == "arib-std-b67":
            return "hlg"
        if self.color_transfer in {"smpte2084", "bt2020-10", "bt2020-12"}:
            return "pq"
        return None


def _number(value: object, default: float = 0.0) -> float:
    try:
        number = float(str(value)) if value not in {None, "", "N/A"} else default
        return number if math.isfinite(number) and number > 0 else default
    except (TypeError, ValueError):
        return default


def _integer(value: object, default: int = 0) -> int:
    try:
        number = int(str(value)) if value not in {None, "", "N/A"} else default
        return number if number >= 0 else default
    except (TypeError, ValueError):
        return default


def _signed_integer(value: object, default: int = 0) -> int:
    try:
        return int(str(value)) if value not in {None, "", "N/A"} else default
    except (TypeError, ValueError):
        return default


def _frame_rate(stream: dict[str, Any]) -> float:
    rates = (
        _valid_frame_rate(value)
        for value in (stream.get("r_frame_rate"), stream.get("avg_frame_rate"))
    )
    return next((rate for rate in rates if rate), 0.0)


def _valid_frame_rate(value: object) -> float:
    try:
        rate = _fraction_rate(str(value or ""))
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0
    return rate if math.isfinite(rate) and rate > 0 else 0.0


def _fraction_rate(value: str) -> float:
    if not value:
        return 0.0
    if "/" not in value:
        return float(value)
    numerator, denominator = value.split("/", maxsplit=1)
    return float(numerator) / float(denominator)


def _rotation(stream: dict[str, Any]) -> int:
    tags = stream.get("tags")
    if isinstance(tags, dict) and "rotate" in tags:
        return abs(_signed_integer(tags["rotate"])) % 360
    for side_data in stream.get("side_data_list", []):
        if "rotation" in side_data:
            return abs(_signed_integer(side_data["rotation"])) % 360
    return 0


def _video_stream(stream: dict[str, Any]) -> VideoStreamProbe:
    bit_depth = _integer(stream.get("bits_per_raw_sample"), -1)
    return VideoStreamProbe(
        _integer(stream.get("index")),
        str(stream.get("codec_name") or ""),
        _number(stream.get("duration")),
        _integer(stream.get("bit_rate")),
        _integer(stream.get("width")),
        _integer(stream.get("height")),
        _frame_rate(stream),
        stream.get("color_space"),
        stream.get("color_transfer"),
        stream.get("color_primaries"),
        bit_depth if bit_depth >= 0 else None,
        _rotation(stream),
    )


def _audio_stream(stream: dict[str, Any]) -> AudioStreamProbe:
    return AudioStreamProbe(
        _integer(stream.get("index")),
        str(stream.get("codec_name") or ""),
        _number(stream.get("duration")),
        _integer(stream.get("bit_rate")),
        _integer(stream.get("sample_rate")),
        _integer(stream.get("channels")),
    )


def _stream_records(
    streams: list[Any],
) -> tuple[tuple[VideoStreamProbe, ...], tuple[AudioStreamProbe, ...]]:
    video_streams = tuple(
        _video_stream(stream)
        for stream in streams
        if isinstance(stream, dict) and stream.get("codec_type") == "video"
    )
    audio_streams = tuple(
        _audio_stream(stream)
        for stream in streams
        if isinstance(stream, dict) and stream.get("codec_type") == "audio"
    )
    return video_streams, audio_streams


def _build_video_probe(
    format_data: dict[str, Any],
    video_streams: tuple[VideoStreamProbe, ...],
    audio_streams: tuple[AudioStreamProbe, ...],
) -> VideoProbe:
    video = max(video_streams, key=lambda stream: stream.width * stream.height, default=None)
    audio = audio_streams[0] if audio_streams else None
    video_values = _legacy_video_values(video)
    audio_values = _legacy_audio_values(audio)
    return VideoProbe(
        duration_seconds=_number(format_data.get("duration")),
        bitrate=_integer(format_data.get("bit_rate")),
        size_bytes=_integer(format_data.get("size")),
        has_video=bool(video_streams),
        has_audio=bool(audio_streams),
        video_streams=video_streams,
        audio_streams=audio_streams,
        **video_values,
        **audio_values,
    )


def _legacy_video_values(video: VideoStreamProbe | None) -> dict[str, Any]:
    if video is None:
        return {
            "video_duration_seconds": 0.0,
            "width": 0,
            "height": 0,
            "fps": 0.0,
            "codec": "",
            "color_space": None,
            "color_transfer": None,
            "color_primaries": None,
            "bit_depth": None,
            "rotation": 0,
            "video_stream_index": 0,
        }
    return {
        "video_duration_seconds": video.duration_seconds,
        "width": video.width,
        "height": video.height,
        "fps": video.fps,
        "codec": video.codec,
        "color_space": video.color_space,
        "color_transfer": video.color_transfer,
        "color_primaries": video.color_primaries,
        "bit_depth": video.bit_depth,
        "rotation": video.rotation,
        "video_stream_index": video.index,
    }


def _legacy_audio_values(audio: AudioStreamProbe | None) -> dict[str, Any]:
    if audio is None:
        return {"audio_duration_seconds": 0.0, "audio_codec": None, "audio_bitrate": 0}
    return {
        "audio_duration_seconds": audio.duration_seconds,
        "audio_codec": audio.codec,
        "audio_bitrate": audio.bitrate,
    }


def _parse_video_probe(data: dict[str, Any]) -> VideoProbe:
    streams = data.get("streams")
    format_data = data.get("format")
    if not isinstance(streams, list) or not isinstance(format_data, dict):
        raise ValueError("missing streams or format")
    video_streams, audio_streams = _stream_records(streams)
    return _build_video_probe(format_data, video_streams, audio_streams)


class ProbeCache:
    """Reuse source probes within one caller-owned run.

    Concurrent callers for the same unchanged ``ProbeKey`` wait on one
    in-flight ffprobe. Failures are only delivered to current waiters and are
    never stored, so a later request retries normally.
    """

    def __init__(self) -> None:
        self._entries: dict[Path, tuple[ProbeKey, VideoProbe]] = {}
        self._inflight: dict[tuple[ProbeKey, int], Future[VideoProbe]] = {}
        self._epochs: dict[Path, int] = {}
        self._lock = threading.RLock()

    def get(self, path: Path | str) -> VideoProbe:
        key = self._key(path)
        with self._lock:
            cached = self._entries.get(key.path)
            if cached is not None and cached[0] == key:
                return cached[1]
            epoch = self._epochs.get(key.path, 0)
            inflight_key = (key, epoch)
            pending = self._inflight.get(inflight_key)
            if pending is None:
                pending = Future()
                self._inflight[inflight_key] = pending
                owns_probe = True
            else:
                owns_probe = False
        if not owns_probe:
            return pending.result()

        try:
            probe = self._probe(key.path)
        except BaseException as exc:
            with self._lock:
                if self._inflight.get(inflight_key) is pending:
                    self._inflight.pop(inflight_key, None)
                pending.set_exception(exc)
            raise

        with self._lock:
            # Do not retain metadata captured while the media was rewritten.
            try:
                unchanged = self._key(key.path) == key
            except ProbeError:
                unchanged = False
            if unchanged and self._epochs.get(key.path, 0) == epoch:
                self._entries[key.path] = (key, probe)
            if self._inflight.get(inflight_key) is pending:
                self._inflight.pop(inflight_key, None)
            pending.set_result(probe)
        return probe

    def invalidate(self, path: Path | str) -> None:
        """Forget canonical path metadata after a caller-owned rewrite/publish."""
        try:
            resolved = self._resolve(path)
        except ProbeError:
            return
        with self._lock:
            self._entries.pop(resolved, None)
            self._epochs[resolved] = self._epochs.get(resolved, 0) + 1

    @staticmethod
    def _resolve(path: Path | str) -> Path:
        raw_path = Path(path)
        invalid_path = False
        try:
            resolved = validate_video_path(raw_path, must_exist=False)
        except (OSError, RuntimeError, TypeError, ValueError):
            invalid_path = True
            resolved = None
        if invalid_path:
            raise ProbePathError(f"could not resolve media file {raw_path.name}")
        assert resolved is not None  # noqa: S101
        return resolved

    @classmethod
    def _key(cls, path: Path | str) -> ProbeKey:
        resolved = cls._resolve(path)
        missing = False
        stat_failed = False
        try:
            stat = resolved.stat()
        except FileNotFoundError:
            missing = True
            stat = None
        except OSError:
            stat_failed = True
            stat = None
        if missing:
            raise ProbeMissingFileError(f"media file missing: {resolved.name}")
        if stat_failed:
            raise ProbeStatError(f"could not stat media file {resolved.name}")
        assert stat is not None  # noqa: S101
        return ProbeKey(resolved, stat.st_size, stat.st_mtime_ns)

    @staticmethod
    def _probe(path: Path) -> VideoProbe:
        command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            (
                "stream=index,codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,"
                "bit_rate,duration,color_space,color_transfer,color_primaries,"
                "bits_per_raw_sample,sample_rate,channels:stream_side_data=rotation:"
                "stream_tags=rotate:"
                "format=duration,size,bit_rate"
            ),
            "-of",
            "json",
            str(path),
        ]
        process_failed = False
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            process_failed = True
            result = None
        if process_failed:
            raise ProbeProcessError(f"ffprobe could not inspect {path.name}")
        assert result is not None  # noqa: S101
        if result.returncode != 0:
            raise ProbeProcessError(f"ffprobe could not inspect {path.name}")
        try:
            parsed = json.loads(result.stdout)
            if not isinstance(parsed, dict):
                raise ValueError("metadata is not an object")
            return _parse_video_probe(parsed)
        except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
            pass
        raise ProbeMetadataError(f"ffprobe returned invalid metadata for {path.name}")
