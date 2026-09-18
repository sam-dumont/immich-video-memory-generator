"""Run-scoped, normalized source-media probing."""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from operator import itemgetter
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
    video_start_seconds: float = 0.0
    average_frame_rate: str | None = None
    nominal_frame_rate: str | None = None
    video_time_base: str | None = None
    container_start_seconds: float = 0.0

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


def _parsed_rate(value: str) -> float:
    if "/" in value:
        numerator, denominator = value.split("/", maxsplit=1)
        return float(numerator) / float(denominator) if float(denominator) else 0.0
    return float(value) if value else 0.0


def _frame_rate(stream: dict[str, Any]) -> float:
    for raw_value in (stream.get("avg_frame_rate"), stream.get("r_frame_rate")):
        value = str(raw_value or "")
        try:
            rate = _parsed_rate(value)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if math.isfinite(rate) and rate > 0:
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
        video_start_seconds=_number(video.get("start_time")),
        average_frame_rate=video.get("avg_frame_rate"),
        nominal_frame_rate=video.get("r_frame_rate"),
        video_time_base=video.get("time_base"),
        container_start_seconds=_number(format_data.get("start_time")),
    )


def _microseconds(seconds: float) -> int:
    """Parse a trim option the way av_parse_time does: six fractional digits, truncated."""
    whole, _, fraction = f"{abs(seconds):.9f}".partition(".")
    magnitude = int(whole) * 1_000_000 + int(fraction[:6])
    return -magnitude if seconds < 0 else magnitude


def trim_ticks(seconds: float, clock: Fraction) -> int:
    """Rescale microseconds onto the packet clock, rounding half away like av_rescale_q."""
    return math.floor(Fraction(_microseconds(seconds), 1_000_000) / clock + Fraction(1, 2))


