"""Location diversity scoring for trip clip selection.

Boosts clips from locations not yet represented in the selection,
encouraging geographic variety in trip memory videos.
"""

from __future__ import annotations

from typing import Any

from immich_memories.analysis.trip_detection import haversine_km

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
