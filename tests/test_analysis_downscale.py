"""The analysis downscale must survive a hardware decoder that does not work.

Hardware acceleration is detected from what ffmpeg advertises, which is not the
same as what actually decodes on this machine: a VAAPI node can exist without a
usable driver, and a CUDA build can be present with no GPU attached. When that
happens ffmpeg exits non-zero, and a downscale that gave up would push the whole
analysis onto the 4K original -- the exact cost the acceleration was added to
avoid.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from immich_memories.cache import video_cache as video_cache_module
from immich_memories.cache.video_cache import (
    VideoDownloadCache,
    analysis_decode_hwaccel_args,
    downscale_for_analysis,
)
from immich_memories.processing.hardware import HWAccelBackend, HWAccelCapabilities


@pytest.fixture(scope="module")
def source_video(tmp_path_factory) -> Path:
    """A real 720p clip, so ffmpeg does real work."""
    path = tmp_path_factory.mktemp("downscale") / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=1280x720:rate=10:duration=1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return path


def _height(path: Path) -> int:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=height",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return int(out.stdout.strip().rstrip(","))


def test_downscales_to_the_target_height(source_video: Path, tmp_path: Path):
    dest = tmp_path / "out.mp4"

    assert downscale_for_analysis(source_video, dest, 480, stream_map="0:v:0") is True
    assert _height(dest) == 480


def test_falls_back_to_software_when_the_hardware_decoder_fails(source_video: Path, tmp_path: Path):
    dest = tmp_path / "out.mp4"

    ok = downscale_for_analysis(
        source_video,
        dest,
        480,
        stream_map="0:v:0",
        hwaccel_args=["-hwaccel", "definitely_not_a_real_hwaccel"],
    )

    assert ok is True, "a broken hardware decoder must not cost us the downscale"
    assert _height(dest) == 480


def test_reports_failure_when_the_source_is_not_decodable(tmp_path: Path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    dest = tmp_path / "out.mp4"

    assert downscale_for_analysis(broken, dest, 480, stream_map="0:v:0") is False
    assert not dest.exists()


class TestDecodeArgsForTheDownscale:
    @staticmethod
    def _pretend(monkeypatch, capabilities: HWAccelCapabilities) -> None:
        # WHY: detection shells out to ffmpeg and reports whatever this machine
        # has, which is not something a test can assert against.
        monkeypatch.setattr(video_cache_module, "_detected_capabilities", lambda: capabilities)

    def test_hevc_source_asks_for_the_h265_decoder(self, monkeypatch):
        self._pretend(
            monkeypatch,
            HWAccelCapabilities(backend=HWAccelBackend.NVIDIA, supports_h265_decode=True),
        )

        assert analysis_decode_hwaccel_args("hevc") == ["-hwaccel", "cuda"]

    def test_no_decoder_means_no_args(self, monkeypatch):
        self._pretend(monkeypatch, HWAccelCapabilities(backend=HWAccelBackend.NONE))

        assert analysis_decode_hwaccel_args("h264") == []

    def test_a_probeable_source_yields_this_machine_s_decoder(
        self, source_video: Path, tmp_path: Path
    ):
        cache = VideoDownloadCache(cache_dir=tmp_path / "cache")

        args = cache._downscale_hwaccel_args(source_video)

        # Whether this machine has a decoder is not something a test can assert.
        assert args == [] or args[0] == "-hwaccel"

    def test_an_unprobeable_source_is_decoded_in_software(self, tmp_path: Path):
        """No codec means no informed choice of decoder, so do not guess one."""
        cache = VideoDownloadCache(cache_dir=tmp_path / "cache")
        not_a_video = tmp_path / "junk.mp4"
        not_a_video.write_bytes(b"junk")

        assert cache._downscale_hwaccel_args(not_a_video) == []


def test_downscale_reports_failure_when_ffmpeg_cannot_be_run(
    source_video: Path, tmp_path: Path, monkeypatch
):
    """A missing or unexecutable ffmpeg must not raise through the cache."""

    def explode(*_args, **_kwargs):
        raise OSError("ffmpeg not found")

    # WHY: stands in for ffmpeg being absent from PATH.
    monkeypatch.setattr(video_cache_module.subprocess, "run", explode)

    assert (
        downscale_for_analysis(source_video, tmp_path / "o.mp4", 480, stream_map="0:v:0") is False
    )
