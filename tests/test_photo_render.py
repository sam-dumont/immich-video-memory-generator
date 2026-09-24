"""A photograph becomes a clip: PQ when FFmpeg can convert it, plain SDR when it cannot."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from immich_memories.config_models_render import PhotoConfig
from immich_memories.photos.photo_pipeline import render_single_photo
from tests.conftest import make_asset
from tests.test_photo_format_dispatch import _rotated_ultrahdr_jpeg

CONFIG = PhotoConfig(duration=1.0)


def _sdr_photo(tmp_path: Path) -> Path:
    path = tmp_path / "photo.jpg"
    Image.new("RGB", (96, 64), "orange").save(path, "JPEG")
    return path


def _gain_mapped_photo(tmp_path: Path) -> Path:
    path = tmp_path / "hdr.jpg"
    _rotated_ultrahdr_jpeg(path)
    return path


def _render(tmp_path: Path, source: Path):
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    download = MagicMock()  # WHY: the source is local, so Immich must never be asked
    clip = render_single_photo(
        make_asset("photo-1"), CONFIG, 96, 64, work, download, fps=5, source_path=source
    )
    download.assert_not_called()
    return clip


class _FFmpeg:
    """WHY: FFmpeg is the boundary; this records the command and answers like it."""

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.command: list[str] = []
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, command, frames, **_):
        self.command = command
        for _frame in frames:
            pass
        if self.returncode == 0:
            Path(command[-1]).write_bytes(b"\0" * 200)
        return self.returncode, self.stderr


def _command(tmp_path, source, *, zscale: bool, encoders: str = "libx265") -> list[str]:
    ffmpeg = _FFmpeg()
    # WHY: three FFmpeg boundaries: the encode, and the two capability probes
    with (
        # WHY: the encode itself; the command is what is under test
        patch("immich_memories.photos.photo_pipeline.write_frames_to_ffmpeg", ffmpeg),
        # WHY: which filters the installed FFmpeg has decides the route
        patch("immich_memories.processing.hdr_utilities.check_zscale_available", lambda: zscale),
        # WHY: which encoders the installed FFmpeg has decides the codec
        patch(
            "immich_memories.photos.photo_pipeline.subprocess.run",
            return_value=MagicMock(stdout=encoders),
        ),
    ):
        clip = _render(tmp_path, source)
    assert clip is not None
    assert clip.is_photo
    assert clip.duration == CONFIG.duration
    return ffmpeg.command


def test_a_photo_is_piped_8_bit_and_leaves_as_pq(tmp_path):
    command = _command(tmp_path, _sdr_photo(tmp_path), zscale=True)

    assert command[command.index("-pix_fmt") + 1] == "rgb24"
    assert "zscale=t=smpte2084:tin=iec61966-2-1" in command[command.index("-vf") + 1]
    assert command[command.index("-color_trc") + 1] == "smpte2084"


def test_a_gain_mapped_photo_is_piped_16_bit_linear(tmp_path):
    command = _command(tmp_path, _gain_mapped_photo(tmp_path), zscale=True)

    assert command[command.index("-pix_fmt") + 1] == "rgb48le"
    assert "tin=linear" in command[command.index("-vf") + 1]


@pytest.mark.parametrize("source", [_sdr_photo, _gain_mapped_photo])
def test_without_zscale_every_photo_is_plain_sdr_h264(tmp_path, source):
    """Tagging unconverted pixels as HDR would be worse than an honest SDR clip."""
    command = _command(tmp_path, source(tmp_path), zscale=False)

    assert command[command.index("-pix_fmt") + 1] == "rgb24"
    assert command[command.index("-vf") + 1] == "format=yuv420p"
    assert command[command.index("-c:v") + 1] == "libx264"
    assert not any("zscale" in part or "bt2020" in part for part in command[:-1])


@pytest.mark.parametrize(
    ("encoders", "codec"),
    [("hevc_videotoolbox libx265", "hevc_videotoolbox"), ("libx264 libx265", "libx265")],
)
def test_the_hevc_encoder_is_the_hardware_one_when_ffmpeg_has_it(tmp_path, encoders, codec):
    command = _command(tmp_path, _sdr_photo(tmp_path), zscale=True, encoders=encoders)

    assert command[command.index("-c:v") + 1] == codec


def test_a_failed_encode_raises_with_what_ffmpeg_said(tmp_path):
    # WHY: FFmpeg failing mid-encode is the case under test
    with (
        # WHY: an FFmpeg that exits non-zero with a message on stderr
        patch(
            "immich_memories.photos.photo_pipeline.write_frames_to_ffmpeg",
            _FFmpeg(returncode=1, stderr="Error: codec not found"),
        ),
        pytest.raises(RuntimeError, match="codec not found"),
    ):
        _render(tmp_path, _sdr_photo(tmp_path))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_a_real_render_is_a_clip_of_the_configured_length(tmp_path):
    clip = _render(tmp_path, _sdr_photo(tmp_path))

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json",
         str(clip.path)],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    assert float(json.loads(probe.stdout)["format"]["duration"]) == pytest.approx(1.0, abs=0.1)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_an_ffmpeg_without_zscale_still_renders_the_photo(tmp_path):
    """Homebrew's FFmpeg ships without libzimg; the photo must come out SDR, not crash."""
    real_run = subprocess.run

    def ffmpeg_without_zscale(command, *args, **kwargs):
        if "-filters" in command:
            return subprocess.CompletedProcess(command, 0, stdout=" ... scale  V->V  Scale\n")
        return real_run(command, *args, **kwargs)

    # WHY: FFmpeg's filter list is the boundary; everything else runs for real
    with patch("immich_memories.processing.hdr_utilities.subprocess.run", ffmpeg_without_zscale):
        clip = _render(tmp_path, _sdr_photo(tmp_path))

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=codec_name,pix_fmt,color_transfer", "-of", "json", str(clip.path)],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream["codec_name"] == "h264"
    assert stream["pix_fmt"] == "yuv420p"
    assert stream.get("color_transfer") != "smpte2084"
