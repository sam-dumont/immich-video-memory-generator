"""Album memory generation for the CLI.

Resolves an Immich album by name or ID, fetches its assets, and generates
a memory video from exactly that curated set — no date-range searching.
"""

from __future__ import annotations

import logging
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.api.models import Asset, AssetType
from immich_memories.cli._helpers import console, print_error, print_info, print_success
from immich_memories.cli._pipeline_runner import run_pipeline_and_generate
from immich_memories.cli._trip_generation import resolve_music_arg
from immich_memories.timeperiod import DateRange

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.config_loader import Config

logger = logging.getLogger(__name__)

_MONTH_NAMES = [
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def resolve_album(client: SyncImmichClient, identifier: str) -> dict:
    """Resolve an album by ID, exact name, or unique partial name match.

    Matching is case-insensitive. On ambiguity or no match, prints the
    candidates / available albums and exits.
    """
    albums = client.get_albums()

    for album in albums:
        if album.get("id") == identifier:
            return album

    lowered = identifier.lower()
    exact = [a for a in albums if a.get("albumName", "").lower() == lowered]
    if len(exact) == 1:
        return exact[0]

    partial = [a for a in albums if lowered in a.get("albumName", "").lower()]
    if not exact and len(partial) == 1:
        return partial[0]

    matches = exact or partial
    if matches:
        print_error(f"Multiple albums match '{identifier}':")
        for a in matches:
            print_info(f"  {a.get('albumName')} ({a.get('assetCount', '?')} items)")
        print_info("Use the exact album name or its ID.")
        sys.exit(1)

    print_error(f"Album not found: {identifier}")
    if albums:
        print_info("Available albums:")
        for a in sorted(albums, key=lambda x: x.get("albumName", "").lower()):
            print_info(f"  {a.get('albumName')} ({a.get('assetCount', '?')} items)")
    sys.exit(1)


def album_slug(name: str) -> str:
    """Filesystem-safe slug from an album name."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug[:40] or "album"


def format_date_span(start: date, end: date) -> str:
    """Human-readable date span, e.g. 'July 4 \u2013 12, 2026'."""
    if start == end:
        return f"{_MONTH_NAMES[start.month]} {start.day}, {start.year}"
    if start.year == end.year:
        if start.month == end.month:
            return f"{_MONTH_NAMES[start.month]} {start.day} \u2013 {end.day}, {start.year}"
        return (
            f"{_MONTH_NAMES[start.month]} {start.day} \u2013 "
            f"{_MONTH_NAMES[end.month]} {end.day}, {start.year}"
        )
    return (
        f"{_MONTH_NAMES[start.month]} {start.day}, {start.year} \u2013 "
        f"{_MONTH_NAMES[end.month]} {end.day}, {end.year}"
    )


def album_date_range(assets: list[Asset], album: dict) -> DateRange:
    """Derive the album's date range from its assets (fallback: album metadata)."""
    if assets:
        stamps = [a.file_created_at for a in assets]
        return DateRange(start=min(stamps), end=max(stamps))

    start_raw = album.get("startDate")
    end_raw = album.get("endDate")
    if start_raw and end_raw:
        start = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).replace(tzinfo=None)
        end = datetime.fromisoformat(end_raw.replace("Z", "+00:00")).replace(tzinfo=None)
        return DateRange(start=start, end=end)

    today = datetime.now()
    return DateRange(start=today, end=today)


def default_album_duration(asset_count: int) -> float:
    """~5s of output per album asset, clamped to 60s-600s."""
    if asset_count <= 0:
        return 120.0
    return float(max(60, min(600, asset_count * 5)))


def run_album_command(
    *,
    config: Config,
    source_album: str,
    output: str | None,
    include_live_photos: bool,
    include_photos: bool,
    photo_duration: float | None,
    analysis_depth: str | None,
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
    duration: float | int | None,
    quiet: bool,
    dry_run: bool,
) -> None:
    """Entry point for `generate --memory-type album`: scaffolding + error boundary."""
    from immich_memories.api.immich import ImmichAPIError, SyncImmichClient
    from immich_memories.cli._live_display import LiveDisplay, QuietDisplay
    from immich_memories.generate import GenerationError

    use_live_photos = include_live_photos or config.analysis.include_live_photos
    use_photos = include_photos or config.photos.enabled
    if photo_duration is not None:
        config.photos.duration = photo_duration
    effective_analysis_depth = analysis_depth or "fast"

    show_interactive = not quiet and sys.stdout.isatty()
    if not show_interactive:
        from immich_memories.cli._helpers import set_quiet_mode

        set_quiet_mode(True)

    try:
        display = LiveDisplay(console=console) if show_interactive else QuietDisplay()
        with display as progress:
            task = progress.add_task("Connecting to Immich...", total=None)
            with SyncImmichClient(
                base_url=config.immich.url,
                api_key=config.immich.api_key,
            ) as client:
                progress.update(task, completed=True)
                handle_album_generation(
                    client=client,
                    config=config,
                    progress=progress,
                    source_album=source_album,
                    output=output,
                    use_live_photos=use_live_photos,
                    use_photos=use_photos,
                    effective_analysis_depth=effective_analysis_depth,
                    transition=transition,
                    music=music,
                    music_volume=music_volume,
                    no_music=no_music,
                    resolution=resolution,
                    scale_mode=scale_mode,
                    output_format=output_format,
                    add_date=add_date,
                    keep_intermediates=keep_intermediates,
                    privacy_mode=privacy_mode,
                    title_override=title_override,
                    subtitle_override=subtitle_override,
                    upload_to_immich=upload_to_immich,
                    album=album,
                    duration=duration,
                    dry_run=dry_run,
                )
    except ImmichAPIError as e:
        print_error(f"Immich API error: {e}")
        sys.exit(1)
    except GenerationError as e:
        print_error(str(e))
        sys.exit(1)
    except Exception as e:  # WHY: CLI top-level error boundary — sanitizes and displays error
        from immich_memories.security import sanitize_error_message

        print_error(f"Error: {sanitize_error_message(str(e))}")
        sys.exit(1)


