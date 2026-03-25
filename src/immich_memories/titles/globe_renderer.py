"""Globe map animation: keyframe generation and camera interpolation.

Generates camera paths for animated trip map transitions,
flying between destinations on a 3D globe.

Camera model: close → pull-back → close. Start and end at the same
zoom level, with mid-flight altitude proportional to distance (like
watching a plane at 30,000m for long trips, lower for short hops).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Close-up zoom for start/end — city-level tight (#28)
_CLOSE_DISTANCE = 1.5


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

    All keyframes use the same close zoom. The mid-flight pull-back
    is computed during interpolation based on segment distance.

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
        keyframes.append(
            GlobeCameraKeyframe(
                lat=math.radians(lat),
                lon=math.radians(lon),
                distance=_CLOSE_DISTANCE,
                time=t,
            )
        )

    return keyframes


def interpolate_camera(
    keyframes: list[GlobeCameraKeyframe],
    t: float,
) -> tuple[float, float, float]:
    """Interpolate camera position at time t with cosine easing.

    Mid-flight pull-back is proportional to the great-circle distance
    between consecutive keyframes. Short hops barely zoom out;
    long flights pull back like a plane at 30,000m.

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

            # Mid-flight pull-back proportional to segment distance
            mid_dist = _segment_cruise_altitude(k0.lat, k0.lon, k1.lat, k1.lon)
            # Smooth bell curve: close → cruise → close
            bell = math.sin(math.pi * eased)
            d = k0.distance + (mid_dist - k0.distance) * bell

            return (lat, lon, d)

    kf = keyframes[-1]
    return (kf.lat, kf.lon, kf.distance)


def _segment_cruise_altitude(lat0: float, lon0: float, lat1: float, lon1: float) -> float:
    """Compute mid-flight camera distance based on segment arc length.

    Uses great-circle distance on the unit sphere (radians input).
    Power-curve scaling keeps short trips tight while long trips still
    pull back dramatically (#28):
        ~120km  (0.019 rad) → ~1.8  (barely zoom out)
        ~800km  (0.125 rad) → ~2.7  (moderate pull-back)
        ~2800km (0.44 rad)  → ~4.2  (high altitude)
        ~9500km (1.49 rad)  → ~6.5  (near max pull-back)
    """
    # Great-circle distance on unit sphere (haversine formula, radians)
    dlat = lat1 - lat0
    dlon = lon1 - lon0
    a = math.sin(dlat / 2) ** 2 + math.cos(lat0) * math.cos(lat1) * math.sin(dlon / 2) ** 2
    arc = 2 * math.asin(min(1.0, math.sqrt(a)))

    # Power curve: short arcs barely zoom out, long arcs pull back hard.
    # arc**0.7 compresses small values → tighter short trips.
    return min(7.0, _CLOSE_DISTANCE + (arc**0.7) * 5.0)
