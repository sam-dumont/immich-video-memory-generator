"""Trip detection display helpers for the CLI."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from rich.table import Table

from immich_memories.analysis.trip_detection import DetectedTrip, detect_trips

if TYPE_CHECKING:
    from rich.progress import Progress

    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.config_loader import Config


def format_trips_table(trips: list[DetectedTrip]) -> Table | None:
    """Format detected trips as a Rich table for CLI display.

    Returns None if no trips were detected.
    """
    if not trips:
        return None

    table = Table(title="Detected Trips")
    table.add_column("#", style="dim", justify="right")
    table.add_column("Location", style="cyan")
    table.add_column("Dates", style="green")
    table.add_column("Days", justify="right")
    table.add_column("Assets", justify="right")

    for i, trip in enumerate(trips, 1):
        days = (trip.end_date - trip.start_date).days + 1
        date_str = f"{trip.start_date.isoformat()} to {trip.end_date.isoformat()}"
        table.add_row(
            str(i),
            trip.location_name,
            date_str,
            str(days),
            str(trip.asset_count),
        )

    return table


def select_trips(
    trips: list[DetectedTrip],
    trip_index: int | None = None,
    all_trips: bool = False,
    month: int | None = None,
    near_date: str | None = None,
) -> list[DetectedTrip]:
    """Select trips based on CLI flags.

    - trip_index: 1-based index to select a single trip
    - all_trips: select all detected trips
    - month: auto-select trip closest to this month (1-12)
    - near_date: auto-select trip closest to this date (YYYY-MM-DD)
    - Neither: return empty list (discovery mode, just show the table)
    """
    if all_trips:
        return trips

    if trip_index is not None:
        if trip_index < 1 or trip_index > len(trips):
            msg = f"Trip index {trip_index} out of range (1-{len(trips)})"
            raise ValueError(msg)
        return [trips[trip_index - 1]]

    if near_date is not None and trips:
        from immich_memories.timeperiod import parse_date

        target_day = parse_date(near_date)
        return [_closest_trip_to_date(trips, target_day)]

    if month is not None and trips:
        # WHY: pick the trip whose midpoint is closest to the 15th of the given month
        from datetime import date as date_cls

        target_year = trips[0].start_date.year
        target_day = date_cls(target_year, month, 15)
        return [_closest_trip_to_date(trips, target_day)]

    return []


def _closest_trip_to_date(trips: list[DetectedTrip], target: date) -> DetectedTrip:
    """Find the trip whose midpoint is closest to the target date."""

    def distance(trip: DetectedTrip) -> int:
        midpoint = trip.start_date + (trip.end_date - trip.start_date) / 2
        return abs((midpoint - target).days)

    return min(trips, key=distance)


def run_trip_detection(
    client: SyncImmichClient,
    config: Config,
    year: int,
    progress: Progress,
    person_names: list[str] | None = None,
) -> list[DetectedTrip]:
    """Run trip detection for a year: fetch videos, validate homebase, detect trips."""
    from immich_memories.cli._helpers import print_success
    from immich_memories.timeperiod import DateRange

    trips_config = config.trips
    trips_config.validate_homebase()

    # Build date range with 1-month buffer on each side to catch
    # trips that span year boundaries (e.g., Dec 25 → Jan 5).
    from datetime import date as date_cls
    from datetime import datetime

    date_range = DateRange(
        start=datetime(year - 1, 12, 1, 0, 0, 0),
        end=datetime(year + 1, 1, 31, 23, 59, 59),
    )

    # Fetch ALL assets (photos + videos + live photos) for trip detection.
    # Trip detection uses GPS data from any asset type — critical for pre-2018
    # trips where users may only have photos, not videos.
    task = progress.add_task(f"Fetching assets for {year}...", total=None)

    if person_names:
        person_ids: list[str] = []
        for pname in person_names:
            found = client.get_person_by_name(pname)
            if found:
                person_ids.append(found.id)
        if len(person_ids) > 1:
            assets = client.get_assets_for_any_person(person_ids, date_range)
        elif len(person_ids) == 1:
            assets = client.get_assets_for_person_and_date_range(person_ids[0], date_range)
        else:
            assets = client.get_assets_for_date_range(date_range)
    else:
        assets = client.get_assets_for_date_range(date_range)

    progress.update(task, completed=True)
    print_success(f"Found {len(assets)} assets for {year}")

    # Run trip detection on the extended date range
    task = progress.add_task("Detecting trips from GPS data...", total=None)
    trips = detect_trips(
        assets,
        trips_config.homebase_latitude,
        trips_config.homebase_longitude,
        min_distance_km=trips_config.min_distance_km,
        min_duration_days=trips_config.min_duration_days,
        max_gap_days=trips_config.max_gap_days,
    )
    progress.update(task, completed=True)

    # Filter to trips that overlap the requested year.
    # A trip overlaps the year if it ends on/after Jan 1 AND starts on/before Dec 31.
    year_start = date_cls(year, 1, 1)
    year_end = date_cls(year, 12, 31)
    trips = [t for t in trips if t.end_date >= year_start and t.start_date <= year_end]

    print_success(f"Detected {len(trips)} trip(s)")
    return trips
