"""Trip detection: GPS clustering, overnight stops, home base identification."""

from __future__ import annotations

import logging
import math
import operator
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from geopy.exc import GeopyError
from geopy.geocoders import Nominatim

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


def filter_near_home(
    assets: list[Asset],
    home_lat: float,
    home_lon: float,
    min_distance_km: float = 50,
) -> list[Asset]:
    """Remove assets within min_distance_km of home. Assets without GPS are kept."""
    result: list[Asset] = []
    for asset in assets:
        if (
            not asset.exif_info
            or asset.exif_info.latitude is None
            or asset.exif_info.longitude is None
        ):
            result.append(asset)  # Keep assets without GPS
            continue
        dist = haversine_km(home_lat, home_lon, asset.exif_info.latitude, asset.exif_info.longitude)
        if dist >= min_distance_km:
            result.append(asset)
    return result


def _filter_away_assets(
    assets: list[Asset], home_lat: float, home_lon: float, min_km: float
) -> list[Asset]:
    away = []
    for a in assets:
        if a.exif_info is None or a.exif_info.latitude is None or a.exif_info.longitude is None:
            continue
        if haversine_km(home_lat, home_lon, a.exif_info.latitude, a.exif_info.longitude) >= min_km:
            away.append(a)
    return away


def _group_by_temporal_gaps(away: list[Asset], max_gap_days: int) -> list[list[Asset]]:
    groups: list[list[Asset]] = [[away[0]]]
    for asset in away[1:]:
        gap = (asset.file_created_at - groups[-1][-1].file_created_at).total_seconds() / 86400
        if gap > max_gap_days:
            groups.append([asset])
        else:
            groups[-1].append(asset)
    return groups


def _build_trip_from_group(group: list[Asset], *, name_locations: bool = True) -> DetectedTrip:
    lats = [a.exif_info.latitude for a in group if a.exif_info and a.exif_info.latitude]
    lons = [a.exif_info.longitude for a in group if a.exif_info and a.exif_info.longitude]
    c_lat = sum(lats) / len(lats) if lats else 0.0
    c_lon = sum(lons) / len(lons) if lons else 0.0
    return DetectedTrip(
        start_date=group[0].file_created_at.date(),
        end_date=group[-1].file_created_at.date(),
        location_name=(
            _derive_location_name(group, centroid_lat=c_lat, centroid_lon=c_lon)
            if name_locations
            else ""
        ),
        asset_count=len(group),
        centroid_lat=c_lat,
        centroid_lon=c_lon,
        asset_ids=[a.id for a in group],
    )


def detect_trips(
    assets: list[Asset],
    home_lat: float,
    home_lon: float,
    min_distance_km: float = 50,
    min_duration_days: int = 2,
    max_gap_days: int = 2,
    *,
    name_locations: bool = True,
) -> list[DetectedTrip]:
    """Detect trips: filter GPS assets far from home, group by temporal gaps, filter by duration.

    name_locations=False skips the reverse geocoding. A caller that only wants
    the dates — the special-day scan, which uses trips solely to know which
    days to skip — otherwise makes a live request per trip across every year
    it walks, and throws every answer away.
    """
    away = _filter_away_assets(assets, home_lat, home_lon, min_distance_km)
    if not away:
        return []
    away.sort(key=lambda a: a.file_created_at)
    groups = _group_by_temporal_gaps(away, max_gap_days)

    trips: list[DetectedTrip] = []
    for group in groups:
        span_days = (group[-1].file_created_at.date() - group[0].file_created_at.date()).days
        if span_days >= min_duration_days:
            trips.append(_build_trip_from_group(group, name_locations=name_locations))
    return trips


_HomeBase = tuple[float, float, str, set[date]]
_DailyStop = tuple[date, float, float, str, list[str]]


def reverse_geocode(lat: float, lon: float, spread_km: float | None = None) -> str | None:
    """Reverse geocode to most specific name. spread_km kept for API compat."""
    try:
        geolocator = Nominatim(user_agent="immich-memories")
        location = geolocator.reverse(f"{lat}, {lon}", zoom=10, language="en")
        if location is None:
            return None
        addr = location.raw.get("address", {})
        country = addr.get("country")
        region = (
            addr.get("island")
            or addr.get("county")
            or addr.get("province")
            or addr.get("state_district")
            or addr.get("state")
        )
        if region and country:
            if region == country:  # Avoid "Cyprus, Cyprus"
                return country
            return f"{region}, {country}"
        return None
    except (GeopyError, OSError, ValueError) as e:
        # GeopyError as well as OSError: only GeocoderTimedOut and
        # GeocoderUnavailable inherit from OSError. GeocoderServiceError
        # itself does not, and neither does the 403 raised when the service
        # declines — which used to travel out of a twenty-year scan.
        logger.debug("Reverse geocoding failed for (%s, %s): %s", lat, lon, e)
    return None


