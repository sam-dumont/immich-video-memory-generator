"""Probe, validate, and atomically publish finished video artifacts."""

from __future__ import annotations

import errno
import json
import logging
import math
import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec

logger = logging.getLogger(__name__)

_CODEC_NAMES = {
    OutputCodec.H264: "h264",
    OutputCodec.H265: "hevc",
    OutputCodec.PRORES: "prores",
}
_TARGET_TRANSFERS = {
    HdrTransfer.NONE: ("SDR", "bt709"),
    HdrTransfer.HLG: ("HLG", "arib-std-b67"),
    HdrTransfer.PQ: ("PQ", "smpte2084"),
}
_DECODED_PIXEL_FORMATS = {
    "nv12": "yuv420p",
    "p010le": "yuv420p10le",
    # FFmpeg's legacy name for full-range 8-bit 4:2:0; the plan does not fix range.
    "yuvj420p": "yuv420p",
}
# WHY: the metadata probe reads the container index and nothing else; two minutes
# covers a multi-gigabyte film whose index sits at the far end of a slow share.
_METADATA_TIMEOUT_SECONDS = 2 * 60
_DECODE_CHECK_FLOOR_SECONDS = 15 * 60
# WHY 0.25: measured with the decode command below (FFmpeg 7.1, app image), a
# 1080p HEVC 10-bit film decodes at 3.8x realtime on two Celeron J4125 cores
# and a 4K one at 1.07x. A file this machine did not render gets a quarter of
# realtime, four times the slowest case measured.
_DECODE_RATE_FLOOR = 0.25
_DECODE_PROGRESS_INTERVAL_SECONDS = 60.0
# One progress block every 30 s keeps an eleven-hour check's file under a
# megabyte, and each poll reads only its tail, where the latest block is.
_DECODE_STATS_PERIOD = "30"
_PROGRESS_TAIL_BYTES = 4096
_UNSUPPORTED_FSYNC_ERRNOS = frozenset(
    {errno.EINVAL, getattr(errno, "ENOSYS", errno.EINVAL), getattr(errno, "ENOTSUP", errno.EINVAL)}
)


class InvalidOutputArtifact(ValueError):
    """The rendered file does not satisfy its resolved encoding plan."""


@dataclass(frozen=True, slots=True)
class DecodeCheck:
    """What bounds the full decode of a finished film, and who hears about it.

    ``encode_seconds`` is the wall time this machine just spent rendering the
    film. Decoding a stream is never slower than encoding it on the same
    machine, so that time is the budget, with fifteen minutes as the floor.
    ``None`` means the file was rendered elsewhere and the budget comes from
    the film's duration. ``progress`` hears one line about once a minute.
    """

    encode_seconds: float | None = None
    progress: Callable[[str], None] | None = None

    def budget_seconds(self, duration_seconds: float) -> float:
        """Return how long the decode of a film this long may take."""
        if self.encode_seconds is not None:
            return max(_DECODE_CHECK_FLOOR_SECONDS, self.encode_seconds)
        return max(_DECODE_CHECK_FLOOR_SECONDS, duration_seconds / _DECODE_RATE_FLOOR)


@dataclass(frozen=True, slots=True)
class _Metadata:
    codec: str
    container: str
    duration_seconds: float
    size_bytes: int
    pixel_format: str
    color_transfer: str | None
    color_primaries: str | None
    width: int
    height: int
    # nb_frames, when the container carries it; logged beside the decoded count.
    container_frames: int | None


@dataclass(frozen=True, slots=True)
class FileStamp:
    """Which bytes a check read: a rename keeps all three, a rewrite changes one."""

    size_bytes: int
    mtime_ns: int
    inode: int

    @classmethod
    def of(cls, path: Path) -> FileStamp:
        """Stamp the file as it is on disk now."""
        stat = path.stat()
        return cls(stat.st_size, stat.st_mtime_ns, stat.st_ino)

    def matches(self, path: Path) -> bool:
        """Whether the file on disk is still the one stamped."""
        stat = path.stat()
        return (stat.st_size, stat.st_mtime_ns, stat.st_ino) == (
            self.size_bytes,
            self.mtime_ns,
            self.inode,
        )