def _split_album_assets(assets: list[Asset], use_photos: bool) -> tuple[list[Asset], list[Asset]]:
    """Split album assets into (videos, photos); photos empty unless enabled."""
    videos = [a for a in assets if a.is_video]
    photos = [a for a in assets if a.type == AssetType.IMAGE] if use_photos else []
    return videos, photos


def _require_album_content(
    videos: list[Asset], photos: list[Asset], album_name: str, use_photos: bool
) -> None:
    """Exit with a helpful message when the album has nothing usable."""
    if videos or photos:
        return
    if use_photos:
        print_error(f"Album '{album_name}' has no usable videos or photos")
    else:
        print_error(
            f"Album '{album_name}' has no videos. "
            "Add --include-photos to build the memory from photos."
        )
    sys.exit(1)


def _album_output_path(
    output: str | None, config: Config, album_name: str, date_range: DateRange
) -> Path:
    """Resolve the output file path for an album memory."""
    if output:
        return Path(output)
    output_dir = config.output.output_path
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = album_slug(album_name)
    return output_dir / f"album_{slug}_{date_range.start.strftime('%Y%m%d')}.mp4"


def _print_album_summary(videos: list[Asset], photos: list[Asset]) -> None:
    """Print asset counts and total video duration."""
    print_info(f"Found {len(videos)} videos" + (f" and {len(photos)} photos" if photos else ""))
    total_dur = sum(a.duration_seconds or 0 for a in videos)
    if total_dur:
        print_info(f"Total video duration: {total_dur / 60:.1f} minutes")


def handle_album_generation(
    *,
    client: SyncImmichClient,
    config: Config,
    progress,
    source_album: str,
    output: str | None,
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
    dry_run: bool = False,
) -> None:
    """Fetch an album's assets and generate a memory video from them."""
    task = progress.add_task("Resolving album...", total=None)
    album_info = resolve_album(client, source_album)
    album_id = album_info["id"]
    album_name = album_info.get("albumName", "Untitled Album")
    progress.update(task, completed=True)
    print_success(f"Album: {album_name}")

    task = progress.add_task("Fetching album assets...", total=None)
    assets = client.get_album_assets(album_id)
    progress.update(task, completed=True)

    videos, photos = _split_album_assets(assets, use_photos)
    _require_album_content(videos, photos, album_name, use_photos)

    if use_live_photos:
        # WHY: Live Photo motion extraction searches by date range, which would
        # pull in assets outside the album. Stills in the album are kept as photos.
        print_info("Live Photo motion clips are not supported in album mode — using stills")

    date_range = album_date_range(assets, album_info)
    span = format_date_span(date_range.start.date(), date_range.end.date())
    _print_album_summary(videos, photos)

    album_duration = float(duration) if duration else default_album_duration(len(assets))
    output_path = _album_output_path(output, config, album_name, date_range)

    console.print(
        f"[bold cyan]Generating album memory:[/bold cyan] {album_name} "
        f"({span}, {len(assets)} assets, {album_duration:.0f}s target)"
    )

    if dry_run:
        print_info("Dry run - no video will be generated")
        return

    effective_transition = transition if transition != "smart" else config.defaults.transition
    resolved_music = resolve_music_arg(music)

    album_preset = {
        "album_id": album_id,
        "album_name": album_name,
        "asset_count": len(assets),
    }

    result_path, should_upload, upload_album_name = run_pipeline_and_generate(
        assets=videos,
        live_photo_clips=[],
        photo_assets=photos if use_photos else None,
        include_photos=use_photos and bool(photos),
        analysis_depth=effective_analysis_depth,
        client=client,
        config=config,
        progress=progress,
        duration=album_duration,
        transition=effective_transition,
        music=resolved_music,
        music_volume=music_volume,
        no_music=no_music,
        output_path=output_path,
        output_resolution=resolution,
        scale_mode=scale_mode or config.defaults.scale_mode,
        output_format=output_format,
        add_date_overlay=add_date,
        debug_preserve_intermediates=keep_intermediates,
        privacy_mode=privacy_mode,
        title_override=title_override or album_name,
        subtitle_override=subtitle_override or span,
        memory_type="album",
        person_names=[],
        date_range=date_range,
        upload_to_immich=upload_to_immich,
        album=album,
        memory_preset_params=album_preset,
    )

    console.print()
    print_success(f"Album video: {result_path}")
    if should_upload:
        print_success(f"Uploaded to Immich (album: {upload_album_name or 'none'})")
