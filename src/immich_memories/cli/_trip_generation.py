"""Trip memory generation for the CLI.

Handles trip detection, selection, and per-trip video generation.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.analysis.trip_detection import DetectedTrip, haversine_km
from immich_memories.cli._helpers import console, print_error, print_info, print_success
from immich_memories.cli._pipeline_runner import (
    fetch_videos_and_live_photos,
    run_pipeline_and_generate,
)
from immich_memories.timeperiod import DateRange

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.api.models import Asset
    from immich_memories.cli._live_display import ProgressDisplay
    from immich_memories.config_loader import Config

logger = logging.getLogger(__name__)


def _filter_photos_near_trip(
    photos: list[Asset], trip: DetectedTrip, config: Config
) -> list[Asset]:
    """Keep only geotagged photos near the trip centroid (>min_distance_km from home)."""
    home_lat = config.trips.homebase_latitude
    home_lon = config.trips.homebase_longitude
    min_km = config.trips.min_distance_km

    result = []
    for p in photos:
        exif = p.exif_info
        if not exif or exif.latitude is None or exif.longitude is None:
            continue
        dist_from_home = haversine_km(home_lat, home_lon, exif.latitude, exif.longitude)
        if dist_from_home >= min_km:
            result.append(p)

    dropped = len(photos) - len(result)
    if dropped:
        logger.info(
            f"Trip photo filter: kept {len(result)}, dropped {dropped} (no GPS or near home)"
        )
    return result


def resolve_music_arg(music: str | None) -> str | None:
    """Resolve --music CLI argument to a file path or None.

    "auto" or None means let generate_memory() decide based on config.
    A file path is validated to exist.
    """
    if not music or music == "auto":
        return None
    if not Path(music).exists():
        print_error(f"Music file not found: {music}")
        sys.exit(1)
    return music


def handle_trip_generation(
    *,
    client: SyncImmichClient,
    config: Config,
    progress: ProgressDisplay,
    year: int,
    month: int | None,
    trip_index: int | None,
    all_trips: bool,
    near_date: str | None,
    person_names: list[str],
    output_path: Path,
    use_live_photos: bool,
    use_photos: bool,
    effective_analysis_depth: str,
    transition: str,
    music: str | None,
    music_volume: float,
    no_music: bool,
    resolution: str,
    scale_mode: str | None,
    output_format: str | None,
    add_date: bool,
    keep_intermediates: bool,
    privacy_mode: bool,
    title_override: str | None,
    subtitle_override: str | None,
    upload_to_immich: bool,
    album: str | None,
    duration: float | int | None = None,
) -> None:
    """Detect trips, select, and generate video for each."""
    from datetime import datetime as dt_cls

    from immich_memories.cli._trip_display import (
        format_trips_table,
        run_trip_detection,
        select_trips,
    )

    trips = run_trip_detection(client, config, year, progress, person_names)

    trips_table = format_trips_table(trips)
    if trips_table:
        progress.stop()
        console.print()
        console.print(trips_table)
        console.print()
    else:
        print_error("No trips detected for this year")
        sys.exit(0)

    try:
        selected = select_trips(trips, trip_index, all_trips, month=month, near_date=near_date)
    except ValueError as e:
        print_error(str(e))
        sys.exit(1)

    if not selected:
        print_info(
            "Use --trip-index N, --month M, --near-date DATE, or --all-trips to select trip(s)"
        )
        return

    for trip in selected:
        trip_date_range = DateRange(
            start=dt_cls.combine(trip.start_date, dt_cls.min.time()),
            end=dt_cls.combine(trip.end_date, dt_cls.max.time()),
        )
        trip_days = (trip.end_date - trip.start_date).days + 1
        trip_duration = float(duration or max(60, min(600, trip_days * 35)))

        trip_slug = trip.location_name.lower().replace(" ", "_")[:30]
        trip_output = output_path.parent / f"trip_{trip_slug}_{trip.start_date.isoformat()}.mp4"

        console.print(
            f"[bold cyan]Generating trip:[/bold cyan] {trip.location_name} "
            f"({trip.start_date} to {trip.end_date}, {trip_days} days, {trip.asset_count} assets)"
        )

        trip_assets, trip_live = fetch_videos_and_live_photos(
            client=client,
            config=config,
            progress=progress,
            date_ranges=[trip_date_range],
            person_ids=[],
            use_live_photos=use_live_photos,
        )

        trip_photos: list = []
        if use_photos:
            all_photos = client.get_photos_for_date_range(trip_date_range)
            # WHY: photos are fetched by date only — filter to geotagged ones
            # near the trip centroid so home photos don't leak into trip memories
            trip_photos = _filter_photos_near_trip(all_photos, trip, config)

        if not trip_assets and not trip_live and not trip_photos:
            print_error(f"No content found for trip: {trip.location_name}")
            continue

        effective_transition = transition if transition != "smart" else config.defaults.transition
        resolved_music = resolve_music_arg(music)

        trip_preset = {
            "location_name": trip.location_name,
            "trip_start": trip.start_date,
            "trip_end": trip.end_date,
            "home_lat": config.trips.homebase_latitude,
            "home_lon": config.trips.homebase_longitude,
        }

        result_path, should_upload, album_name = run_pipeline_and_generate(
            assets=trip_assets,
            live_photo_clips=trip_live,
            photo_assets=trip_photos if use_photos else None,
            include_photos=use_photos and bool(trip_photos),
            analysis_depth=effective_analysis_depth,
            client=client,
            config=config,
            progress=progress,
            duration=trip_duration,
            transition=effective_transition,
            music=resolved_music,
            music_volume=music_volume,
            no_music=no_music,
            output_path=trip_output,
            output_resolution=resolution,
            scale_mode=scale_mode or config.defaults.scale_mode,
            output_format=output_format,
            add_date_overlay=add_date,
            debug_preserve_intermediates=keep_intermediates,
            privacy_mode=privacy_mode,
            title_override=title_override,
            subtitle_override=subtitle_override,
            memory_type="trip",
            person_names=person_names,
            date_range=trip_date_range,
            upload_to_immich=upload_to_immich,
            album=album,
            memory_preset_params=trip_preset,
        )

        console.print()
        print_success(f"Trip video: {result_path}")
        if should_upload:
            print_success(f"Uploaded to Immich (album: {album_name or 'none'})")
