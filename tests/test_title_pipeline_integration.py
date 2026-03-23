"""Tests for title screen pipeline behavior.

Verifies orientation detection, transition decisions, HDR matching,
and frame interpolation — the integration points that unit tests miss.

Requires FFmpeg installed. Skips if unavailable.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
)
from immich_memories.processing.assembly_engine import _pick_transition


def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


pytestmark = pytest.mark.skipif(not _has_ffmpeg(), reason="FFmpeg not available")


@pytest.fixture
def landscape_clip(tmp_path: Path) -> Path:
    path = tmp_path / "landscape.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:size=320x240:duration=2:rate=30",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(path),
        ],
        capture_output=True,
        timeout=30,
    )
    return path


class TestOrientationRotation:
    """Verify prober handles iPhone rotation metadata correctly."""

    def test_swaps_for_rotation_90(self):
        from immich_memories.processing.ffmpeg_prober import FFmpegProber

        prober = FFmpegProber(AssemblySettings())
        stream = {"width": 3840, "height": 2160, "side_data_list": [{"rotation": -90}]}
        assert prober.parse_resolution_from_stream(stream) == (2160, 3840)

    def test_swaps_for_rotation_270(self):
        from immich_memories.processing.ffmpeg_prober import FFmpegProber

        prober = FFmpegProber(AssemblySettings())
        stream = {"width": 1920, "height": 1080, "side_data_list": [{"rotation": 270}]}
        assert prober.parse_resolution_from_stream(stream) == (1080, 1920)

    def test_no_swap_without_rotation(self):
        from immich_memories.processing.ffmpeg_prober import FFmpegProber

        prober = FFmpegProber(AssemblySettings())
        stream = {"width": 1920, "height": 1080, "side_data_list": []}
        assert prober.parse_resolution_from_stream(stream) == (1920, 1080)


class TestTransitionCut:
    """Verify outgoing_transition='cut' overrides is_title_screen auto-fade."""

    def test_cut_overrides_title_screen(self, landscape_clip: Path):
        title = AssemblyClip(
            path=landscape_clip,
            duration=3.5,
            is_title_screen=True,
            outgoing_transition="cut",
        )
        content = AssemblyClip(path=landscape_clip, duration=5.0)
        transition, _, _ = _pick_transition(title, content, 0, 0)
        assert transition == "cut"

    def test_title_screen_without_override_fades(self, landscape_clip: Path):
        title = AssemblyClip(
            path=landscape_clip,
            duration=3.5,
            is_title_screen=True,
        )
        content = AssemblyClip(path=landscape_clip, duration=5.0)
        transition, _, _ = _pick_transition(title, content, 0, 0)
        assert transition == "fade"

    def test_normal_clip_outgoing_transition_respected(self, landscape_clip: Path):
        clip_a = AssemblyClip(
            path=landscape_clip,
            duration=5.0,
            outgoing_transition="cut",
        )
        clip_b = AssemblyClip(path=landscape_clip, duration=5.0)
        transition, _, _ = _pick_transition(clip_a, clip_b, 0, 0)
        assert transition == "cut"


class TestSlowmoInterpolation:
    """Verify frame interpolation produces smooth, non-duplicate frames."""

    def test_correct_frame_count(self, landscape_clip: Path):
        from immich_memories.titles.content_background import SlowmoBackgroundReader

        reader = SlowmoBackgroundReader(landscape_clip, 160, 120, 30, 3.5)
        if not reader.is_active:
            pytest.skip("Could not create reader")

        count = 0
        for _ in range(200):
            frame = reader.read_frame()
            if frame is None:
                break
            count += 1
        reader.close()
        assert count == 105, f"Expected 105 frames (3.5s * 30fps), got {count}"

    def test_no_duplicate_adjacent_frames(self, landscape_clip: Path):
        from immich_memories.titles.content_background import SlowmoBackgroundReader

        reader = SlowmoBackgroundReader(landscape_clip, 160, 120, 30, 3.5)
        if not reader.is_active:
            pytest.skip("Could not create reader")

        prev = None
        duplicates = 0
        for _ in range(20):
            frame = reader.read_frame()
            if frame is None:
                break
            if prev is not None and (frame == prev).all():
                duplicates += 1
            prev = frame.copy()
        reader.close()
        assert duplicates == 0, f"{duplicates} duplicate frames — interpolation broken"

    def test_frame_values_in_range(self, landscape_clip: Path):
        from immich_memories.titles.content_background import SlowmoBackgroundReader

        reader = SlowmoBackgroundReader(landscape_clip, 160, 120, 30, 3.5)
        if not reader.is_active:
            pytest.skip("Could not create reader")

        frame = reader.read_frame()
        reader.close()
        assert frame is not None
        assert frame.min() >= 0.0
        assert frame.max() <= 1.0
        assert frame.dtype.name == "float32"
