"""Real PIL rendering integration tests.

Renders actual frames via PIL — no mocking. Verifies dimensions,
animation progression, and robustness with edge-case inputs.

Run: make test-integration-titles
"""

from __future__ import annotations

import numpy as np
import pytest

from immich_memories.titles.renderer_pil import RenderSettings, TitleRenderer, render_title_frame
from immich_memories.titles.styles import TitleStyle

pytestmark = [pytest.mark.integration]

# Deterministic dark style — matches cinematic defaults
_STYLE = TitleStyle(
    name="test_pil",
    background_colors=["#1A1A2E", "#16213E"],
    background_type="soft_gradient",
    text_color="#FFFFFF",
    animation_preset="fade_up",
    font_weight="semibold",
    use_line_accent=False,
)

_SMALL = RenderSettings(width=320, height=180, fps=10.0, duration=1.0, animated_background=False)


class TestRenderSingleFrame:
    def test_not_blank(self):
        renderer = TitleRenderer(_STYLE, _SMALL)
        frame = renderer.render_frame("Test Title", frame_number=5)
        arr = np.array(frame)
        assert arr.std() > 0, "Frame is uniform — text was not drawn"

    def test_correct_dimensions(self):
        renderer = TitleRenderer(_STYLE, _SMALL)
        frame = renderer.render_frame("Dimensions")
        assert frame.size == (320, 180)
        assert frame.mode == "RGB"

    def test_with_subtitle(self):
        renderer = TitleRenderer(_STYLE, _SMALL)
        frame = renderer.render_frame("Main", subtitle="Sub text")
        arr = np.array(frame)
        assert arr.shape == (180, 320, 3)
        assert arr.std() > 0

    def test_empty_subtitle_works(self):
        """None subtitle should not crash or change dimensions."""
        renderer = TitleRenderer(_STYLE, _SMALL)
        frame = renderer.render_frame("Title Only", subtitle=None)
        assert frame.size == (320, 180)


class TestRenderAllFrames:
    def test_correct_count(self):
        renderer = TitleRenderer(_STYLE, _SMALL)
        frames = renderer.render_all_frames("Frame Count", fade_out_duration=0.3)
        expected = int(1.0 * 10.0)
        assert len(frames) == expected

    def test_mid_and_last_differ(self):
        """A fully-visible mid frame and the faded-out last frame should differ."""
        settings = RenderSettings(
            width=320, height=180, fps=10.0, duration=2.0, animated_background=False
        )
        renderer = TitleRenderer(_STYLE, settings)
        frames = renderer.render_all_frames("Animated", fade_out_duration=1.0)
        # WHY: mid-sequence has fully opaque text; the last frame has text
        # faded out. Comparing these avoids the subtle first-frame issue
        # where fade-up starts almost invisible on dark backgrounds.
        mid = np.array(frames[len(frames) // 2])
        last = np.array(frames[-1])
        assert not np.array_equal(mid, last), "Mid and last frames are identical"


class TestLongTitle:
    def test_long_title_no_crash(self):
        """Very long titles get auto-shrunk — should not raise."""
        renderer = TitleRenderer(_STYLE, _SMALL)
        long_title = "A" * 200
        frame = renderer.render_frame(long_title, frame_number=3)
        assert frame.size == (320, 180)


class TestStandaloneRenderTitleFrame:
    def test_returns_numpy_rgb(self):
        arr = render_title_frame("Standalone", "Sub", _STYLE, 320, 180, 0.5)
        assert isinstance(arr, np.ndarray)
        assert arr.shape == (180, 320, 3)
        assert arr.dtype == np.uint8

    def test_progress_zero_vs_one_differ(self):
        """Progress 0.0 and 1.0 should produce different frames (animation)."""
        a = render_title_frame("Progress", None, _STYLE, 320, 180, 0.0)
        b = render_title_frame("Progress", None, _STYLE, 320, 180, 1.0)
        diff = np.abs(a.astype(float) - b.astype(float)).mean()
        assert diff > 0.5, f"Progress 0 vs 1 too similar (mean diff {diff:.2f})"
