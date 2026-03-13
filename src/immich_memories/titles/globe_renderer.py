"""Globe map animation: keyframe generation and camera interpolation.

Generates camera paths for animated trip map transitions,
flying between destinations on a 3D globe.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class GlobeCameraKeyframe:
    """A camera position/zoom at a specific time in the animation."""

    lat: float  # Radians
    lon: float  # Radians
    distance: float  # Distance from sphere center (>1.0)
    time: float  # Normalized [0, 1]


def generate_camera_keyframes(
    home_lat: float,
    home_lon: float,
    destinations: list[tuple[float, float]],
) -> list[GlobeCameraKeyframe]:
    """Generate camera keyframes from home to destinations.

    Args:
        home_lat: Home latitude (degrees).
        home_lon: Home longitude (degrees).
        destinations: List of (lat, lon) in degrees.

    Returns:
        List of keyframes from home through all destinations.
    """
    points = [(home_lat, home_lon), *destinations]
    n = len(points)
    keyframes = []

    for i, (lat, lon) in enumerate(points):
        t = i / max(1, n - 1)
        distance = _compute_overview_distance(home_lat, home_lon, destinations) if i == 0 else 2.2
        keyframes.append(
            GlobeCameraKeyframe(
                lat=math.radians(lat),
                lon=math.radians(lon),
                distance=distance,
                time=t,
            )
        )

    return keyframes


def interpolate_camera(
    keyframes: list[GlobeCameraKeyframe],
    t: float,
) -> tuple[float, float, float]:
    """Interpolate camera position at time t with cosine easing.

    Returns:
        (lat_rad, lon_rad, distance).
    """
    if not keyframes:
        return (0.0, 0.0, 3.0)
    if t <= keyframes[0].time:
        kf = keyframes[0]
        return (kf.lat, kf.lon, kf.distance)
    if t >= keyframes[-1].time:
        kf = keyframes[-1]
        return (kf.lat, kf.lon, kf.distance)

    for i in range(len(keyframes) - 1):
        k0, k1 = keyframes[i], keyframes[i + 1]
        if k0.time <= t <= k1.time:
            local_t = (t - k0.time) / (k1.time - k0.time)
            eased = 0.5 * (1.0 - math.cos(math.pi * local_t))

            lat = k0.lat + (k1.lat - k0.lat) * eased
            lon = k0.lon + (k1.lon - k0.lon) * eased

            # Pull-back mid-transition for distant moves
            mid_dist = max(k0.distance, k1.distance) * 1.3
            if eased < 0.5:
                d = k0.distance + (mid_dist - k0.distance) * (eased * 2)
            else:
                d = mid_dist + (k1.distance - mid_dist) * ((eased - 0.5) * 2)

            return (lat, lon, d)

    kf = keyframes[-1]
    return (kf.lat, kf.lon, kf.distance)


def _compute_overview_distance(
    home_lat: float,
    home_lon: float,
    destinations: list[tuple[float, float]],
) -> float:
    """Compute camera distance that shows both home and farthest destination."""
    from immich_memories.analysis.trip_detection import haversine_km

    max_dist_km = 0.0
    for lat, lon in destinations:
        d = haversine_km(home_lat, home_lon, lat, lon)
        max_dist_km = max(max_dist_km, d)

    # Mapping: 500km → 3.0, 2000km → 5.0, 10000km → 8.0
    return min(8.0, max(2.5, 2.5 + math.log1p(max_dist_km / 500)))
