"""Pixel-level tests for EndingService fade-to-white behavior.

Verifies the actual fade progression by decoding video frames.
Run: make test-integration-titles
"""

from __future__ import annotations

from pathlib import Path

import pytest

from immich_memories.titles.ending_service import EndingService
from immich_memories.titles.styles import TitleStyle
from tests.integration.conftest import requires_ffmpeg
from tests.integration.titles.conftest import (
    TITLE_FPS,
    TITLE_H,
    TITLE_W,
    extract_frame_rgb,
    extract_frames_rgb,
    has_audio_stream,
)

pytestmark = [pytest.mark.integration, requires_ffmpeg]

TEST_STYLE = TitleStyle(
    name="test_ending",
    background_colors=["#1A1A2E", "#16213E"],
    background_type="soft_gradient",
)


class TestEndingPixels:
    """Verify ending screen fade-to-white at the pixel level."""

    def _generate_ending(self, tmp_path: Path, duration: float = 2.0) -> Path:
        service = EndingService(TEST_STYLE)
        output = tmp_path / "ending.mp4"
        service.create_ending_video(
            output_path=output,
            fade_to_color=(255, 255, 255),
            width=TITLE_W,
            height=TITLE_H,
            duration=duration,
            fps=float(TITLE_FPS),
            hdr=False,
        )
        return output

    def test_early_frames_show_background(self, tmp_path):
        """First ~1.5s should hold dark background (before fade starts)."""
        output = self._generate_ending(tmp_path)

        # WHY: EndingService holds background for 1.5s before starting fade.
        # Frame at 0.5s (frame 5 at 10fps) should be dark background.
        frame = extract_frame_rgb(output, 3, TITLE_W, TITLE_H)
        mean = float(frame.mean())

        assert mean < 80, (
            f"Early frame mean {mean:.0f} — expected dark background (<80). "
            f"Fade may be starting too early."
        )

    def test_last_frame_is_bright(self, tmp_path):
        """Last frame should be near-white (fade completed)."""
        output = self._generate_ending(tmp_path)
        total_frames = int(2.0 * TITLE_FPS)

        last_frame = extract_frame_rgb(output, total_frames - 1, TITLE_W, TITLE_H)
        mean = float(last_frame.mean())

        assert mean > 200, (
            f"Last frame mean {mean:.0f} — expected near-white (>200). Fade-to-white may be broken."
        )

    def test_fade_is_progressive(self, tmp_path):
        """Brightness should increase monotonically during fade portion."""
        output = self._generate_ending(tmp_path, duration=3.0)

        frames = extract_frames_rgb(output, 5, TITLE_W, TITLE_H)
        means = [float(f.mean()) for f in frames]

        # WHY: First frame is dark, last is bright. The sequence should
        # generally increase. We check that the last 3 frames are
        # monotonically increasing (fade region).
        for i in range(2, len(means) - 1):
            assert means[i + 1] >= means[i] - 5, (
                f"Fade not progressive: frame {i} mean={means[i]:.0f}, "
                f"frame {i + 1} mean={means[i + 1]:.0f}. "
                f"Full sequence: {[f'{m:.0f}' for m in means]}"
            )

    def test_has_audio_stream(self, tmp_path):
        """Ending must have audio for assembly compatibility."""
        output = self._generate_ending(tmp_path)
        assert has_audio_stream(output), "Ending video missing audio — assembly concat will fail"
