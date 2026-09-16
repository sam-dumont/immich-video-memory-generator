"""Lossless camera cuts keep a container that can carry their original streams."""

import subprocess

import pytest

from immich_memories.config import Config
from immich_memories.processing.clips import ClipExtractor, ClipSegment, extract_clip
from immich_memories.processing.probe_cache import ProbeCache
from tests.integration.conftest import requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


@pytest.mark.parametrize("entry_point", ["function", "extractor"])
def test_a_prores_mov_cut_preserves_hdr_and_pcm_audio(tmp_path, entry_point):
    source = tmp_path / "camera.MOV"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=30:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=2",
            "-vf", "setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc",
            "-c:v", "prores_ks", "-profile:v", "1", "-pix_fmt", "yuv422p10le",
            "-color_primaries", "bt2020", "-colorspace", "bt2020nc",
            "-color_trc", "arib-std-b67", "-c:a", "pcm_s16le", "-ac", "2",
            str(source),
        ],
        check=True,
    )  # fmt: skip
    config = Config()
    assert ProbeCache().get(source).hdr_type == "hlg"

    if entry_point == "function":
        cut = extract_clip(source, 0.5, 1.5, config=config)
    else:
        cut = ClipExtractor(tmp_path / "cuts", config=config).extract(
            ClipSegment(source, 0.5, 1.5, "camera")
        )

    probe = ProbeCache().get(cut)
    assert cut.suffix == ".mov"
    assert probe.codec == "prores"
    assert probe.hdr_type == "hlg"
    assert probe.audio_codec == "pcm_s16le"
    assert probe.video_duration_seconds == pytest.approx(1.0, abs=1 / 30)
