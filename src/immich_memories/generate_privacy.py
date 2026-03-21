"""Privacy and anonymization helpers for video generation.

Extracted from generate.py — relocates GPS coordinates to a fake city,
anonymizes person names, and generates trip titles from preset params.
"""

from __future__ import annotations

import hashlib
import logging
import random
from dataclasses import replace

from immich_memories.processing.assembly_config import AssemblyClip

logger = logging.getLogger(__name__)


_PRIVACY_FAKE_NAMES = [
    "Alice",
    "Bob",
    "Charlie",
    "Diana",
    "Eve",
    "Frank",
    "Grace",
    "Hank",
    "Iris",
    "Jack",
    "Kim",
    "Leo",
]
# WHY: real city coordinates so map tiles show a real place, not ocean
_PRIVACY_FAKE_CITIES = [
    ("Paris, France", 48.8566, 2.3522),
    ("Amsterdam, Netherlands", 52.3676, 4.9041),
    ("Barcelona, Spain", 41.3874, 2.1686),
    ("Copenhagen, Denmark", 55.6761, 12.5683),
    ("Lisbon, Portugal", 38.7223, -9.1393),
    ("Vienna, Austria", 48.2082, 16.3738),
    ("Prague, Czech Republic", 50.0755, 14.4378),
    ("Stockholm, Sweden", 59.3293, 18.0686),
]


def anonymize_name(name: str | None) -> str | None:
    """Replace a real name with a consistent fake name."""
    if name is None:
        return None
    # WHY: hashlib is deterministic across processes (hash() is not — PYTHONHASHSEED)
    idx = int(hashlib.sha256(name.encode()).hexdigest(), 16) % len(_PRIVACY_FAKE_NAMES)
    return _PRIVACY_FAKE_NAMES[idx]


def pick_fake_city() -> tuple[str, float, float]:
    """Pick a deterministic fake city (same seed = same city every run)."""
    rng = random.Random(42)
    return _PRIVACY_FAKE_CITIES[rng.randint(0, len(_PRIVACY_FAKE_CITIES) - 1)]


def anonymize_preset_params(preset_params: dict) -> dict:
    """Anonymize trip-related preset params (home GPS, location name)."""
    result = preset_params.copy()
    fake_name, fake_lat, fake_lon = pick_fake_city()

    # WHY: shift home to the fake city center so map fly starts from there
    if "home_lat" in result and result["home_lat"] is not None:
        result["home_lat"] = fake_lat - 1.5  # Offset from city so route is visible
        result["home_lon"] = fake_lon - 1.0
    if "location_name" in result:
        result["location_name"] = fake_name

    return result


def anonymize_clips_for_privacy(
    clips: list[AssemblyClip],
) -> list[AssemblyClip]:
    """Relocate all GPS coords to a fake city (preserves cluster shape)."""
    fake_name, fake_lat, fake_lon = pick_fake_city()

    # Find centroid of real GPS coords
    gps_clips = [(c.latitude, c.longitude) for c in clips if c.latitude and c.longitude]
    if not gps_clips:
        return clips

    real_center_lat = sum(lat for lat, _ in gps_clips) / len(gps_clips)
    real_center_lon = sum(lon for _, lon in gps_clips) / len(gps_clips)

    # WHY: offset = fake city - real centroid, so cluster lands ON the fake city
    lat_offset = fake_lat - real_center_lat
    lon_offset = fake_lon - real_center_lon

    result = []
    for clip in clips:
        if clip.latitude is not None and clip.longitude is not None:
            new_lat = max(-90, min(90, clip.latitude + lat_offset))
            new_lon = ((clip.longitude + lon_offset + 180) % 360) - 180

            clip = replace(clip, latitude=new_lat, longitude=new_lon, location_name=fake_name)
        result.append(clip)
    return result


def clip_location_name(exif) -> str | None:
    """Extract human-readable location from exif data."""
    if not exif:
        return None
    city = exif.city
    country = exif.country
    if city and country:
        return f"{city}, {country}"
    return country or city


def extract_trip_locations(assembly_clips: list[AssemblyClip]) -> list[tuple[float, float]]:
    """Extract unique GPS locations from assembly clips for map pins."""
    seen: set[tuple[float, float]] = set()
    locations: list[tuple[float, float]] = []
    for clip in assembly_clips:
        if clip.latitude is not None and clip.longitude is not None:
            key = (round(clip.latitude, 2), round(clip.longitude, 2))
            if key not in seen:
                seen.add(key)
                locations.append((clip.latitude, clip.longitude))
    return locations


def generate_trip_title_text(preset_params: dict) -> str | None:
    """Generate trip title text from preset params."""
    from immich_memories.titles._trip_titles import generate_trip_title

    location_name = preset_params.get("location_name")
    trip_start = preset_params.get("trip_start")
    trip_end = preset_params.get("trip_end")

    if not location_name or not trip_start or not trip_end:
        return None

    return generate_trip_title(location_name, trip_start, trip_end)
