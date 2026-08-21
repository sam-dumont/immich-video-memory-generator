"""Tests for title screen pipeline behavior.

Verifies orientation detection, transition decisions, HDR matching,
and frame interpolation — the integration points that unit tests miss.

Requires FFmpeg installed. Skips if unavailable.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
    standalone_assembly_encoding_plan,
)
from immich_memories.processing.assembly_engine import _pick_transition
from immich_memories.processing.encoding_plan import HdrTransfer


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

        prober = FFmpegProber(AssemblySettings(encoding_plan=standalone_assembly_encoding_plan()))
        stream = {"width": 3840, "height": 2160, "side_data_list": [{"rotation": -90}]}
        assert prober.parse_resolution_from_stream(stream) == (2160, 3840)

    def test_swaps_for_rotation_270(self):
        from immich_memories.processing.ffmpeg_prober import FFmpegProber

        prober = FFmpegProber(AssemblySettings(encoding_plan=standalone_assembly_encoding_plan()))
        stream = {"width": 1920, "height": 1080, "side_data_list": [{"rotation": 270}]}
        assert prober.parse_resolution_from_stream(stream) == (1080, 1920)

    def test_no_swap_without_rotation(self):
        from immich_memories.processing.ffmpeg_prober import FFmpegProber

        prober = FFmpegProber(AssemblySettings(encoding_plan=standalone_assembly_encoding_plan()))
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


class TestSlowmoColorDomain:
    """Content-backed titles must preserve the source transfer exactly."""

    @staticmethod
    def _reader_command(source_transfer: HdrTransfer) -> list[str]:
        import io

        from immich_memories.titles.content_background import SlowmoBackgroundReader

        duration_probe = MagicMock(stdout="2.0", returncode=0)
        frame = np.full((2, 2, 3), 32768, dtype=np.uint16).tobytes()
        # WHY a real BytesIO: extraction streams the pipe (#408); the mock
        # process must behave like one — data, then EOF.
        frame_proc = MagicMock()
        frame_proc.stdout = io.BytesIO(frame * 2)
        frame_proc.wait.return_value = 0
        # WHY: ffprobe/ffmpeg are the external boundary; the fake streams then EOFs (#408)
        with (
            patch("immich_memories.titles.content_background.shutil.which", return_value="ffmpeg"),
            patch(
                "immich_memories.titles.content_background.subprocess.run",
                side_effect=[duration_probe],
            ),
            patch(
                "immich_memories.titles.content_background.subprocess.Popen",
                return_value=frame_proc,
            ) as popen,
        ):
            reader = SlowmoBackgroundReader(
                Path("/tmp/content.mp4"),
                2,
                2,
                2,
                title_duration=1.0,
                source_transfer=source_transfer,
            )
        assert reader.is_active
        return popen.call_args_list[0].args[0]

    def test_hlg_background_is_not_tone_mapped_before_title_composition(self) -> None:
        command = self._reader_command(HdrTransfer.HLG)

        assert "-vf" not in command
        assert command[command.index("-pix_fmt") + 1] == "rgb48le"

    def test_pq_background_is_not_tone_mapped_before_title_composition(self) -> None:
        command = self._reader_command(HdrTransfer.PQ)

        assert "-vf" not in command
        assert command[command.index("-pix_fmt") + 1] == "rgb48le"

    def test_sdr_background_does_not_receive_hdr_tone_mapping(self) -> None:
        command = self._reader_command(HdrTransfer.NONE)

        assert "-vf" not in command
        assert command[command.index("-pix_fmt") + 1] == "rgb24"


@pytest.mark.parametrize("fps", [30, 60])
def test_pil_content_background_extracts_from_short_prerender(
    tmp_path: Path,
    fps: int,
) -> None:
    """The 0.5-second title pre-render always has frame zero, never frame 30."""
    from immich_memories.titles.rendering_service import RenderingService

    clip = tmp_path / f"short-{fps}.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=red:s=32x18:r={fps}:d=0.5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(clip),
        ],
        check=True,
        timeout=15,
    )

    frame = RenderingService._extract_blurred_frame(clip, 32, 18)

    assert frame is not None
    assert frame.shape == (18, 32, 3)
