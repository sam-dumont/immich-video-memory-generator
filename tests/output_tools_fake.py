"""A stand-in for the two FFmpeg tools the output contract runs.

The contract reads a film twice: ffprobe prints its metadata as JSON without
decoding anything, then ffmpeg decodes the video stream into the null muxer and
writes its frame count to the file named after ``-progress``. Tests that only
care about the metadata can hand over the ffprobe payload and let the decode
report the frame count the payload carries.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

RunFn = Callable[..., subprocess.CompletedProcess[str]]


def progress_file_of(command: list[str]) -> Path:
    """Return the file an ffmpeg decode command writes its progress to."""
    return Path(command[command.index("-progress") + 1])


def is_decode_check(command: list[str]) -> bool:
    return bool(command) and command[0] == "ffmpeg" and "-progress" in command


def _payload_frames(payload: dict[str, object]) -> int:
    streams = payload.get("streams")
    if not isinstance(streams, list) or not streams:
        return 0
    stream = streams[0]
    raw = stream.get("nb_read_frames", stream.get("nb_frames", "0"))
    return int(str(raw))


def write_decode_progress(
    command: list[str], frames: int, *, finished: bool = True, out_time_us: int = 0
) -> None:
    """Write what ffmpeg's ``-progress`` leaves behind after decoding ``frames``."""
    ending = "end" if finished else "continue"
    progress_file_of(command).write_text(
        f"frame={frames}\nout_time_us={out_time_us}\nprogress={ending}\n",
        encoding="utf-8",
    )


def decoded_file_of(command: list[str]) -> Path:
    """Return the film an ffmpeg decode command reads."""
    return Path(command[command.index("-i") + 1])


_AUDIO_PAYLOAD = {"streams": [{"codec_type": "audio", "nb_read_frames": "240"}]}


def payload_of(probe: object) -> dict[str, object]:
    """The ffprobe answer that the output contract reads back as ``probe``."""
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": probe.codec,
                "pix_fmt": probe.pixel_format,
                "color_transfer": probe.color_transfer,
                "color_primaries": probe.color_primaries,
                "width": probe.width,
                "height": probe.height,
                "nb_frames": str(probe.decoded_frames),
            }
        ],
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": str(probe.duration_seconds),
            "size": str(probe.size_bytes),
            "tags": {"major_brand": "qt  " if probe.container == "mov" else "isom"},
        },
    }


def output_tools(
    probe_payload: dict[str, object],
    *,
    decoded_frames: int | None = None,
    decode_stderr: str = "",
    calls: list[tuple[list[str], dict[str, object]]] | None = None,
) -> RunFn:
    """Answer ffprobe with ``probe_payload`` and an ffmpeg decode with a frame count.

    An ffprobe of the audio stream, which the music mix runs, gets one decoded
    audio stream back.
    """

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if calls is not None:
            calls.append((command, kwargs))
        if command[0] == "ffprobe" and "a:0" in command:
            return subprocess.CompletedProcess(command, 0, json.dumps(_AUDIO_PAYLOAD), "")
        if is_decode_check(command):
            frames = _payload_frames(probe_payload) if decoded_frames is None else decoded_frames
            write_decode_progress(command, frames)
            return subprocess.CompletedProcess(command, 0, "", decode_stderr)
        return subprocess.CompletedProcess(command, 0, json.dumps(probe_payload), "")

    return run
