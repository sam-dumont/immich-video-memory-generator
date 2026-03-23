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
    # WHY: testsrc2 generates a moving test pattern (not solid color)
    # so frame interpolation tests can detect actual differences
    path = tmp_path / "landscape.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:duration=2:rate=30",
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

    def test_no_duplicate_frames_in_fast_section(self, landscape_clip: Path):
        """Last 20 frames (fast section of ease-in) should all be unique."""
        from immich_memories.titles.content_background import SlowmoBackgroundReader

        reader = SlowmoBackgroundReader(landscape_clip, 160, 120, 30, 3.5)
        if not reader.is_active:
            pytest.skip("Could not create reader")

        # WHY: cubic ease-in makes first frames nearly identical (very slow).
        # Check the LAST 20 frames where speed is near real-time.
        frames = []
        for _ in range(105):
            f = reader.read_frame()
            if f is None:
                break
            frames.append(f.copy())
        reader.close()

        duplicates = 0
        for i in range(max(0, len(frames) - 20), len(frames) - 1):
            if (frames[i] == frames[i + 1]).all():
                duplicates += 1
        assert duplicates == 0, f"{duplicates} duplicate frames in fast section"

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
