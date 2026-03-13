"""Trip detection: GPS-based clustering of videos far from home.

Detects trips by filtering assets beyond a distance threshold from the user's
homebase, then grouping temporally contiguous assets into trip clusters.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from immich_memories.api.models import Asset


@dataclass
class DetectedTrip:
    """A detected trip: a cluster of GPS-tagged assets far from home."""

    start_date: date
    end_date: date
    location_name: str
    asset_count: int
    centroid_lat: float
    centroid_lon: float
    asset_ids: list[str] = field(default_factory=list)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute great-circle distance between two GPS points in kilometers."""
    r = 6371.0  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


def detect_trips(
    assets: list[Asset],
    home_lat: float,
    home_lon: float,
    min_distance_km: float = 50,
    min_duration_days: int = 2,
    max_gap_days: int = 2,
) -> list[DetectedTrip]:
    """Detect trips from a list of assets based on GPS distance from home.

    1. Filter to assets with GPS beyond min_distance_km from home
    2. Sort by timestamp
    3. Group into clusters separated by > max_gap_days
    4. Keep clusters spanning >= min_duration_days
    """
    # Step 1: filter to far-from-home assets with GPS
    away: list[Asset] = []
    for asset in assets:
        if asset.exif_info is None:
            continue
        if asset.exif_info.latitude is None or asset.exif_info.longitude is None:
            continue
        dist = haversine_km(home_lat, home_lon, asset.exif_info.latitude, asset.exif_info.longitude)
        if dist >= min_distance_km:
            away.append(asset)

    if not away:
        return []

    # Step 2: sort by timestamp
    away.sort(key=lambda a: a.file_created_at)

    # Step 3: group by temporal gaps
    groups: list[list[Asset]] = [[away[0]]]
    for asset in away[1:]:
        prev = groups[-1][-1]
        gap_days = (asset.file_created_at - prev.file_created_at).total_seconds() / 86400
        if gap_days > max_gap_days:
            groups.append([asset])
        else:
            groups[-1].append(asset)

    # Step 4: filter by min duration and build DetectedTrip objects
    trips: list[DetectedTrip] = []
    for group in groups:
        first_date = group[0].file_created_at.date()
        last_date = group[-1].file_created_at.date()
        span_days = (last_date - first_date).days
        if span_days < min_duration_days:
            continue

        location = _derive_location_name(group)
        lats = [a.exif_info.latitude for a in group if a.exif_info and a.exif_info.latitude]
        lons = [a.exif_info.longitude for a in group if a.exif_info and a.exif_info.longitude]

        trips.append(
            DetectedTrip(
                start_date=first_date,
                end_date=last_date,
                location_name=location,
                asset_count=len(group),
                centroid_lat=sum(lats) / len(lats) if lats else 0.0,
                centroid_lon=sum(lons) / len(lons) if lons else 0.0,
                asset_ids=[a.id for a in group],
            )
        )

    return trips


def _derive_location_name(assets: list[Asset]) -> str:
    """Derive a human-readable location name from EXIF city/country fields."""
    pairs: list[tuple[str | None, str | None]] = []
    for asset in assets:
        if asset.exif_info:
            pairs.append((asset.exif_info.city, asset.exif_info.country))

    if not pairs:
        return "Unknown Location"

    # Count (city, country) pairs
    counter: Counter[tuple[str | None, str | None]] = Counter(pairs)
    city, country = counter.most_common(1)[0][0]

    if city and country:
        return f"{city}, {country}"
    if country:
        return country
    if city:
        return city
    return "Unknown Location"