@dataclass(frozen=True, slots=True)
class OutputProbe:
    """Normalized metadata, and decoded-frame evidence once the film was decoded.

    ``decoded_frames`` is None for a metadata-only check. ``stamp`` names the
    bytes that were decoded, so a later step can tell whether they changed.
    """

    codec: str
    container: str
    duration_seconds: float
    size_bytes: int
    pixel_format: str
    color_transfer: str | None
    color_primaries: str | None
    width: int
    height: int
    decoded_frames: int | None
    stamp: FileStamp | None = None

    def render_metrics(self, plan: EncodingPlan) -> dict[str, object]:
        """Describe the effective render contract with validated artifact facts."""
        return {
            "output_width": self.width,
            "output_height": self.height,
            "codec": plan.codec.value,
            # Present only when the machine could not encode what was asked for,
            # so a smaller-than-expected codec in a run record is explained.
            "codec_requested": (
                plan.codec_substituted_from.value if plan.codec_substituted_from else None
            ),
            "encoder": plan.encoder,
            "crf": plan.crf,
            "encoder_args": list(plan.encoder_args),
            "planned_pixel_format": plan.pixel_format,
            "output_pixel_format": self.pixel_format,
            "target_transfer": plan.target_transfer.value,
        }


def _container_name(format_data: dict[str, object]) -> str:
    format_name = str(format_data.get("format_name", ""))
    tags = format_data.get("tags")
    major_brand = str(tags.get("major_brand", "")) if isinstance(tags, dict) else ""
    if major_brand.strip().lower().startswith("qt"):
        return "mov"
    if "mp4" in format_name.split(","):
        return "mp4"
    return format_name.split(",", maxsplit=1)[0]


def _container_frames(stream: dict[str, object]) -> int | None:
    try:
        count = int(str(stream.get("nb_frames")))
    except ValueError:
        return None
    return count if count > 0 else None


def _run_ffprobe(path: Path) -> str:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        (
            "stream=codec_type,codec_name,pix_fmt,color_transfer,color_primaries,width,height,"
            "nb_frames:"
            "format=format_name,duration,size:format_tags=major_brand"
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
            timeout=_METADATA_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InvalidOutputArtifact("ffprobe failed to inspect output artifact") from exc
    if result.returncode != 0:
        raise InvalidOutputArtifact("ffprobe failed to inspect output artifact")
    if result.stderr.strip():
        raise InvalidOutputArtifact("ffprobe reported decode errors in output artifact")
    return result.stdout


def _parse_metadata(stdout: str) -> _Metadata:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise InvalidOutputArtifact("invalid ffprobe metadata") from exc
    try:
        streams = data.get("streams", [])
        if not streams:
            raise InvalidOutputArtifact("output artifact is missing video stream")
        stream = streams[0]
        format_data = data["format"]
        return _Metadata(
            codec=str(stream["codec_name"]),
            container=_container_name(format_data),
            duration_seconds=float(format_data["duration"]),
            size_bytes=int(format_data["size"]),
            pixel_format=str(stream["pix_fmt"]),
            color_transfer=stream.get("color_transfer"),
            color_primaries=stream.get("color_primaries"),
            width=int(stream["width"]),
            height=int(stream["height"]),
            container_frames=_container_frames(stream),
        )
    except InvalidOutputArtifact:
        raise
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise InvalidOutputArtifact("invalid ffprobe metadata") from exc


def _read_metadata(path: Path) -> _Metadata:
    try:
        actual_size = path.stat().st_size
    except FileNotFoundError as exc:
        raise InvalidOutputArtifact("output artifact does not exist") from exc
    if actual_size <= 0:
        raise InvalidOutputArtifact("output artifact is empty")
    return _parse_metadata(_run_ffprobe(path))


def _clock(seconds: float) -> str:
    whole = max(0, round(seconds))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _progress_values(progress_file: Path) -> dict[str, str]:
    """Return the latest value of each key ffmpeg wrote to its progress file."""
    try:
        with progress_file.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - _PROGRESS_TAIL_BYTES))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return {}
    lines = text.splitlines()
    if size > _PROGRESS_TAIL_BYTES:
        lines = lines[1:]
    values: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    return values


def _report_decode_progress(progress_file: Path, duration: float, check: DecodeCheck) -> None:
    raw = _progress_values(progress_file).get("out_time_us", "")
    decoded = int(raw) / 1_000_000 if raw.isdecimal() else 0.0
    message = f"Checking the finished film: {_clock(decoded)} of {_clock(duration)} decoded"
    logger.info(message)
    if check.progress is not None:
        check.progress(message)


def _decoded_frame_count(progress_file: Path) -> int:
    values = _progress_values(progress_file)
    raw_count = values.get("frame")
    if values.get("progress") != "end" or raw_count is None:
        raise InvalidOutputArtifact("ffmpeg is missing decoded frame evidence")
    try:
        return int(raw_count)
    except ValueError as exc:
        raise InvalidOutputArtifact("ffmpeg returned invalid decoded frame evidence") from exc


