"""Live Photo burst alignment reads the clips' own audio, on a default install.

Run with: make test-integration-processing
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.integration.conftest import requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


def _clip(source: Path, start: float, seconds: float, dest: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-ss", str(start), "-t", str(seconds), "-i", str(source),
            "-c:a", "aac", "-b:a", "128k", str(dest),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )  # fmt: skip
    return dest


def test_the_second_clip_is_placed_where_its_audio_is_not_where_its_shutter_says(tmp_path):
    from immich_memories.processing.live_photo_merger import align_clips_spectrogram

    # A chirp is unique at every instant, so there is exactly one right answer.
    scene = tmp_path / "scene.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", "aevalsrc=sin(2*PI*(200*t+300*t*t)):s=48000:d=6",
            str(scene),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )  # fmt: skip
    first = _clip(scene, 0.0, 3.0, tmp_path / "first.m4a")
    second = _clip(scene, 1.5, 3.0, tmp_path / "second.m4a")

    # The shutters claim a 1.0 s gap; the audio says the second clip starts 1.5 s in.
    video_trims, _ = align_clips_spectrogram([first, second], [100.0, 101.0], [3.0, 3.0])

    assert video_trims[0][1] == pytest.approx(1.5, abs=0.05)


def _video_with_audio(dest: Path, seconds: float, frequency: int) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=30:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(dest),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )  # fmt: skip
    return dest


def test_a_burst_whose_alignment_breaks_still_merges_on_its_shutter_trims(tmp_path, monkeypatch):
    """Alignment is an optimisation: however it fails, the burst still renders."""
    from unittest.mock import MagicMock

    from immich_memories.api.models import Asset, AssetType, VideoClipInfo
    from immich_memories.generate_downloads import download_clip
    from immich_memories.processing import live_photo_merger

    sources = {
        "live-a": _video_with_audio(tmp_path / "a.mov", 3.0, 440),
        "live-b": _video_with_audio(tmp_path / "b.mov", 3.0, 660),
    }

    def download_asset(asset_id: str, dest: Path) -> None:
        dest.write_bytes(sources[asset_id].read_bytes())

    # WHY: replaces the Immich download boundary with two local clips
    client = MagicMock()
    client.download_asset.side_effect = download_asset

    def broken(*_args, **_kwargs):
        raise ModuleNotFoundError("No module named 'scipy'")

    # WHY: stands in for any failure inside the optional alignment step
    monkeypatch.setattr(live_photo_merger, "align_clips_spectrogram", broken)

    shutter = 1_700_000_000.0
    clip = VideoClipInfo(
        asset=Asset(
            id="still-a",
            type=AssetType.IMAGE,
            originalFileName="a.heic",
            fileCreatedAt="2025-06-01T10:00:00Z",
            fileModifiedAt="2025-06-01T10:00:00Z",
            updatedAt="2025-06-01T10:00:00Z",
            livePhotoVideoId="live-a",
        ),
        duration_seconds=3.0,
        live_burst_video_ids=list(sources),
        live_burst_trim_points=[(0.0, 2.0), (1.0, 3.0)],
        live_burst_shutter_timestamps=[shutter, shutter + 1.0],
    )

    merged = download_clip(client, None, clip, tmp_path / "work")

    assert merged is not None
    assert merged.name.endswith("_merged.mp4")
    assert merged.stat().st_size > 1000
