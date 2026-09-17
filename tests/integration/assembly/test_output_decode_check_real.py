"""A real finished film goes through the metadata probe and the full decode check."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec
from tests.integration.conftest import ffprobe_json, requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


def _write_film(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1920x1080:rate=30:duration=2",
            "-vf",
            "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
            "-movflags",
            "+faststart",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )


def _h264_plan() -> EncodingPlan:
    return EncodingPlan(
        codec=OutputCodec.H264,
        encoder="libx264",
        encoder_args=("-c:v", "libx264"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv420p",
        container="mp4",
    )


def test_a_real_two_second_film_passes_both_probes_and_reports_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import DecodeCheck, publish_validated_output

    staged = tmp_path / "memory.assembling.mp4"
    final = tmp_path / "memory.mp4"
    _write_film(staged)
    container_frames = int(ffprobe_json(staged)["streams"][0]["nb_frames"])
    heard: list[str] = []
    # WHY: a two-second decode finishes long before a minute; poll often enough to hear it.
    monkeypatch.setattr(output_contract, "_DECODE_PROGRESS_INTERVAL_SECONDS", 0.005)

    probe = publish_validated_output(
        staged,
        final,
        _h264_plan(),
        decode_check=DecodeCheck(encode_seconds=1.0, progress=heard.append),
    )

    assert final.is_file()
    assert not staged.exists()
    assert (probe.codec, probe.container, probe.width, probe.height) == ("h264", "mp4", 1920, 1080)
    assert probe.duration_seconds == pytest.approx(2.0, abs=0.05)
    assert probe.decoded_frames == container_frames == 60
    assert heard
    assert all(line.startswith("Checking the finished film: ") for line in heard)
    assert all(line.endswith(" of 0:02 decoded") for line in heard)


def test_a_real_truncated_film_fails_the_decode_check_and_stays_on_disk(tmp_path: Path) -> None:
    from immich_memories.processing.output_contract import (
        InvalidOutputArtifact,
        publish_validated_output,
    )

    staged = tmp_path / "memory.assembling.mp4"
    final = tmp_path / "memory.mp4"
    _write_film(staged)
    intact = staged.read_bytes()
    staged.write_bytes(intact[: len(intact) * 3 // 4])

    with pytest.raises(InvalidOutputArtifact, match="decode errors") as caught:
        publish_validated_output(staged, final, _h264_plan())

    assert str(staged) in str(caught.value)
    assert staged.stat().st_size == len(intact) * 3 // 4
    assert not final.exists()
