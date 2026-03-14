"""Trip detection: GPS clustering, overnight stops, home base identification."""

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
class OvernightBase:
    """A base camp: consecutive days sleeping at the same location."""

    start_date: date
    end_date: date
    nights: int
    lat: float
    lon: float
    location_name: str
    asset_ids: list[str] = field(default_factory=list)


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
    """Remove assets that are within min_distance_km of home.

    Assets without GPS data are kept (they may be from the trip
    but missing coordinates).
    """
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


def _cluster_photos(
    gps_assets: list[Asset],
    radius_km: float,
) -> list[list[Asset]]:
    """Greedy spatial clustering: assign each photo to nearest cluster or create new."""
    clusters: list[tuple[float, float, list[Asset]]] = []  # centroid_lat, centroid_lon, assets
    for a in gps_assets:
        assert a.exif_info is not None  # pre-filtered
        assert a.exif_info.latitude is not None
        assert a.exif_info.longitude is not None
        lat, lon = a.exif_info.latitude, a.exif_info.longitude
        best_idx, best_dist = -1, radius_km + 1
        for i, (clat, clon, _) in enumerate(clusters):
            d = haversine_km(lat, lon, clat, clon)
            if d < best_dist:
                best_idx, best_dist = i, d
        if best_idx >= 0 and best_dist <= radius_km:
            clusters[best_idx][2].append(a)
        else:
            clusters.append((lat, lon, [a]))
    return [c[2] for c in clusters]


def _cluster_day_presence(
    cluster: list[Asset],
) -> set[date]:
    """Return the set of distinct dates this cluster has photos on."""
    dates: set[date] = set()
    for a in cluster:
        dt = a.local_date_time if a.local_date_time else a.file_created_at
        dates.add(dt.date())
    return dates


def _cluster_centroid(cluster: list[Asset]) -> tuple[float, float]:
    """Average lat/lon of a photo cluster."""
    lats = [a.exif_info.latitude for a in cluster if a.exif_info and a.exif_info.latitude]
    lons = [a.exif_info.longitude for a in cluster if a.exif_info and a.exif_info.longitude]
    return sum(lats) / len(lats), sum(lons) / len(lons)  # type: ignore[arg-type]


def _cluster_city(cluster: list[Asset]) -> str:
    """Most common city name in a cluster."""
    cities: Counter[str] = Counter()
    for a in cluster:
        if a.exif_info and a.exif_info.city:
            cities[a.exif_info.city] += 1
    if cities:
        return cities.most_common(1)[0][0]
    return "Unknown"


def _has_return_gap(days: set[date], min_gap: int = 2) -> bool:
    """True if you left this location and came back (gap of min_gap+ days).

    A gap of 1 day could be consecutive travel. A gap of 2+ days means
    you were away for at least one full day, proving a return pattern.
    """
    if len(days) < 2:
        return False
    sorted_days = sorted(days)
    for i in range(1, len(sorted_days)):
        if (sorted_days[i] - sorted_days[i - 1]).days > min_gap:
            return True
    return False


_HomeBase = tuple[float, float, str, set[date]]  # lat, lon, city, days
_DailyStop = tuple[date, float, float, str, list[str]]  # date, lat, lon, city, ids


def _identify_home_bases(
    gps_assets: list[Asset],
    trip_days: int,
    base_radius: float,
) -> list[_HomeBase]:
    """Phase 1: Cluster photos and identify recurring home bases."""
    clusters = _cluster_photos(gps_assets, base_radius)
    threshold = max(3, math.ceil(trip_days * 0.3))
    home_bases: list[_HomeBase] = []
    for cluster in clusters:
        days = _cluster_day_presence(cluster)
        if len(days) >= threshold or _has_return_gap(days):
            clat, clon = _cluster_centroid(cluster)
            home_bases.append((clat, clon, _cluster_city(cluster), days))
    return home_bases


def _assign_daily_stops(
    sorted_dates: list[date],
    by_date: dict[date, list[Asset]],
    home_bases: list[_HomeBase],
    base_radius: float,
) -> list[_DailyStop]:
    """Phase 2: Assign each day to a home base or excursion location."""
    daily_stops: list[_DailyStop] = []
    for i, d in enumerate(sorted_dates):
        day_assets = sorted(by_date[d], key=lambda a: a.local_date_time or a.file_created_at)
        ids = [a.id for a in day_assets]
        last = day_assets[-1]
        assert last.exif_info and last.exif_info.latitude and last.exif_info.longitude
        last_lat, last_lon = last.exif_info.latitude, last.exif_info.longitude

        # Check home bases (only if last photo is still near base)
        assigned = False
        for hb_lat, hb_lon, hb_city, hb_days in home_bases:
            if d in hb_days and haversine_km(last_lat, last_lon, hb_lat, hb_lon) <= base_radius:
                daily_stops.append((d, hb_lat, hb_lon, hb_city, ids))
                assigned = True
                break
        if assigned:
            continue

        # Excursion: use next morning's first photo, or last photo of the day
        if i + 1 < len(sorted_dates):
            ref = sorted(
                by_date[sorted_dates[i + 1]], key=lambda a: a.local_date_time or a.file_created_at
            )[0]
        else:
            ref = day_assets[-1]
        assert ref.exif_info and ref.exif_info.latitude and ref.exif_info.longitude
        daily_stops.append(
            (
                d,
                ref.exif_info.latitude,
                ref.exif_info.longitude,
                ref.exif_info.city or "Unknown",
                ids,
            )
        )
    return daily_stops


