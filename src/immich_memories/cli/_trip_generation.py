"""Trip memory generation for the CLI.

Handles trip detection, selection, and per-trip video generation.
"""

from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import click

from immich_memories.analysis.live_photo_pipeline import drop_live_photo_components
from immich_memories.analysis.trip_detection import DetectedTrip, haversine_km
from immich_memories.cli._asset_fetch import fetch_videos
from immich_memories.cli._helpers import console, print_error, print_info, print_success
from immich_memories.cli._pipeline_runner import run_pipeline_and_generate
from immich_memories.memory_types.date_builders import build_trip
from immich_memories.processing.encoding_plan import resolve_output_selection

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


def _print_trip_result(
    *,
    dry_run: bool,
    location_name: str,
    result_path: Path,
    should_upload: bool,
    album_name: str | None,
) -> None:
    """Report trip planning without claiming an artifact or upload exists."""
    if dry_run:
        print_success(f"Trip plan complete: {location_name}")
        return
    print_success(f"Trip video: {result_path}")
    if should_upload:
        print_success(f"Uploaded to Immich (album: {album_name or 'none'})")


def _has_trusted_automation_identity(
    source: str,
    memory_key: str | None,
    memory_category: str | None,
) -> bool:
    """Return whether hidden context identifies an automation trip candidate."""
    return source == "auto" and bool(memory_key) and memory_category == "trip"


def _exact_trip_match_error(start: date, end: date, match_count: int) -> click.ClickException:
    if match_count == 0:
        message = f"No detected trip exactly matches {start.isoformat()} to {end.isoformat()}"
    else:
        message = (
            f"Exact trip range {start.isoformat()} to {end.isoformat()} found "
            f"{match_count} detected trips; expected exactly one"
        )
    return click.ClickException(message)


def _select_requested_trips(
    trips: list[DetectedTrip],
    *,
    trip_index: int | None,
    all_trips: bool,
    month: int | None,
    near_date: str | None,
    requested_start: date | None,
    requested_end: date | None,
    source: str,
    memory_key: str | None,
    memory_category: str | None,
) -> list[DetectedTrip]:
    """Select one exact automation trip or preserve the manual selectors."""
    exact_automation_request = _has_trusted_automation_identity(
        source,
        memory_key,
        memory_category,
    )
    if exact_automation_request and requested_start is not None and requested_end is not None:
        exact_matches = [
            trip
            for trip in trips
            if trip.start_date == requested_start and trip.end_date == requested_end
        ]
        if len(exact_matches) != 1:
            raise _exact_trip_match_error(requested_start, requested_end, len(exact_matches))
        return exact_matches

    from immich_memories.cli._trip_display import select_trips

    try:
        return select_trips(trips, trip_index, all_trips, month=month, near_date=near_date)
    except ValueError as e:
        print_error(str(e))
        sys.exit(1)


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
    add_place: bool,
    keep_intermediates: bool,
    privacy_mode: bool,
    title_override: str | None,
    subtitle_override: str | None,
    llm_title: bool = False,
    upload_to_immich: bool,
    album: str | None,
    duration: float | int | None = None,
    requested_start: date | None = None,
    requested_end: date | None = None,
    source: str = "manual",
    memory_key: str | None = None,
    memory_category: str | None = None,
    automation_attempt_id: str | None = None,
    orientation: str = "landscape",
    dry_run: bool = False,
) -> None:
    """Detect trips, select, and generate video for each."""
    from immich_memories.cli._trip_display import (
        format_trips_table,
        run_trip_detection,
    )

    trips = run_trip_detection(client, config, year, progress, person_names)
    selected = _select_requested_trips(
        trips,
        trip_index=trip_index,
        all_trips=all_trips,
        month=month,
        near_date=near_date,
        requested_start=requested_start,
        requested_end=requested_end,
        source=source,
        memory_key=memory_key,
        memory_category=memory_category,
    )

    trips_table = format_trips_table(trips)
    if trips_table:
        progress.stop()
        console.print()
        console.print(trips_table)
        console.print()
    else:
        print_error("No trips detected for this year")
        sys.exit(0)

    if not selected:
        print_info(
            "Use --trip-index N, --month M, --near-date DATE, or --all-trips to select trip(s)"
        )
        return

    output_selection = resolve_output_selection(
        config_codec=config.output.codec,
        config_container=config.output.format,
        format_override=output_format,
    )

    for trip in selected:
        # The wizard's Trip card resolves the same span through build_trip via
        # the preset; sharing the builder is what keeps the two surfaces from
        # ending the last day a microsecond apart.
        trip_date_range = build_trip(trip.start_date, trip.end_date)
        trip_days = (trip.end_date - trip.start_date).days + 1
        trip_duration = float(duration) if duration is not None else None

        trip_slug = trip.location_name.lower().replace(" ", "_")[:30]
        trip_output = output_path.parent / (
            f"trip_{trip_slug}_{trip.start_date.isoformat()}.{output_selection.container}"
        )

        console.print(
            f"[bold cyan]Generating trip:[/bold cyan] {trip.location_name} "
            f"({trip.start_date} to {trip.end_date}, {trip_days} days, {trip.asset_count} assets)"
        )

        trip_assets = fetch_videos(
            client=client,
            progress=progress,
            date_ranges=[trip_date_range],
            person_ids=[],
        )

        trip_photos: list = []
        if use_photos:
            all_photos = client.get_photos_for_date_range(trip_date_range)
            # WHY: photos are fetched by date only — filter to geotagged ones
            # near the trip centroid so home photos don't leak into trip memories
            trip_photos = _filter_photos_near_trip(all_photos, trip, config)

        # A Live Photo's video half belongs to its still, not to the video pool.
        trip_assets = drop_live_photo_components(trip_assets, trip_photos)

        if not trip_assets and not trip_photos:
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
            photo_assets=trip_photos if use_photos else None,
            include_photos=use_photos and bool(trip_photos),
            use_live_photos=use_live_photos,
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
            output_orientation=orientation,
            scale_mode=scale_mode or config.defaults.scale_mode,
            output_format=output_format,
            add_date_overlay=add_date,
            add_place_overlay=add_place,
            debug_preserve_intermediates=keep_intermediates,
            privacy_mode=privacy_mode,
            title_override=title_override,
            subtitle_override=subtitle_override,
            llm_title=llm_title,
            memory_type="trip",
            person_names=person_names,
            date_range=trip_date_range,
            upload_to_immich=upload_to_immich,
            album=album,
            memory_preset_params=trip_preset,
            source=source,
            memory_key=memory_key,
            memory_category=memory_category,
            automation_attempt_id=automation_attempt_id,
            dry_run=dry_run,
        )

        console.print()
        _print_trip_result(
            dry_run=dry_run,
            location_name=trip.location_name,
            result_path=result_path,
            should_upload=should_upload,
            album_name=album_name,
        )
