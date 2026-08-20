"""Album-mode generation: an Immich album is the candidate pool (#270)."""

from __future__ import annotations

import re
import sys
import unicodedata
from typing import TYPE_CHECKING

from immich_memories.cli._helpers import console, print_error, print_info, print_success

if TYPE_CHECKING:
    from pathlib import Path

    from immich_memories.api.sync_client import SyncImmichClient
    from immich_memories.cli._live_display import ProgressDisplay
    from immich_memories.config_loader import Config

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def album_output_path(default_path: Path, album_name: str, container: str) -> Path:
    """Name the output file after the album, alongside the default output."""
    folded = unicodedata.normalize("NFKD", album_name).encode("ascii", "ignore").decode()
    slug = _SLUG_STRIP.sub("_", folded.lower()).strip("_")[:40]
    stem = f"album_{slug}" if slug else "album"
    return default_path.parent / f"{stem}.{container}"


def handle_album_generation(
    *,
    client: SyncImmichClient,
    config: Config,
    progress: ProgressDisplay,
    album_ref: str,
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
    upload_to_immich: bool,
    album: str | None,
    duration: float | int | None = None,
    orientation: str = "landscape",
    source: str = "manual",
    memory_key: str | None = None,
    memory_category: str | None = None,
    automation_attempt_id: str | None = None,
    dry_run: bool = False,
) -> None:
    """Generate one memory from the assets of a single Immich album."""
    import click

    from immich_memories.analysis.album_source import fetch_album_media
    from immich_memories.api.album_service import AlbumNotFoundError, AmbiguousAlbumError
    from immich_memories.cli._pipeline_runner import run_pipeline_and_generate
    from immich_memories.cli._trip_generation import resolve_music_arg
    from immich_memories.memory_types.registry import MemoryType
    from immich_memories.processing.encoding_plan import resolve_output_selection

    task = progress.add_task(f"Resolving album: {album_ref}...", total=None)
    try:
        resolved = client.resolve_album(album_ref)
    except (AlbumNotFoundError, AmbiguousAlbumError) as exc:
        progress.update(task, completed=True)
        progress.stop()
        raise click.ClickException(str(exc)) from exc
    progress.update(task, completed=True)
    print_success(f"Album: {resolved.name} ({resolved.asset_count} assets)")

    media = fetch_album_media(
        client,
        resolved,
        config=config,
        use_live_photos=use_live_photos,
        use_photos=use_photos,
    )
    if media.truncated:
        print_info(
            f"Album exceeds {config.analysis.max_album_assets} assets per type — "
            "using the most recent ones"
        )
    if media.date_range is None or not (media.videos or media.live_photo_clips or media.photos):
        print_error(f"No usable videos or photos in album: {resolved.name}")
        sys.exit(1)

    print_info(
        f"Album pool: {len(media.videos)} videos, "
        f"{len(media.live_photo_clips)} live photo clips, {len(media.photos)} photos"
    )

    output_selection = resolve_output_selection(
        config_codec=config.output.codec,
        config_container=config.output.format,
        format_override=output_format,
    )
    album_output = album_output_path(output_path, resolved.name, output_selection.container)

    result_path, should_upload, album_name = run_pipeline_and_generate(
        assets=media.videos,
        live_photo_clips=media.live_photo_clips,
        photo_assets=media.photos or None,
        include_photos=use_photos and bool(media.photos),
        analysis_depth=effective_analysis_depth,
        client=client,
        config=config,
        progress=progress,
        duration=float(duration) if duration is not None else None,
        transition=transition if transition != "smart" else config.defaults.transition,
        music=resolve_music_arg(music),
        music_volume=music_volume,
        no_music=no_music,
        output_path=album_output,
        output_resolution=resolution,
        output_orientation=orientation,
        scale_mode=scale_mode or config.defaults.scale_mode,
        output_format=output_format,
        add_date_overlay=add_date,
        add_place_overlay=add_place,
        debug_preserve_intermediates=keep_intermediates,
        privacy_mode=privacy_mode,
        # The album's own name is the best title we have; an explicit --title still wins.
        title_override=title_override or resolved.name,
        subtitle_override=subtitle_override,
        memory_type=MemoryType.ALBUM,
        person_names=person_names,
        date_range=media.date_range,
        upload_to_immich=upload_to_immich,
        album=album,
        memory_preset_params={"album_name": resolved.name, "album_id": resolved.id},
        source=source,
        memory_key=memory_key,
        memory_category=memory_category,
        automation_attempt_id=automation_attempt_id,
        dry_run=dry_run,
    )

    console.print()
    if dry_run:
        print_success(f"Album plan complete: {resolved.name}")
        return
    print_success(f"Album video: {result_path}")
    if should_upload:
        print_success(f"Uploaded to Immich (album: {album_name or 'none'})")