def _merge_consecutive(
    daily_stops: list[_DailyStop],
    merge_radius_km: float,
) -> list[OvernightBase]:
    """Phase 3: Merge consecutive same-location nights into bases."""
    bases: list[OvernightBase] = []
    for stop_date, lat, lon, city, ids in daily_stops:
        if bases and haversine_km(bases[-1].lat, bases[-1].lon, lat, lon) <= merge_radius_km:
            bases[-1].end_date = stop_date
            bases[-1].nights += 1
            bases[-1].asset_ids.extend(ids)
        else:
            bases.append(
                OvernightBase(
                    start_date=stop_date,
                    end_date=stop_date,
                    nights=1,
                    lat=lat,
                    lon=lon,
                    location_name=city,
                    asset_ids=list(ids),
                )
            )
    return bases


def _merge_repeated_bases(
    bases: list[OvernightBase],
    merge_radius_km: float,
) -> list[OvernightBase]:
    """Phase 4: Merge bases that appear at non-consecutive positions (A→X→A → A)."""
    changed = True
    while changed:
        changed = False
        i = 0
        while i < len(bases):
            for j in range(i + 2, len(bases)):
                if (
                    haversine_km(bases[i].lat, bases[i].lon, bases[j].lat, bases[j].lon)
                    <= merge_radius_km
                ):
                    for k in range(i + 1, j + 1):
                        bases[i].end_date = bases[k].end_date
                        bases[i].nights += bases[k].nights
                        bases[i].asset_ids.extend(bases[k].asset_ids)
                    del bases[i + 1 : j + 1]
                    changed = True
                    break
            i += 1
    return bases


def detect_overnight_stops(
    assets: list[Asset],
    merge_radius_km: float = 5.0,
) -> list[OvernightBase]:
    """Detect overnight stops using home base detection + excursion fallback."""
    gps_assets = [
        a
        for a in assets
        if a.exif_info and a.exif_info.latitude is not None and a.exif_info.longitude is not None
    ]
    if not gps_assets:
        return []

    by_date: dict[date, list[Asset]] = {}
    for a in gps_assets:
        dt = a.local_date_time if a.local_date_time else a.file_created_at
        by_date.setdefault(dt.date(), []).append(a)
    sorted_dates = sorted(by_date)

    base_cluster_radius = max(merge_radius_km, 15.0)
    home_bases = _identify_home_bases(gps_assets, len(sorted_dates), base_cluster_radius)
    daily_stops = _assign_daily_stops(sorted_dates, by_date, home_bases, base_cluster_radius)
    bases = _merge_consecutive(daily_stops, merge_radius_km)
    return _merge_repeated_bases(bases, merge_radius_km)


def reverse_geocode(lat: float, lon: float, spread_km: float | None = None) -> str | None:
    """Reverse geocode coordinates to the most specific location name.

    Always queries at zoom=10 for full address hierarchy.
    Returns the most specific name available: island > county > province > state.
    The caller (_derive_location_name) handles multi-country and spread logic.

    The spread_km parameter is accepted but no longer used for zoom selection —
    kept for API compatibility.

    Returns "Location, Country" or None on failure.
    """
    try:
        geolocator = Nominatim(user_agent="immich-memories")
        location = geolocator.reverse(f"{lat}, {lon}", zoom=10, language="en")
        if location is None:
            return None
        addr = location.raw.get("address", {})
        country = addr.get("country")

        # Return most specific: island > county > province > state_district > state
        # Skip 'region' — it's too broad (e.g., "Metropolitan France") and
        # the EXIF fallback in _derive_location_name gives better results.
        region = (
            addr.get("island")
            or addr.get("county")
            or addr.get("province")
            or addr.get("state_district")
            or addr.get("state")
        )

        if region and country:
            # Avoid "Cyprus, Cyprus" — if region equals country, just return country
            if region == country:
                return country
            return f"{region}, {country}"
        # No useful region found — return None so EXIF fallback kicks in
        return None
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
