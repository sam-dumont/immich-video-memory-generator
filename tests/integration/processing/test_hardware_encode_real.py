"""The encoder this host actually detected has to survive a real assembly.

Run with: make test-integration-processing

On a VAAPI or QSV box these are the tests that would have caught #782: detection
said the device could encode, and every render failed because the command it
built had neither a device nor an upload. Everywhere else they still prove the
detected backend's real command runs, which is the same contract.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.integration.conftest import ffprobe_json, requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]

_CODEC_STREAM_NAMES = {"h264": {"h264"}, "h265": {"hevc"}}


def _detected_plan(codec: str):
    from immich_memories.processing.encoding_plan import (
        EncodingRequest,
        HdrMode,
        OutputCodec,
        resolve_encoding_plan,
    )
    from immich_memories.processing.hardware import detect_hardware_acceleration

    request = EncodingRequest(
        codec=OutputCodec(codec),
        hdr_mode=HdrMode.SDR,
        hardware_enabled=True,
        preset="fast",
        crf=23,
        container="mp4",
    )
    return resolve_encoding_plan(request, detect_hardware_acceleration(), input_has_hdr=False)


def _assemble(plan, output: Path) -> None:
    from unittest.mock import MagicMock

    from immich_memories.processing.assembly_config import AssemblyClip, AssemblySettings
    from immich_memories.processing.clip_encoder import ClipEncoder
    from immich_memories.processing.ffmpeg_runner import AssemblyContext

    settings = AssemblySettings(encoding_plan=plan)
    # WHY: duration estimation would ffprobe a synthetic lavfi source that has no file.
    prober = MagicMock()
    prober.estimate_duration.return_value = 2.0
    encoder = ClipEncoder(settings, prober, lambda _path: None)
    context = AssemblyContext(
        target_w=640,
        target_h=360,
        pix_fmt="yuv420p",
        hdr_type="sdr",
        clip_hdr_types=[None],
        clip_primaries=[None],
        colorspace_filter="",
        target_fps=30,
        fade_duration=0.0,
    )
    result = encoder.run_ffmpeg_assembly(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=30:duration=2",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo:d=2",
        ],
        "[0:v]null[vout];[1:a]anull[aout]",
        "[vout]",
        "[aout]",
        output,
        [AssemblyClip(path=output, duration=2.0)],
        context,
    )
    assert result.returncode == 0, result.stderr[-2000:]


@pytest.mark.parametrize("codec", ["h264", "h265"])
def test_the_detected_encoder_assembles_a_playable_file(tmp_path: Path, codec: str) -> None:
    plan = _detected_plan(codec)
    output = tmp_path / f"{codec}.mp4"

    _assemble(plan, output)

    streams = ffprobe_json(output)["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    assert video["codec_name"] in _CODEC_STREAM_NAMES[codec]


def test_a_device_encoder_is_never_handed_software_frames(tmp_path: Path) -> None:
    """The exact asymmetry of #782, asserted against the detected backend."""
    from immich_memories.processing.hardware_encode import apply_hardware_encode, encoder_backend

    plan = _detected_plan("h264")
    backend = encoder_backend(plan.encoder)
    if backend is None:
        pytest.skip(f"{plan.encoder} takes software frames; nothing to upload")

    cmd = apply_hardware_encode(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=30:duration=1",
            "-vf",
            "null",
            "-c:v",
            plan.encoder,
            *plan.encoder_args,
            str(tmp_path / "probe.mp4"),
        ],
        pixel_format=plan.pixel_format,
    )

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
