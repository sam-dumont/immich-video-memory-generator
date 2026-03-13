"""Trip detection: GPS-based clustering of videos far from home.

Detects trips by filtering assets beyond a distance threshold from the user's
homebase, then grouping temporally contiguous assets into trip clusters.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from geopy.geocoders import Nominatim  # type: ignore[import-untyped]

from immich_memories.api.models import Asset

logger = logging.getLogger(__name__)


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

        lats = [a.exif_info.latitude for a in group if a.exif_info and a.exif_info.latitude]
        lons = [a.exif_info.longitude for a in group if a.exif_info and a.exif_info.longitude]
        c_lat = sum(lats) / len(lats) if lats else 0.0
        c_lon = sum(lons) / len(lons) if lons else 0.0
        location = _derive_location_name(group, centroid_lat=c_lat, centroid_lon=c_lon)

        trips.append(
            DetectedTrip(
                start_date=first_date,
                end_date=last_date,
                location_name=location,
                asset_count=len(group),
                centroid_lat=c_lat,
                centroid_lon=c_lon,
                asset_ids=[a.id for a in group],
            )
        )

    return trips


def reverse_geocode(lat: float, lon: float, spread_km: float | None = None) -> str | None:
    """Reverse geocode coordinates to a location name.

    Always queries at zoom=10 (which returns the full address hierarchy),
    then picks the appropriate specificity based on trip spread:
      - Small spread (<100km): county/island/province level ("Ardennes")
      - Large spread (>=100km): country level ("Cyprus")

    Returns "Location, Country" or None on failure.
    """
    try:
        geolocator = Nominatim(user_agent="immich-memories")
        location = geolocator.reverse(f"{lat}, {lon}", zoom=10, language="en")
        if location is None:
            return None
        addr = location.raw.get("address", {})
        country = addr.get("country")

        if spread_km is not None and spread_km >= 100:
            # Wide trip (whole island/country): just return country
            return country

        # Detailed: island > county > province > state_district > state
        region = (
            addr.get("island")
            or addr.get("county")
            or addr.get("province")
            or addr.get("state_district")
            or addr.get("state")
            or addr.get("region")
        )

        if region and country:
            return f"{region}, {country}"
        if country:
            return country
        if region:
            return region
    except Exception:
        logger.debug("Reverse geocoding failed for (%s, %s)", lat, lon)
    return None


def _extract_unique_countries(assets: list[Asset]) -> list[str]:
    """Extract unique country names from EXIF, ordered by first appearance."""
    seen: set[str] = set()
    countries: list[str] = []
    for a in assets:
        if a.exif_info and a.exif_info.country and a.exif_info.country not in seen:
            seen.add(a.exif_info.country)
            countries.append(a.exif_info.country)
    return countries


def _get_dominant_country(assets: list[Asset], threshold: float = 0.9) -> str | None:
    """Return the country name if it accounts for >= threshold of tagged assets."""
    country_counts: Counter[str] = Counter()
    total = 0
    for a in assets:
        if a.exif_info and a.exif_info.country:
            country_counts[a.exif_info.country] += 1
            total += 1
    if total == 0:
        return None
    top_country, top_count = country_counts.most_common(1)[0]
    if top_count / total >= threshold:
        return top_country
    return None


def _compute_spread_km(assets: list[Asset]) -> float:
    """Compute max distance between any two GPS-tagged assets in km."""
    coords = [
        (a.exif_info.latitude, a.exif_info.longitude)
        for a in assets
        if a.exif_info and a.exif_info.latitude and a.exif_info.longitude
    ]
    if len(coords) < 2:
        return 0.0
    max_dist = 0.0
    for i, (lat1, lon1) in enumerate(coords):
        for lat2, lon2 in coords[i + 1 :]:
            d = haversine_km(lat1, lon1, lat2, lon2)
            if d > max_dist:
                max_dist = d
    return max_dist


def _derive_location_name(
    assets: list[Asset],
    centroid_lat: float | None = None,
    centroid_lon: float | None = None,
) -> str:
    """Derive a human-readable location name, preferring reverse geocoding."""
    # Compute geographic spread to decide zoom level
    spread_km = _compute_spread_km(assets)

    # Multi-country trips: if spread is very large and multiple countries
    # are represented, check if one country dominates (90%+ of assets).
    # Layovers with a few photos shouldn't change the trip name.
    if spread_km > 300:
        countries = _extract_unique_countries(assets)
        if len(countries) > 1:
            dominant = _get_dominant_country(assets, threshold=0.9)
            if dominant:
                return dominant
            return " → ".join(countries)

    # Try reverse geocoding the centroid first
    if centroid_lat is not None and centroid_lon is not None:
        geocoded = reverse_geocode(centroid_lat, centroid_lon, spread_km=spread_km)
        if geocoded:
            return geocoded

    # Fallback to EXIF city/country
    pairs: list[tuple[str | None, str | None]] = []
    for asset in assets:
        if asset.exif_info:
            pairs.append((asset.exif_info.city, asset.exif_info.country))

    if not pairs:
        return "Unknown Location"

    counter: Counter[tuple[str | None, str | None]] = Counter(pairs)
    city, country = counter.most_common(1)[0][0]

    if city and country:
        return f"{city}, {country}"
    if country:
        return country
    if city:
        return city
    return "Unknown Location"