def _run_reporting(
    command: list[str], budget: float, report: Callable[[], None]
) -> subprocess.CompletedProcess[str]:
    """Run ``command`` within ``budget`` seconds, calling ``report`` while it runs."""
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="decode-check") as pool:
        running = pool.submit(
            subprocess.run,
            command,
            capture_output=True,
            text=True,
            timeout=budget,
            check=False,
        )
        while not wait([running], timeout=_DECODE_PROGRESS_INTERVAL_SECONDS).done:
            report()
        return running.result()


def _decode_timeout_message(check: DecodeCheck, budget: float) -> str:
    if check.encode_seconds is not None:
        return (
            "the decode check did not finish within the render's own encode time "
            f"({_clock(budget)})"
        )
    return f"the decode check did not finish within its {_clock(budget)} budget"


def _log_decoded(frames: int, container_frames: int | None, elapsed: float) -> None:
    summary = f"Checked the finished film in {_clock(elapsed)}: {frames} frames decoded"
    # A mismatch is worth seeing in the log, not worth failing a finished render over.
    if container_frames is not None and frames != container_frames:
        logger.warning(f"{summary}, the container lists {container_frames}")
    else:
        logger.info(summary)


def _decode_video_stream(path: Path, metadata: _Metadata, check: DecodeCheck) -> int:
    """Decode every frame of the first video stream and return how many there were.

    ffmpeg rather than ffprobe: ``ffprobe -count_frames`` decodes on one thread
    (2.25x realtime on a Celeron J4125, whatever the core count), and only
    ffmpeg can write its position to a file while it runs.
    """
    duration = metadata.duration_seconds
    budget = check.budget_seconds(duration)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="immich-memories-decode-") as scratch:
        progress_file = Path(scratch) / "progress"
        command = [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-nostats",
            "-v",
            "error",
            "-progress",
            str(progress_file),
            "-stats_period",
            _DECODE_STATS_PERIOD,
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-f",
            "null",
            "-",
        ]
        try:
            result = _run_reporting(
                command,
                budget,
                lambda: _report_decode_progress(progress_file, duration, check),
            )
        except subprocess.TimeoutExpired as exc:
            raise InvalidOutputArtifact(_decode_timeout_message(check, budget)) from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise InvalidOutputArtifact("ffmpeg failed to decode output artifact") from exc
        if result.returncode != 0:
            raise InvalidOutputArtifact("ffmpeg failed to decode output artifact")
        if result.stderr.strip():
            raise InvalidOutputArtifact("ffmpeg reported decode errors in output artifact")
        frames = _decoded_frame_count(progress_file)
    _log_decoded(frames, metadata.container_frames, time.monotonic() - started)
    return frames


def _validate_media_shape(metadata: _Metadata) -> None:
    if not math.isfinite(metadata.duration_seconds) or metadata.duration_seconds <= 0:
        raise InvalidOutputArtifact("output artifact must have a positive duration")
    if metadata.size_bytes <= 0:
        raise InvalidOutputArtifact("output artifact must have a positive size")
    if metadata.width <= 0 or metadata.height <= 0:
        raise InvalidOutputArtifact("output artifact must have a positive resolution")


def _validate_encoding_identity(metadata: _Metadata, plan: EncodingPlan) -> None:
    if metadata.container != plan.container:
        raise InvalidOutputArtifact(f"expected {plan.container}, got {metadata.container}")
    expected_decoded_format = _DECODED_PIXEL_FORMATS.get(plan.pixel_format, plan.pixel_format)
    decoded_format = _DECODED_PIXEL_FORMATS.get(metadata.pixel_format, metadata.pixel_format)
    if decoded_format != expected_decoded_format:
        raise InvalidOutputArtifact(f"expected {plan.pixel_format}, got {metadata.pixel_format}")
    expected_codec = _CODEC_NAMES[plan.codec]
    if metadata.codec != expected_codec:
        raise InvalidOutputArtifact(f"expected {expected_codec}, got {metadata.codec}")


def _validate_color_metadata(metadata: _Metadata, plan: EncodingPlan) -> None:
    transfer_label, expected_transfer = _TARGET_TRANSFERS[plan.target_transfer]
    if metadata.color_transfer != expected_transfer:
        raise InvalidOutputArtifact(
            f"expected {transfer_label} transfer {expected_transfer}, "
            f"got {metadata.color_transfer or 'missing'}"
        )
    dynamic_range = "HDR" if plan.hdr else "SDR"
    expected_primaries = "bt2020" if plan.hdr else "bt709"
    if metadata.color_primaries != expected_primaries:
        raise InvalidOutputArtifact(
            f"expected {dynamic_range} primaries {expected_primaries}, "
            f"got {metadata.color_primaries or 'missing'}"
        )


