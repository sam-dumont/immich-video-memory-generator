"""A Live Photo burst reaches the film as one merged clip, or as its still's own video.

Real FFmpeg on one-second clips; only Immich is replaced.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from immich_memories.generate_downloads import download_clip
from tests.conftest import make_clip

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _member(path: Path, *, seconds: float = 1.0, audio: bool = False) -> Path:
    sources = ["-f", "lavfi", "-i", f"testsrc=size=160x120:rate=30:duration={seconds}"]
    if audio:
        sources += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *sources, "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", *(["-c:a", "aac"] if audio else []), "-shortest", str(path)],
        check=True, capture_output=True,
    )  # fmt: skip
    return path


def _streams(path: Path) -> tuple[float, set[str]]:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    payload = json.loads(probe.stdout)
    kinds = {stream["codec_type"] for stream in payload["streams"]}
    return float(payload["format"]["duration"]), kinds


class _Immich:
    """WHY: Immich is the external boundary; this one serves the files it holds
    and fails every other download the way a dropped connection does."""

    def __init__(self, held: dict[str, Path]) -> None:
        self.held = held
        self.requested: list[str] = []

    def download_asset(self, asset_id: str, destination: Path, **_: object) -> None:
        self.requested.append(asset_id)
        if asset_id not in self.held:
            raise ConnectionError(f"{asset_id}: connection reset")
        shutil.copyfile(self.held[asset_id], destination)


def _burst(trims: list[tuple[float, float]], shutters: list[float] | None = None):
    clip = make_clip("still-1")
    clip.live_burst_video_ids = [f"member-{i}" for i in range(len(trims))]
    clip.live_burst_trim_points = trims
    clip.live_burst_shutter_timestamps = shutters
    return clip


def _download(immich: _Immich, clip, tmp_path: Path) -> Path | None:
    return download_clip(immich, None, clip, tmp_path / "run", hardware_enabled=False)


def test_a_burst_is_merged_into_one_clip(tmp_path):
    immich = _Immich({f"member-{i}": _member(tmp_path / f"m{i}.mov") for i in range(2)})

    merged = _download(immich, _burst([(0.0, 1.0), (0.0, 1.0)]), tmp_path)

    assert merged == tmp_path / "run" / ".live_merges" / "still-1_merged.mp4"
    duration, _ = _streams(merged)
    assert duration == pytest.approx(2.0, abs=0.15)


def test_a_merge_already_on_disk_is_reused_without_downloading(tmp_path):
    existing = tmp_path / "run" / ".live_merges" / "still-1_merged.mp4"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"x" * 2000)
    immich = _Immich({})

    assert _download(immich, _burst([(0.0, 1.0), (0.0, 1.0)]), tmp_path) == existing
    assert immich.requested == []


def test_a_member_that_fails_to_download_leaves_the_rest_on_their_own_trims(tmp_path):
    """The survivor keeps its own trim, not the first one in the list."""
    immich = _Immich({"member-1": _member(tmp_path / "m1.mov")})

    merged = _download(immich, _burst([(0.0, 1.0), (0.0, 0.5)]), tmp_path)

    duration, _ = _streams(merged)
    assert duration == pytest.approx(0.5, abs=0.15)


def test_when_no_member_downloads_the_still_video_is_used(tmp_path):
    immich = _Immich({"still-1": _member(tmp_path / "still.mov")})

    fallback = _download(immich, _burst([(0.0, 1.0), (0.0, 1.0)]), tmp_path)

    assert fallback is not None
    assert fallback.stem == "still-1"
    assert not (tmp_path / "run" / ".live_merges" / "still-1_merged.mp4").exists()


def test_when_no_member_is_a_video_the_still_video_is_used(tmp_path):
    broken = tmp_path / "broken.mov"
    broken.write_bytes(b"not a video")
    immich = _Immich(
        {"member-0": broken, "member-1": broken, "still-1": _member(tmp_path / "still.mov")}
    )

    fallback = _download(immich, _burst([(0.0, 1.0), (0.0, 1.0)]), tmp_path)

    assert fallback is not None
    assert fallback.stem == "still-1"


def test_a_burst_with_sound_keeps_it_when_audio_alignment_fails(tmp_path):
    """Spectrogram alignment is an improvement; losing it must not lose the clip."""
    immich = _Immich({f"member-{i}": _member(tmp_path / f"m{i}.mov", audio=True) for i in range(2)})
    clip = _burst([(0.0, 1.0), (0.0, 1.0)], shutters=[100.0, 101.0])

    # WHY: forces the cross-correlation to fail, which real one-second tones rarely do
    with patch(
        "immich_memories.processing.live_photo_merger.align_clips_spectrogram",
        side_effect=ValueError("no correlation peak"),
    ):
        merged = _download(immich, clip, tmp_path)

    duration, kinds = _streams(merged)
    assert kinds == {"video", "audio"}
    assert duration == pytest.approx(2.0, abs=0.15)