def _extract_unique_countries(assets: list[Asset]) -> list[str]:
    """Extract unique country names from EXIF, ordered by first appearance."""
    seen: set[str] = set()
    result: list[str] = []
    for a in assets:
        c = a.exif_info.country if a.exif_info else None
        if c and c not in seen:
            seen.add(c)
            result.append(c)
    return result


def _get_dominant_country(assets: list[Asset], threshold: float = 0.9) -> str | None:
    """Return the country name if it accounts for >= threshold of tagged assets."""
    counts: Counter[str] = Counter(
        a.exif_info.country for a in assets if a.exif_info and a.exif_info.country
    )
    total = sum(counts.values())
    if total == 0:
        return None
    top, cnt = counts.most_common(1)[0]
    return top if cnt / total >= threshold else None


def _compute_spread_km(assets: list[Asset]) -> float:
    """Approximate max GPS spread via bounding-box extremes (O(n) vs O(n²))."""
    coords = [
        (a.exif_info.latitude, a.exif_info.longitude)
        for a in assets
        if a.exif_info and a.exif_info.latitude and a.exif_info.longitude
    ]
    if len(coords) < 2:
        return 0.0
    extremes = [
        min(coords, key=operator.itemgetter(0)),
        max(coords, key=operator.itemgetter(0)),
        min(coords, key=operator.itemgetter(1)),
        max(coords, key=operator.itemgetter(1)),
    ]
    max_dist = 0.0
    for i, (lat1, lon1) in enumerate(extremes):
        for lat2, lon2 in extremes[i + 1 :]:
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
    spread_km = _compute_spread_km(assets)

    if spread_km > 300:
        countries = _extract_unique_countries(assets)
        if len(countries) > 1:
            dominant = _get_dominant_country(assets, threshold=0.9)
            if dominant:
                return dominant
            return " → ".join(countries)

    if centroid_lat is not None and centroid_lon is not None:
        geocoded = reverse_geocode(centroid_lat, centroid_lon, spread_km=spread_km)
        if geocoded:
            return geocoded

    pairs: list[tuple[str | None, str | None]] = [
        (a.exif_info.city, a.exif_info.country) for a in assets if a.exif_info
    ]

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


# ---------------------------------------------------------------------------
# Location diversity scoring for trip clip selection
# ---------------------------------------------------------------------------

# Clips closer than this to an already-selected location get no bonus
_SAME_LOCATION_THRESHOLD_KM = 20.0

# Bonus multiplier for clips from new locations
_DIVERSITY_BONUS = 0.25


def location_diversity_bonus(
    candidate_location: str | None,
    candidate_lat: float,
    candidate_lon: float,
    selected_locations: list[dict[str, Any]],
) -> float:
    """Calculate diversity bonus for a candidate clip's location.

    Returns a bonus score (0.0 to _DIVERSITY_BONUS) based on whether
    the candidate's location is already represented in the selection.
    Uses name matching first, then falls back to distance-based check.

    Args:
        candidate_location: Location name of the candidate clip.
        candidate_lat: Latitude of the candidate.
        candidate_lon: Longitude of the candidate.
        selected_locations: List of dicts with 'latitude', 'longitude' keys
            for already-selected clips.

    Returns:
        0.0 if the location is already represented, _DIVERSITY_BONUS otherwise.
    """
    if not selected_locations:
        return _DIVERSITY_BONUS

    for sel in selected_locations:
        # Fast path: exact name match
        if candidate_location and sel.get("location_name") == candidate_location:
            return 0.0
        sel_lat = sel.get("latitude")
        sel_lon = sel.get("longitude")
        if sel_lat is None or sel_lon is None:
            continue
        dist = haversine_km(candidate_lat, candidate_lon, sel_lat, sel_lon)
        if dist < _SAME_LOCATION_THRESHOLD_KM:
            return 0.0

    return _DIVERSITY_BONUS