def _checked_metadata(path: Path, plan: EncodingPlan) -> _Metadata:
    metadata = _read_metadata(path)
    _validate_media_shape(metadata)
    _validate_encoding_identity(metadata, plan)
    _validate_color_metadata(metadata, plan)
    return metadata


def _probe_of(
    metadata: _Metadata, decoded_frames: int | None, stamp: FileStamp | None
) -> OutputProbe:
    return OutputProbe(
        codec=metadata.codec,
        container=metadata.container,
        duration_seconds=metadata.duration_seconds,
        size_bytes=metadata.size_bytes,
        pixel_format=metadata.pixel_format,
        color_transfer=metadata.color_transfer,
        color_primaries=metadata.color_primaries,
        width=metadata.width,
        height=metadata.height,
        decoded_frames=decoded_frames,
        stamp=stamp,
    )


def _still_verified(path: Path, verified: OutputProbe | None) -> bool:
    if verified is None or verified.stamp is None or not verified.decoded_frames:
        return False
    return verified.stamp.matches(path)


def _decoded_probe(
    path: Path, plan: EncodingPlan, check: DecodeCheck, verified: OutputProbe | None
) -> OutputProbe:
    metadata = _checked_metadata(path, plan)
    if _still_verified(path, verified):
        assert verified is not None  # _still_verified is False without it
        return _probe_of(metadata, verified.decoded_frames, verified.stamp)
    stamp = FileStamp.of(path)
    decoded_frames = _decode_video_stream(path, metadata, check)
    if decoded_frames <= 0:
        raise InvalidOutputArtifact("output artifact must have a positive decoded frame count")
    if not stamp.matches(path):
        raise InvalidOutputArtifact("output artifact changed while it was being checked")
    return _probe_of(metadata, decoded_frames, stamp)


def _naming_the_file(path: Path, check: Callable[[], OutputProbe]) -> OutputProbe:
    try:
        return check()
    except InvalidOutputArtifact as exc:
        raise InvalidOutputArtifact(f"{path}: {exc}") from exc


def check_output(path: Path, plan: EncodingPlan) -> OutputProbe:
    """Read the film's container, without decoding, and check it against its plan.

    This is the check for every step before the film's last write: a second at
    most, and the returned probe carries no decoded-frame evidence.
    """
    return _naming_the_file(path, lambda: _probe_of(_checked_metadata(path, plan), None, None))


def validate_output(
    path: Path,
    plan: EncodingPlan,
    decode_check: DecodeCheck | None = None,
    *,
    verified: OutputProbe | None = None,
) -> OutputProbe:
    """Return the film's metadata only when it matches its plan and decodes cleanly.

    The container is read first, without decoding, and checked against the plan,
    so a wrong codec fails in a second. Only then is the video stream decoded end
    to end, within the budget ``decode_check`` sets. When ``verified`` is an
    earlier decoded probe of the same bytes (same size, mtime and inode), that
    decode stands and the stream is not decoded again. Every failure names the
    file, and nothing here deletes it.
    """
    check = decode_check or DecodeCheck()
    return _naming_the_file(path, lambda: _decoded_probe(path, plan, check, verified))


def _fsync_directory(directory: Path) -> None:
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None:
        return
    try:
        directory_fd = os.open(directory, os.O_RDONLY | directory_flag)
    except OSError as exc:
        if exc.errno in _UNSUPPORTED_FSYNC_ERRNOS:
            return
        raise
    try:
        try:
            os.fsync(directory_fd)
        except OSError as exc:
            if exc.errno not in _UNSUPPORTED_FSYNC_ERRNOS:
                raise
    finally:
        os.close(directory_fd)


def publish_validated_output(
    staged_path: Path,
    final_path: Path,
    plan: EncodingPlan,
    *,
    decode_check: DecodeCheck | None = None,
    verified: OutputProbe | None = None,
) -> OutputProbe:
    """Validate a staged sibling before atomically replacing the final path.

    A staged file that fails validation stays where it is.
    """
    if staged_path == final_path:
        raise InvalidOutputArtifact("output must use a distinct staged sibling")
    if staged_path.parent != final_path.parent:
        raise InvalidOutputArtifact("output must be rendered to a staged sibling")
    expected_suffix = f".{plan.container}"
    if final_path.suffix.lower() != expected_suffix:
        raise InvalidOutputArtifact(
            f"final suffix must be {expected_suffix}, got {final_path.suffix or 'missing'}"
        )
    probe = validate_output(staged_path, plan, decode_check, verified=verified)
    os.replace(staged_path, final_path)
    _fsync_directory(final_path.parent)
    return probe
