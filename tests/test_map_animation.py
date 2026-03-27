"""Tests for titles/map_animation.py coordinate math and interpolation."""

from __future__ import annotations

import pytest

from immich_memories.titles.map_animation import (
    _destination_overview,
    _geo_to_screen,
    _linear_pan,
    _pick_interpolator,
    _title_alpha,
    _to_latlon,
    _to_world,
    _van_wijk,
)


class TestWebMercator:
    def test_round_trip_equator(self):
        """to_world → to_latlon should recover the original coordinates."""
        lat, lon = 0.0, 0.0
        wx, wy = _to_world(lat, lon)
        recovered_lat, recovered_lon = _to_latlon(wx, wy)

        assert abs(recovered_lat - lat) < 0.001
        assert abs(recovered_lon - lon) < 0.001

    def test_round_trip_paris(self):
        lat, lon = 48.8566, 2.3522
        wx, wy = _to_world(lat, lon)
        recovered_lat, recovered_lon = _to_latlon(wx, wy)

        assert abs(recovered_lat - lat) < 0.001
        assert abs(recovered_lon - lon) < 0.001

    def test_round_trip_southern_hemisphere(self):
        lat, lon = -33.8688, 151.2093  # Sydney
        wx, wy = _to_world(lat, lon)
        recovered_lat, recovered_lon = _to_latlon(wx, wy)

        assert abs(recovered_lat - lat) < 0.001
        assert abs(recovered_lon - lon) < 0.001


class TestScreenProjection:
    def test_center_of_screen_at_same_position(self):
        """A pin at the camera position should project to screen center."""
        lat, lon = 48.8566, 2.3522
        sx, sy = _geo_to_screen(lat, lon, lat, lon, zoom=10, w=1920, h=1080)

        assert abs(sx - 960) < 2
        assert abs(sy - 540) < 2


class TestVanWijkInterpolator:
    def test_starts_at_origin(self):
        """f(0) should return the start view."""
        p0 = (100.0, 100.0, 10.0)
        p1 = (200.0, 200.0, 10.0)
        interp = _van_wijk(p0, p1)

        cx, cy, w = interp(0.0)

        assert abs(cx - p0[0]) < 0.01
        assert abs(cy - p0[1]) < 0.01

    def test_ends_at_destination(self):
        """f(1) should return the end view."""
        p0 = (100.0, 100.0, 10.0)
        p1 = (200.0, 200.0, 10.0)
        interp = _van_wijk(p0, p1)

        cx, cy, w = interp(1.0)

        assert abs(cx - p1[0]) < 0.5
        assert abs(cy - p1[1]) < 0.5


class TestLinearPan:
    def test_starts_at_origin(self):
        p0 = (100.0, 100.0, 5.0)
        p1 = (110.0, 110.0, 5.0)
        interp = _linear_pan(p0, p1)

        cx, cy, w = interp(0.0)

        assert abs(cx - p0[0]) < 0.01
        assert abs(cy - p0[1]) < 0.01

    def test_ends_at_destination(self):
        p0 = (100.0, 100.0, 5.0)
        p1 = (110.0, 110.0, 5.0)
        interp = _linear_pan(p0, p1)

        cx, cy, w = interp(1.0)

        assert abs(cx - p1[0]) < 0.01
        assert abs(cy - p1[1]) < 0.01


class TestPickInterpolator:
    def test_short_hop_uses_linear_pan(self):
        """Nearby points should use linear pan (mid-zoom stays high)."""
        lat0, lon0 = 48.856, 2.352  # Paris center
        lat1, lon1 = 48.860, 2.360  # ~500m away
        p0 = (*_to_world(lat0, lon0), 1920 / (2**14))
        p1 = (*_to_world(lat1, lon1), 1920 / (2**14))

        interp = _pick_interpolator(p0, p1, width=1920)

        # Linear pan: viewport width stays constant (max of the two inputs)
        _, _, w_mid = interp(0.5)
        assert w_mid >= max(p0[2], p1[2])


class TestDestinationOverview:
    def test_contains_all_destinations(self):
        """Overview should produce a view that covers all destinations."""
        destinations = [(48.856, 2.352), (35.676, 139.650), (-33.868, 151.209)]

        cx, cy, w = _destination_overview(destinations, 1920, 1080)

        for lat, lon in destinations:
            wx, wy = _to_world(lat, lon)
            assert abs(wx - cx) < w, f"Destination ({lat},{lon}) outside overview x"
            assert abs(wy - cy) < w, f"Destination ({lat},{lon}) outside overview y"


class TestTitleAlpha:
    @pytest.mark.parametrize(
        "progress,expected_range",
        [
            (0.0, (0.0, 0.01)),  # Start: invisible
            (0.075, (0.4, 0.6)),  # Mid fade-in: partial
            (0.15, (0.99, 1.01)),  # End of fade-in: fully visible
            (0.5, (0.99, 1.01)),  # Middle: fully visible
            (0.85, (0.99, 1.01)),  # Start of fade-out: still visible
            (1.0, (0.0, 0.01)),  # End: invisible
        ],
    )
    def test_fade_curve(self, progress: float, expected_range: tuple[float, float]):
        alpha = _title_alpha(progress)
        lo, hi = expected_range
        assert lo <= alpha <= hi, f"alpha={alpha} at progress={progress}"
