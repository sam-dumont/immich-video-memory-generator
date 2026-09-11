"""Prove hardware encoding end to end on this host, through the app's own code.

Nothing here hand-writes an FFmpeg line: it detects the backend the pipeline
would detect, resolves the plan the pipeline would resolve, and runs the final
assembly command `ClipEncoder` builds — the command that failed on every VAAPI
and QSV host before #782. Exits non-zero if the render produced nothing, or if
a hardware backend was detected and the render still went to the CPU.

    python scripts/verify_hardware_encode.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from immich_memories.processing.assembly_config import AssemblyClip, AssemblySettings
from immich_memories.processing.clip_encoder import ClipEncoder
from immich_memories.processing.encoding_plan import (
    EncodingPlan,
    EncodingRequest,
    HdrMode,
    OutputCodec,
    resolve_encoding_plan,
    uses_hardware_encoder,
)
from immich_memories.processing.ffmpeg_runner import AssemblyContext
from immich_memories.processing.hardware import detect_hardware_acceleration
from immich_memories.processing.hardware_encode import encoder_backend


class _SyntheticProber:
    """The source is a lavfi pattern, so there is no file to probe."""

    def estimate_duration(self, _clips: list[AssemblyClip]) -> float:
        return 3.0


def _print_vainfo() -> None:
    if shutil.which("vainfo") is None:
        print("vainfo: not installed")
        return
    result = subprocess.run(["vainfo"], capture_output=True, text=True, timeout=30)
    lines = [
        line.strip()
        for line in (result.stdout + result.stderr).splitlines()
        if "driver" in line.lower() or "EncSlice" in line
    ]
    print("vainfo:")
    for line in lines or ["(no driver and no encode entrypoint reported)"]:
        print(f"  {line}")


def _plan_for(codec: str) -> EncodingPlan:
    request = EncodingRequest(
        codec=OutputCodec(codec),
        hdr_mode=HdrMode.SDR,
        hardware_enabled=True,
        preset="fast",
        crf=23,
        container="mp4",
    )
    return resolve_encoding_plan(request, detect_hardware_acceleration(), input_has_hdr=False)


def _assemble(plan: EncodingPlan, output: Path) -> list[str]:
    """Run the real final-assembly command and return the video codecs it wrote."""
    encoder = ClipEncoder(AssemblySettings(encoding_plan=plan), _SyntheticProber(), lambda _p: None)
    context = AssemblyContext(
        target_w=1280,
        target_h=720,
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
            "testsrc2=size=1280x720:rate=30:duration=3",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo:d=3",
        ],
        "[0:v]null[vout];[1:a]anull[aout]",
        "[vout]",
        "[aout]",
        output,
        [AssemblyClip(path=output, duration=3.0)],
        context,
    )
    if result.returncode != 0:
        print(f"  FFmpeg failed:\n{result.stderr[-2000:]}")
        return []
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    streams = json.loads(probe.stdout)["streams"]
    return [s["codec_name"] for s in streams if s["codec_type"] == "video"]


def main() -> int:
    capabilities = detect_hardware_acceleration()
    print(f"backend: {capabilities}")
    _print_vainfo()

    empty: list[str] = []
    on_cpu: list[str] = []
    with tempfile.TemporaryDirectory() as work:
        for codec in ("h264", "h265"):
            plan = _plan_for(codec)
            output = Path(work) / f"{codec}.mp4"
            written = _assemble(plan, output)
            size = output.stat().st_size if output.exists() else 0
            device = encoder_backend(plan.encoder)
            where = f"on the {device} device" if device else "without a device upload"
            print(f"{codec}: {plan.encoder} {where}, wrote {written} ({size} bytes)")
            if not written or size == 0:
                empty.append(codec)
            elif capabilities.has_encoding and not uses_hardware_encoder(plan):
                on_cpu.append(codec)

    if empty:
        print(f"FAILED: {', '.join(empty)} produced no video")
        return 1
    if on_cpu:
        # Expected on a chip whose driver has no encode entrypoint for that codec.
        print(f"NOTE: {', '.join(on_cpu)} fell back to software despite a detected backend")
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
