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


def output_tools(
    probe_payload: dict[str, object],
    *,
    decoded_frames: int | None = None,
    decode_stderr: str = "",
    calls: list[tuple[list[str], dict[str, object]]] | None = None,
) -> RunFn:
    """Answer ffprobe with ``probe_payload`` and an ffmpeg decode with a frame count."""

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if calls is not None:
            calls.append((command, kwargs))
        if is_decode_check(command):
            frames = _payload_frames(probe_payload) if decoded_frames is None else decoded_frames
            write_decode_progress(command, frames)
            return subprocess.CompletedProcess(command, 0, "", decode_stderr)
        return subprocess.CompletedProcess(command, 0, json.dumps(probe_payload), "")

    return run
