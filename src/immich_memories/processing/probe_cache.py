"""Run-scoped, normalized source-media probing."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from immich_memories.security import validate_video_path


class ProbeError(RuntimeError):
    """A source file could not be inspected by ffprobe."""


@dataclass(frozen=True, slots=True)
class ProbeKey:
    """Filesystem identity used for one run's source-probe entries."""

    path: Path
    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class VideoProbe:
    """Normalized metadata from one comprehensive ffprobe invocation."""

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

    @property
    def resolution(self) -> tuple[int, int] | None:
        """Return display resolution after rotation metadata is applied."""
        if self.width <= 0 or self.height <= 0:
            return None
        if self.rotation in (90, 270):
            return self.height, self.width
        return self.width, self.height

    @property
    def hdr_type(self) -> str | None:
        """Return ``hlg``/``pq`` only for wide-gamut HDR sources."""
        if self.color_primaries != "bt2020":
            return None
        if self.color_transfer == "arib-std-b67":
            return "hlg"
        if self.color_transfer in {"smpte2084", "bt2020-10", "bt2020-12"}:
            return "pq"
        return None


def _number(value: object, default: float = 0.0) -> float:
    try:
        return float(str(value)) if value not in {None, "", "N/A"} else default
    except (TypeError, ValueError):
        return default


def _integer(value: object, default: int = 0) -> int:
    try:
        return int(str(value)) if value not in {None, "", "N/A"} else default
    except (TypeError, ValueError):
        return default


def _frame_rate(stream: dict[str, Any]) -> float:
    for raw_value in (stream.get("r_frame_rate"), stream.get("avg_frame_rate")):
        value = str(raw_value or "")
        try:
            if "/" in value:
                numerator, denominator = value.split("/", maxsplit=1)
                rate = float(numerator) / float(denominator) if float(denominator) else 0.0
            else:
                rate = float(value) if value else 0.0
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if rate > 0:
            return rate
    return 0.0


def _rotation(stream: dict[str, Any]) -> int:
    for side_data in stream.get("side_data_list", []):
        if "rotation" in side_data:
            return abs(_integer(side_data["rotation"])) % 360
    return 0


def _parse_video_probe(data: dict[str, Any]) -> VideoProbe:
    streams = data.get("streams", [])
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    video: dict[str, Any] = max(
        video_streams,
        key=lambda stream: _integer(stream.get("width")) * _integer(stream.get("height")),
        default={},
    )
    audio = audio_streams[0] if audio_streams else {}
    format_data = data.get("format", {})
    bit_depth = _integer(video.get("bits_per_raw_sample"), -1)
    return VideoProbe(
        duration_seconds=_number(format_data.get("duration")),
        video_duration_seconds=_number(video.get("duration")),
        audio_duration_seconds=_number(audio.get("duration")),
        width=_integer(video.get("width")),
        height=_integer(video.get("height")),
        fps=_frame_rate(video),
        codec=str(video.get("codec_name") or ""),
        bitrate=_integer(format_data.get("bit_rate")),
        size_bytes=_integer(format_data.get("size")),
        color_space=video.get("color_space"),
        color_transfer=video.get("color_transfer"),
        color_primaries=video.get("color_primaries"),
        bit_depth=None if bit_depth < 0 else bit_depth,
        rotation=_rotation(video),
        video_stream_index=_integer(video.get("index")),
        has_video=bool(video_streams),
        has_audio=bool(audio_streams),
        audio_codec=str(audio.get("codec_name")) if audio.get("codec_name") else None,
        audio_bitrate=_integer(audio.get("bit_rate")),
    )


class ProbeCache:
    """Cache comprehensive ffprobe results for the lifetime of one caller-owned run."""

    def __init__(self) -> None:
        self._entries: dict[Path, tuple[ProbeKey, VideoProbe]] = {}

    def get(self, path: Path | str) -> VideoProbe:
        validated = validate_video_path(path, must_exist=True)
        stat = validated.stat()
        key = ProbeKey(validated, stat.st_size, stat.st_mtime_ns)
        cached = self._entries.get(validated)
        if cached is not None and cached[0] == key:
            return cached[1]

        probe = self._probe(validated)
        self._entries[validated] = (key, probe)
        return probe

    def invalidate(self, path: Path | str) -> None:
        """Forget a path without touching the underlying media file."""
        try:
            resolved = Path(path).resolve()
        except (OSError, RuntimeError):
            return
        self._entries.pop(resolved, None)

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
                "format=duration,size,bit_rate"
            ),
            "-of",
            "json",
            str(path),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProbeError(f"ffprobe could not inspect {path.name}") from exc
        if result.returncode != 0:
            raise ProbeError(f"ffprobe could not inspect {path.name}")
        try:
            return _parse_video_probe(json.loads(result.stdout))
        except (json.JSONDecodeError, AttributeError, TypeError, ValueError) as exc:
            raise ProbeError(f"ffprobe returned invalid metadata for {path.name}") from exc