class ProbeCache:
    """Cache comprehensive ffprobe results for the lifetime of one caller-owned run."""

    def __init__(self) -> None:
        self._entries: dict[Path, tuple[ProbeKey, VideoProbe]] = {}
        self._packet_entries: dict[Path, tuple[ProbeKey, dict]] = {}

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
        self._packet_entries.pop(resolved, None)

    def _video_packets(self, path: Path | str) -> dict:
        """Read selected compressed presentation timestamps, cached by file identity."""
        source = validate_video_path(path, must_exist=True)
        stat = source.stat()
        key = ProbeKey(source, stat.st_size, stat.st_mtime_ns)
        cached = self._packet_entries.get(source)
        if cached is not None and cached[0] == key:
            return cached[1]
        probe = self.get(source)
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    str(probe.video_stream_index),
                    "-show_packets",
                    "-show_entries",
                    "stream=time_base:packet=pts,duration,flags",
                    "-of",
                    "json",
                    str(source),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result.returncode:
                raise ValueError("packet probe failed")
            data = json.loads(result.stdout)
            clock = Fraction(data["streams"][0]["time_base"])
            # MOV edit lists retain reference packets that are decoded but never
            # displayed. They cannot establish a visible frame or its cadence.
            packets = [p for p in data["packets"] if "D" not in p.get("flags", "")]
            if clock <= 0 or not packets or any(type(p.get("pts")) is not int for p in packets):
                raise ValueError("missing presentation timestamps")
            packets.sort(key=itemgetter("pts"))
            if len({p["pts"] for p in packets}) != len(packets):
                raise ValueError("ambiguous duplicate presentation timestamps")
            value = {"clock": clock, "packets": packets}
            self._packet_entries[source] = key, value
            return value
        except (
            OSError,
            subprocess.SubprocessError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            ZeroDivisionError,
        ) as exc:
            raise ProbeError("Source has no verified presentation packet clock") from exc

    def render_frame_rate(self, path: Path | str) -> dict:
        """Preserve the densest selected cadence, independently of average metadata fps.

        Matching valid stream rates retain the CFR fast path, including true high
        rates. VFR/ambiguous sources need unique actual PTS; an average fallback
        cannot establish that a common output frame grid preserves dense motion.
        """
        probe = self.get(path)
        try:
            average = Fraction(probe.average_frame_rate or "0")
            nominal = Fraction(probe.nominal_frame_rate or "0")
        except (ValueError, ZeroDivisionError):
            average = nominal = Fraction(0)
        if average > 0 and average == nominal:
            return {"basis": "matching-stream-rates", "rate": str(average), "fps": float(average)}
        data = self._video_packets(path)
        packets, clock = data["packets"], data["clock"]
        if len(packets) < 2:
            raise ProbeError("Source has no verified presentation cadence")
        spacing = min(b["pts"] - a["pts"] for a, b in zip(packets, packets[1:], strict=False))
        rate = 1 / (spacing * clock)
        return {
            "basis": "presentation-packet-spacing",
            "rate": str(rate),
            "fps": float(rate),
            "packet_count": len(packets),
            "time_base": str(clock),
            "min_spacing_ticks": spacing,
        }

    def first_video_frame(self, path: Path | str) -> dict[str, float | int | str]:
        """Bind a declared start to the actual first presentation packet."""
        return self._video_frame(path, 0, "initial")

    def last_video_frame(self, path: Path | str) -> dict[str, float | int | str]:
        """Bind a rounded source endpoint to its actual final presentation packet."""
        return self._video_frame(path, -1, "final")

    def _video_frame(self, path: Path | str, index: int, label: str) -> dict:
        data = self._video_packets(path)
        clock, packet = data["clock"], data["packets"][index]
        ticks = packet.get("duration")
        if type(ticks) is not int or ticks <= 0:
            raise ProbeError(f"Source has no verified {label} presentation frame")
        return {
            "time_base": str(clock),
            "pts": packet["pts"],
            "duration_ticks": ticks,
            "start_seconds": float(packet["pts"] * clock),
            "end_seconds": float((packet["pts"] + ticks) * clock),
            "frame_seconds": float(ticks * clock),
        }

    def quantized_segment(
        self, path: Path | str, start: float, end: float, rate: Fraction
    ) -> dict[str, float | int | str]:
        """Frames the certified trim/fps graph emits for a declared interval of this source.

        ffmpeg re-bases every packet by the container start, keeps the packets whose
        timestamp lies in ``[start, end)``, ends the segment at the first packet at or
        past ``end`` (or the last packet's end), and ``fps=...:eof_action=pass`` rounds
        that endpoint UP to the output grid. A cut inside an irregular source can
        therefore carry a whole source interval, not one output frame.
        """
        data = self._video_packets(path)
        clock, packets = data["clock"], data["packets"]
        origin = trim_ticks(self.get(path).container_start_seconds, clock)
        first_tick, end_tick = trim_ticks(start, clock) + origin, trim_ticks(end, clock) + origin
        kept = [p["pts"] for p in packets if first_tick <= p["pts"] < end_tick]
        if not kept:
            raise ProbeError("Declared interval holds no source frame")
        later = [p["pts"] for p in packets if p["pts"] >= end_tick]
        tail = packets[-1]
        if later:
            eof = later[0]
        elif type(tail.get("duration")) is int and tail["duration"] > 0:
            eof = tail["pts"] + tail["duration"]
        else:
            raise ProbeError("Source has no verified final presentation frame")
        frames = math.ceil((eof - kept[0]) * clock * rate)
        return {
            "time_base": str(clock),
            "origin_pts": origin,
            "first_pts": kept[0],
            "eof_pts": eof,
            "kept_packets": len(kept),
            "frames": frames,
            "seconds": float(Fraction(frames) / rate),
        }

    @staticmethod
    def _probe(path: Path) -> VideoProbe:
        command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            (
                "stream=index,codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,"
                "bit_rate,duration,start_time,time_base,color_space,color_transfer,color_primaries,"
                "bits_per_raw_sample,sample_rate,channels:stream_side_data=rotation:"
                "format=duration,size,bit_rate,start_time"
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
