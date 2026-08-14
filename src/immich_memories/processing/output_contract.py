"""Probe, validate, and atomically publish finished video artifacts."""

from __future__ import annotations

import errno
import json
import math
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec

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
}
# WHY: frame counting decodes the whole memory; supported presets run up to ten minutes.
_FULL_DECODE_TIMEOUT_SECONDS = 15 * 60
_UNSUPPORTED_FSYNC_ERRNOS = frozenset(
    {errno.EINVAL, getattr(errno, "ENOSYS", errno.EINVAL), getattr(errno, "ENOTSUP", errno.EINVAL)}
)


class InvalidOutputArtifact(ValueError):
    """The rendered file does not satisfy its resolved encoding plan."""


@dataclass(frozen=True, slots=True)
class OutputProbe:
    """Normalized metadata read from one final-output ffprobe invocation."""

    codec: str
    container: str
    duration_seconds: float
    size_bytes: int
    pixel_format: str
    color_transfer: str | None
    color_primaries: str | None
    width: int
    height: int
    decoded_frames: int

    def render_metrics(self, plan: EncodingPlan) -> dict[str, object]:
        """Describe the effective render contract with validated artifact facts."""
        return {
            "output_width": self.width,
            "output_height": self.height,
            "codec": plan.codec.value,
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


def _decoded_frame_count(stream: dict[str, object]) -> int:
    raw_count = stream.get("nb_read_frames")
    if raw_count in {None, "N/A"}:
        raise InvalidOutputArtifact("ffprobe is missing decoded frame evidence")
    try:
        return int(str(raw_count))
    except ValueError as exc:
        raise InvalidOutputArtifact("ffprobe returned invalid decoded frame evidence") from exc


def _run_ffprobe(path: Path) -> str:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-count_frames",
        "-show_entries",
        (
            "stream=codec_type,codec_name,pix_fmt,color_transfer,color_primaries,width,height,"
            "nb_read_frames:"
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
            timeout=_FULL_DECODE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InvalidOutputArtifact("ffprobe failed to inspect output artifact") from exc
    if result.returncode != 0:
        raise InvalidOutputArtifact("ffprobe failed to inspect output artifact")
    if result.stderr.strip():
        raise InvalidOutputArtifact("ffprobe reported decode errors in output artifact")
    return result.stdout


def _parse_probe(stdout: str) -> OutputProbe:
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
        return OutputProbe(
            codec=str(stream["codec_name"]),
            container=_container_name(format_data),
            duration_seconds=float(format_data["duration"]),
            size_bytes=int(format_data["size"]),
            pixel_format=str(stream["pix_fmt"]),
            color_transfer=stream.get("color_transfer"),
            color_primaries=stream.get("color_primaries"),
            width=int(stream["width"]),
            height=int(stream["height"]),
            decoded_frames=_decoded_frame_count(stream),
        )
    except InvalidOutputArtifact:
        raise
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise InvalidOutputArtifact("invalid ffprobe metadata") from exc


def probe_output(path: Path) -> OutputProbe:
    """Read the final video stream and container metadata in one ffprobe call."""
    try:
        actual_size = path.stat().st_size
    except FileNotFoundError as exc:
        raise InvalidOutputArtifact("output artifact does not exist") from exc
    if actual_size <= 0:
        raise InvalidOutputArtifact("output artifact is empty")
    return _parse_probe(_run_ffprobe(path))


def _validate_media_shape(probe: OutputProbe) -> None:
    if not math.isfinite(probe.duration_seconds) or probe.duration_seconds <= 0:
        raise InvalidOutputArtifact("output artifact must have a positive duration")
    if probe.size_bytes <= 0:
        raise InvalidOutputArtifact("output artifact must have a positive size")
    if probe.width <= 0 or probe.height <= 0:
        raise InvalidOutputArtifact("output artifact must have a positive resolution")
    if probe.decoded_frames <= 0:
        raise InvalidOutputArtifact("output artifact must have a positive decoded frame count")


def _validate_encoding_identity(probe: OutputProbe, plan: EncodingPlan) -> None:
    if probe.container != plan.container:
        raise InvalidOutputArtifact(f"expected {plan.container}, got {probe.container}")
    expected_decoded_format = _DECODED_PIXEL_FORMATS.get(plan.pixel_format, plan.pixel_format)
    if probe.pixel_format != expected_decoded_format:
        raise InvalidOutputArtifact(f"expected {plan.pixel_format}, got {probe.pixel_format}")
    expected_codec = _CODEC_NAMES[plan.codec]
    if probe.codec != expected_codec:
        raise InvalidOutputArtifact(f"expected {expected_codec}, got {probe.codec}")


def _validate_color_metadata(probe: OutputProbe, plan: EncodingPlan) -> None:
    transfer_label, expected_transfer = _TARGET_TRANSFERS[plan.target_transfer]
    if probe.color_transfer != expected_transfer:
        raise InvalidOutputArtifact(
            f"expected {transfer_label} transfer {expected_transfer}, "
            f"got {probe.color_transfer or 'missing'}"
        )
    dynamic_range = "HDR" if plan.hdr else "SDR"
    expected_primaries = "bt2020" if plan.hdr else "bt709"
    if probe.color_primaries != expected_primaries:
        raise InvalidOutputArtifact(
            f"expected {dynamic_range} primaries {expected_primaries}, "
            f"got {probe.color_primaries or 'missing'}"
        )


def validate_output(path: Path, plan: EncodingPlan) -> OutputProbe:
    """Return normalized metadata only when the artifact matches its plan."""
    probe = probe_output(path)
    _validate_media_shape(probe)
    _validate_encoding_identity(probe, plan)
    _validate_color_metadata(probe, plan)
    return probe


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
) -> OutputProbe:
    """Validate a staged sibling before atomically replacing the final path."""
    if staged_path == final_path:
        raise InvalidOutputArtifact("output must use a distinct staged sibling")
    if staged_path.parent != final_path.parent:
        raise InvalidOutputArtifact("output must be rendered to a staged sibling")
    expected_suffix = f".{plan.container}"
    if final_path.suffix.lower() != expected_suffix:
        raise InvalidOutputArtifact(
            f"final suffix must be {expected_suffix}, got {final_path.suffix or 'missing'}"
        )
    probe = validate_output(staged_path, plan)
    os.replace(staged_path, final_path)
    _fsync_directory(final_path.parent)
    return probe


def publish_output_metrics(
    staged_path: Path,
    final_path: Path,
    plan: EncodingPlan,
) -> dict[str, object]:
    """Publish a validated artifact and describe the effective render contract."""
    return publish_validated_output(staged_path, final_path, plan).render_metrics(plan)
