"""Year-scoped trip discovery shared by the Memory page and CLI."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from immich_memories.analysis.trip_detection import DetectedTrip, detect_trips
from immich_memories.timeperiod import DateRange

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.config_models_automation import TripsConfig


def discover_year_trips(
    client: SyncImmichClient,
    config: TripsConfig,
    year: int,
    *,
    person_names: list[str] | None = None,
) -> list[DetectedTrip]:
    """Find trips overlapping a year, using GPS from every asset type.

    A month on each side keeps New Year trips whole. Optional people narrow
    discovery only; the finished memory still uses the trip's full window.
    """
    config.validate_homebase()
    date_range = DateRange(
        start=datetime(year - 1, 12, 1),
        end=datetime(year + 1, 1, 31, 23, 59, 59),
    )
    person_ids = []
    for name in person_names or []:
        if person := client.get_person_by_name(name):
            person_ids.append(person.id)
    if len(person_ids) > 1:
        assets = client.get_assets_for_any_person(person_ids, date_range)
    elif person_ids:
        assets = client.get_assets_for_person_and_date_range(person_ids[0], date_range)
    else:
        assets = client.get_assets_for_date_range(date_range)
    trips = detect_trips(
        assets,
        config.homebase_latitude,
        config.homebase_longitude,
        min_distance_km=config.min_distance_km,
        min_duration_days=config.min_duration_days,
        max_gap_days=config.max_gap_days,
    )
    return [
        trip
        for trip in trips
        if trip.end_date >= date(year, 1, 1) and trip.start_date <= date(year, 12, 31)
    ]
