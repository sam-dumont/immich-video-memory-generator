"""Device setup a VAAPI/QSV encode needs and every other encode must not get.

`h264_vaapi`, `hevc_vaapi` and the QSV encoders refuse frames that live in
system memory: they need an initialised device and an explicit `hwupload` at
the end of the filter chain. NVENC and VideoToolbox take software frames, which
is why only these two backends ever needed this module.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class HardwareChainUnavailable(RuntimeError):
    """A hardware encode command cannot be given frames on a device."""


# Named devices, so the filter chain and the encoder agree on which one to use.
_DEVICE_ARGS: dict[str, tuple[str, ...]] = {
    "vaapi": ("-init_hw_device", "vaapi=va", "-filter_hw_device", "va"),
    "qsv": ("-init_hw_device", "qsv=hw", "-filter_hw_device", "hw"),
}

# The software format the upload reads from. Uploading straight from yuv420p
# lets FFmpeg pick the surface format, and it picks 8-bit — which would quietly
# throw away the 10 bits an HDR plan asked for.
_UPLOAD_SOURCE_FORMAT: dict[str, str] = {
    "yuv420p": "nv12",
    "nv12": "nv12",
    "p010le": "p010le",
    "yuv420p10le": "p010le",
}

_VIDEO_ENCODER_FLAGS = ("-c:v", "-codec:v", "-vcodec")
_SIMPLE_FILTER_FLAGS = ("-vf", "-filter:v")


def encoder_backend(encoder: str) -> str | None:
    """Backend name for encoders that reject frames in system memory."""
    for backend in _DEVICE_ARGS:
        if encoder.endswith(f"_{backend}"):
            return backend
    return None


def upload_filter(backend: str, pixel_format: str) -> str:
    """Filter chain that moves software frames onto the backend's device."""
    source = _UPLOAD_SOURCE_FORMAT.get(pixel_format)
    if source is None:
        raise HardwareChainUnavailable(
            f"{backend} has no surface format for pixel format {pixel_format!r}"
        )
    if backend == "qsv":
        return f"format={source},hwupload=extra_hw_frames=8,format=qsv"
    return f"format={source},hwupload"


def device_args(backend: str) -> list[str]:
    """Global FFmpeg options that create the device the upload targets."""
    return list(_DEVICE_ARGS[backend])


def _video_encoder(cmd: list[str]) -> str | None:
    for flag in _VIDEO_ENCODER_FLAGS:
        if flag in cmd:
            index = cmd.index(flag) + 1
            if index < len(cmd):
                return cmd[index]
    return None


def _with_device_args(cmd: list[str], backend: str) -> list[str]:
    # Declaring the same device name twice is a hard FFmpeg error, and a command
    # can reach here already carrying its device when a caller builds once and
    # applies again, so adding them is conditional.
    if "-init_hw_device" in cmd:
        return cmd
    # `-init_hw_device` and `-filter_hw_device` are global options wherever they
    # appear, but keeping them ahead of every other flag keeps commands readable
    # in the logs, and keeps a multi-word launcher prefix intact.
    start = next((i for i, arg in enumerate(cmd) if arg.startswith("-")), len(cmd))
    return [*cmd[:start], *device_args(backend), *cmd[start:]]


def _extend_simple_filter(cmd: list[str], flag: str, upload: str) -> list[str]:
    index = cmd.index(flag) + 1
    chain = cmd[index]
    if upload not in chain:
        cmd[index] = f"{chain},{upload}" if chain else upload
    return cmd


def _extend_complex_graph(cmd: list[str], upload: str, video_label: str | None) -> list[str]:
    if video_label is None:
        raise HardwareChainUnavailable(
            "a complex filtergraph gives no video output label to upload from"
        )
    label = video_label.strip("[]")
    graph_index = cmd.index("-filter_complex") + 1
    if upload in cmd[graph_index]:
        return cmd
    map_index = next(
        (i for i, arg in enumerate(cmd) if cmd[i - 1] == "-map" and arg.strip("[]") == label),
        None,
    )
    if map_index is None:
        raise HardwareChainUnavailable(f"video label {video_label!r} is not mapped to an output")
    uploaded = f"{label}_hw"
    cmd[graph_index] = f"{cmd[graph_index]};[{label}]{upload}[{uploaded}]"
    cmd[map_index] = f"[{uploaded}]"
    return cmd


def _insert_simple_filter(cmd: list[str], upload: str) -> list[str]:
    for flag in _VIDEO_ENCODER_FLAGS:
        if flag in cmd:
            index = cmd.index(flag)
            return [*cmd[:index], "-vf", upload, *cmd[index:]]
    raise HardwareChainUnavailable("command selects no video encoder")


def apply_hardware_encode(
    cmd: list[str],
    *,
    pixel_format: str = "yuv420p",
    video_label: str | None = None,
) -> list[str]:
    """Give a built FFmpeg command the device and the upload its encoder needs.

    Returns the command unchanged when its video encoder takes software frames.
    Raises `HardwareChainUnavailable` when the chain cannot be formed, so the
    caller falls back to software rather than running a command that cannot work.
    """
    encoder = _video_encoder(cmd)
    backend = encoder_backend(encoder) if encoder else None
    if backend is None:
        return cmd.copy()

    upload = upload_filter(backend, pixel_format)
    built = cmd.copy()
    for flag in _SIMPLE_FILTER_FLAGS:
        if flag in built:
            return _with_device_args(_extend_simple_filter(built, flag, upload), backend)
    if "-filter_complex" in built:
        return _with_device_args(_extend_complex_graph(built, upload, video_label), backend)
    return _with_device_args(_insert_simple_filter(built, upload), backend)
