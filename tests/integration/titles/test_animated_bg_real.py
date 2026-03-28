"""Real animated background generation tests.

Renders actual gradient/radial/vignette backgrounds via PIL and
NumPy. Verifies dimensions, visible content, and animation progression.

Run: make test-integration-titles
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from immich_memories.titles.backgrounds_animated import (
    AnimatedBackgroundConfig,
    create_animated_background,
    create_animated_gradient,
    create_animated_radial,
    create_animated_vignette,
)

pytestmark = [pytest.mark.integration]

_W, _H = 320, 180
_COLORS = ["#1A1A2E", "#E94560"]
_NO_BOKEH = AnimatedBackgroundConfig(enable_bokeh=False)


class TestAnimatedGradient:
    def test_correct_size(self):
        img = create_animated_gradient(_W, _H, _COLORS, base_angle=45.0, progress=0.5)
        assert isinstance(img, Image.Image)
        assert img.size == (_W, _H)
        assert img.mode == "RGB"

    def test_not_blank(self):
        img = create_animated_gradient(_W, _H, _COLORS, base_angle=45.0, progress=0.5)
        arr = np.array(img)
        assert arr.std() > 5, f"Gradient looks blank (std={arr.std():.1f})"

    def test_animation_visible(self):
        """Progress 0.0 and 1.0 should produce visually different frames."""
        img_start = create_animated_gradient(_W, _H, _COLORS, base_angle=0.0, progress=0.0)
        img_end = create_animated_gradient(_W, _H, _COLORS, base_angle=0.0, progress=0.5)
        diff = np.abs(np.array(img_start, dtype=float) - np.array(img_end, dtype=float)).mean()
        assert diff > 0.5, f"Gradient not visibly animated (mean diff={diff:.2f})"


class TestAnimatedRadial:
    def test_correct_size(self):
        img = create_animated_radial(_W, _H, "#E94560", "#1A1A2E", progress=0.5)
        assert img.size == (_W, _H)

    def test_visible_gradient(self):
        img = create_animated_radial(_W, _H, "#FFFFFF", "#000000", progress=0.5)
        arr = np.array(img)
        assert arr.std() > 10, f"Radial gradient too uniform (std={arr.std():.1f})"

    def test_center_brighter_than_edge(self):
        """White center, black edge — center region should be brighter."""
        img = create_animated_radial(_W, _H, "#FFFFFF", "#000000", progress=0.0)
        arr = np.array(img, dtype=float)
        h, w = arr.shape[:2]
        center = arr[h // 4 : 3 * h // 4, w // 4 : 3 * w // 4].mean()
        edge = arr[: h // 8, :].mean()
        assert center > edge, "Center should be brighter than edge in radial gradient"


class TestAnimatedVignette:
    def test_produces_image(self):
        img = create_animated_vignette(_W, _H, "#FFFFFF", "#000000", progress=0.3)
        assert isinstance(img, Image.Image)
        assert img.size == (_W, _H)

    def test_not_uniform(self):
        img = create_animated_vignette(_W, _H, "#FFFFFF", "#000000", progress=0.5)
        arr = np.array(img)
        assert arr.std() > 1, "Vignette output is completely uniform"


class TestAnimatedBackgroundDispatch:
    def test_linear_type(self):
        img = create_animated_background(
            _W, _H, "solid_gradient", _COLORS, 45.0, 0.5, config=_NO_BOKEH
        )
        assert img.size == (_W, _H)
        assert img.mode == "RGB"

    def test_radial_type(self):
        img = create_animated_background(
            _W, _H, "radial_gradient", _COLORS, 0.0, 0.5, config=_NO_BOKEH
        )
        assert img.size == (_W, _H)

    def test_vignette_type(self):
        img = create_animated_background(_W, _H, "vignette", _COLORS, 0.0, 0.5, config=_NO_BOKEH)
        assert img.size == (_W, _H)

    def test_single_color_no_crash(self):
        img = create_animated_background(
            _W, _H, "solid_gradient", ["#FF0000"], 0.0, 0.5, config=_NO_BOKEH
        )
        assert img.size == (_W, _H)


class TestBokehOverlay:
    def test_bokeh_enabled(self):
        cfg = AnimatedBackgroundConfig(enable_bokeh=True, bokeh_count=6)
        img_with = create_animated_background(
            _W, _H, "solid_gradient", _COLORS, 45.0, 0.5, config=cfg
        )
        img_without = create_animated_background(
            _W, _H, "solid_gradient", _COLORS, 45.0, 0.5, config=_NO_BOKEH
        )
        # WHY: bokeh composites semi-transparent white circles on top,
        # so the two images should differ
        diff = np.abs(np.array(img_with, dtype=float) - np.array(img_without, dtype=float)).mean()
        # WHY: bokeh particles are intentionally very subtle (opacity 0.03-0.12),
        # so the mean pixel diff is small but nonzero
        assert diff > 0.01, f"Bokeh overlay had no visible effect (mean diff={diff:.3f})"
