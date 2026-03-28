"""Real map rendering integration tests.

Renders actual map tiles via staticmap + PIL. Verifies dimensions,
content (not blank), and edge cases like single-location maps.

NOTE: These tests fetch map tiles over the network. If the tile
server is unreachable, map_renderer gracefully falls back to a
solid dark background — tests handle both cases.

Run: make test-integration-titles
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from immich_memories.titles.map_renderer import (
    render_equirectangular_map,
    render_location_card,
    render_trip_map_array,
    render_trip_map_frame,
)

pytestmark = [pytest.mark.integration]

# Test coordinates — well-known cities, stable across tile providers
_PARIS = (48.8566, 2.3522)
_LYON = (45.7640, 4.8357)
_LOCATIONS = [_PARIS, _LYON]
_NAMES = ["Paris", "Lyon"]

# Small dimensions for speed
_W, _H = 320, 180


class TestRenderTripMapFrame:
    def test_correct_size(self):
        img = render_trip_map_frame(_LOCATIONS, "France Trip", _W, _H, _NAMES)
        assert isinstance(img, Image.Image)
        assert img.size == (_W, _H)
        assert img.mode == "RGB"

    def test_not_blank(self):
        img = render_trip_map_frame(_LOCATIONS, "France Trip", _W, _H, _NAMES)
        arr = np.array(img)
        # WHY: even the solid-color fallback has pins/text drawn on it,
        # so std should exceed a low threshold
        assert arr.std() > 3, f"Map frame looks blank (std={arr.std():.1f})"


class TestRenderTripMapArray:
    def test_returns_float32_array(self):
        arr = render_trip_map_array(_LOCATIONS, _W, _H, _NAMES)
        assert isinstance(arr, np.ndarray)
        assert arr.dtype == np.float32

    def test_correct_shape(self):
        arr = render_trip_map_array(_LOCATIONS, _W, _H, _NAMES)
        assert arr.shape == (_H, _W, 3)

    def test_normalized_range(self):
        arr = render_trip_map_array(_LOCATIONS, _W, _H, _NAMES)
        assert arr.min() >= 0.0
        assert arr.max() <= 1.0


class TestRenderLocationCard:
    def test_with_coordinates(self):
        img = render_location_card("Paris", _W, _H, lat=_PARIS[0], lon=_PARIS[1])
        assert isinstance(img, Image.Image)
        assert img.size == (_W, _H)
        assert img.mode == "RGB"

    def test_without_coordinates_dark_fallback(self):
        img = render_location_card("Unknown", _W, _H)
        assert img.size == (_W, _H)
        arr = np.array(img)
        # WHY: no coordinates → dark gradient (30,30,35), very uniform
        assert arr.mean() < 50, "Fallback card should be dark"


class TestRenderEquirectangularMap:
    def test_correct_shape(self):
        arr = render_equirectangular_map(_PARIS[0], _PARIS[1], _W, _H)
        assert isinstance(arr, np.ndarray)
        assert arr.shape == (_H, _W, 3)
        assert arr.dtype == np.float32

    def test_has_content(self):
        arr = render_equirectangular_map(_PARIS[0], _PARIS[1], _W, _H)
        # WHY: even the dark fallback returns 0.15-filled array which has std>0
        # A real satellite image has much more variation
        assert arr.std() > 0.0, "Equirectangular map is completely uniform"
        assert arr.min() >= 0.0
        assert arr.max() <= 1.0


class TestSingleLocation:
    def test_single_location_no_crash(self):
        """A single location should render without errors."""
        img = render_trip_map_frame([_PARIS], "Paris", _W, _H, ["Paris"])
        assert img.size == (_W, _H)

    def test_single_location_array(self):
        arr = render_trip_map_array([_PARIS], _W, _H, ["Paris"])
        assert arr.shape == (_H, _W, 3)
