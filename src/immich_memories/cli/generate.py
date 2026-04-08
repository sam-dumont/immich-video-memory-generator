"""Generate command for Immich Memories CLI."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.table import Table

from immich_memories.cli._date_resolution import (
    default_duration_for_type,
    duration_from_date_range,
    resolve_date_range,
)
from immich_memories.cli._helpers import console, print_error, print_info, print_success
from immich_memories.cli._pipeline_runner import (
    fetch_videos_and_live_photos,
    run_pipeline_and_generate,
)
from immich_memories.cli._trip_generation import handle_trip_generation, resolve_music_arg
from immich_memories.timeperiod import DateRange

if TYPE_CHECKING:
    from immich_memories.config_loader import Config


def _build_params_table(
    *,
    config: Config,
    memory_type: str | None,
    date_range: DateRange,
    person_names: list[str],
    duration: float,
    orientation: str,
    scale_mode: str | None,
    transition: str,
    resolution: str,
    output_format: str,
    output_path: Path,
    add_date: bool,
    keep_intermediates: bool,
    privacy_mode: bool,
    title_override: str | None,
    subtitle_override: str | None,
    use_live_photos: bool,
    music: str | None,
    music_volume: float,
    no_music: bool = False,
) -> Table:
    """Build a Rich table displaying generation parameters."""
    table = Table(title="Generation Parameters")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    if memory_type:
        table.add_row("Memory Type", memory_type)
    table.add_row("Time Period", date_range.description)
    table.add_row("Duration", f"{date_range.days} days")
    table.add_row("Person", ", ".join(person_names) if person_names else "All people")
    dur_display = f"{duration / 60:.1f} min" if duration >= 60 else f"{duration:.0f}s"
    table.add_row("Target Duration", dur_display)
    table.add_row("Orientation", orientation)
    table.add_row("Scale Mode", scale_mode or config.defaults.scale_mode)
    table.add_row("Transition", transition)
    table.add_row("Resolution", resolution)
    table.add_row("Format", output_format)
    table.add_row("Output", str(output_path))
    if add_date:
        table.add_row("Date Overlay", "Enabled")
    if keep_intermediates:
        table.add_row("Keep Intermediates", "Enabled")
    if privacy_mode:
        table.add_row("Privacy Mode", "Enabled (blur faces, mute speech)")
    if title_override:
        table.add_row("Title Override", title_override)
    if subtitle_override:
        table.add_row("Subtitle Override", subtitle_override)
    if use_live_photos:
        table.add_row("Live Photos", "Enabled")
    if no_music:
        table.add_row("Music", "Disabled")
    elif music and music != "auto":
        table.add_row("Music", music)
        table.add_row("Music Volume", f"{int(music_volume * 100)}%")
    elif music == "auto" or _has_music_backends(config):
        table.add_row("Music", "Auto (AI-generated)")
        table.add_row("Music Volume", f"{int(music_volume * 100)}%")

    return table


def _has_music_backends(config: Config) -> bool:
    """Check if any music generation backend is enabled in config."""
    from immich_memories.generate_music import music_config_available

    return music_config_available(config)


def register_generate_commands(main: click.Group) -> None:
    """Register the generate command on the main CLI group."""

    @main.command()
    @click.option(
        "--year", "-y", type=int, help="Year to generate video for (calendar year by default)"
    )
    @click.option("--start", type=str, help="Start date (YYYY-MM-DD or DD/MM/YYYY)")
    @click.option("--end", type=str, help="End date (use with --start)")
    @click.option("--period", type=str, help="Period from start date (e.g., 6m, 1y, 2w)")
    @click.option(
        "--birthday",
        "-b",
        is_flag=False,
        flag_value="auto",
        default=None,
        help="Use birthday-based year (auto-detects from Immich, or specify MM/DD)",
    )
    @click.option("--person", "-p", type=str, multiple=True, help="Person name (repeatable)")
    @click.option(
        "--memory-type",
        type=click.Choice(
            [
                "year_in_review",
                "season",
                "person_spotlight",
                "multi_person",
                "monthly_highlights",
                "on_this_day",
                "trip",
            ]
        ),
        default=None,
        help="Memory type preset",
    )
    @click.option(
        "--season",
        type=click.Choice(["spring", "summer", "fall", "autumn", "winter"]),
        default=None,
        help="Season (use with --memory-type season)",
    )
    @click.option(
        "--month",
        type=int,
        default=None,
        help="Month 1-12 (narrows any yearly memory type; selects trip by month)",
    )
    @click.option(
        "--hemisphere",
        type=click.Choice(["north", "south"]),
        default="north",
        help="Hemisphere for season calculation",
    )
    @click.option(
        "--duration",
        "-d",
        type=int,
        default=None,
        help="Target duration in seconds (default: from memory type preset)",
    )
    @click.option(
        "--orientation",
        "-o",
        type=click.Choice(["landscape", "portrait", "square"]),
        default="landscape",
        help="Output orientation",
    )
    @click.option(
        "--scale-mode",
        "-s",
        type=click.Choice(["fit", "fill", "smart_crop", "blur"]),
        default=None,
        help="Scale mode (default: from config or smart_crop)",
    )
    @click.option(
        "--transition",
        "-t",
        type=click.Choice(["smart", "cut", "crossfade", "none"]),
        default="smart",
        help="Transition style (default: smart — mix of fades & cuts)",
    )
    @click.option(
        "--resolution",
        "-r",
        type=click.Choice(["auto", "4k", "1080p", "720p"]),
        default=None,
        help="Output resolution (default: config value, 'auto' to match source clips)",
    )
    @click.option(
        "--music-volume",
        type=float,
        default=0.5,
        help="Music volume 0.0-1.0 (default: 0.5)",
    )
    @click.option(
        "--format",
        "output_format",
        type=click.Choice(["mp4", "prores"]),
        default="mp4",
        help="Output format",
    )
    @click.option(
        "--quality",
        "-q",
        type=click.Choice(["high", "medium", "low"]),
        default=None,
        help="Output quality (default: from config, typically high)",
    )
    @click.option("--output", "-O", type=click.Path(), help="Output file path")
    @click.option(
        "--music",
        "-m",
        type=str,
        default=None,
        help="Music: path to audio file, 'auto' to generate from config, or omit for default behavior",
    )
    @click.option(
        "--no-music",
        "no_music",
        is_flag=True,
        default=False,
        help="Disable all music (skip both provided files and AI generation)",
    )
    @click.option("--dry-run", is_flag=True, help="Show what would be done without generating")
    @click.option(
        "--upload-to-immich",
        is_flag=True,
        default=False,
        help="Upload generated video back to Immich",
    )
    @click.option("--album", type=str, default=None, help="Immich album name for uploaded video")
    @click.option("--add-date", is_flag=True, default=False, help="Add date overlay to clips")
    @click.option(
        "--keep-intermediates",
        is_flag=True,
        default=False,
        help="Keep intermediate files for debugging",
    )
    @click.option("--privacy-mode", is_flag=True, default=False, help="Blur faces and mute speech")
    @click.option(
        "--title",
        "title_override",
        type=str,
        default=None,
        help="Override video title text",
    )
    @click.option(
        "--subtitle",
        "subtitle_override",
        type=str,
        default=None,
        help="Override video subtitle text",
    )
    @click.option(
        "--include-live-photos",
        is_flag=True,
        default=False,
        help="Include Live Photo video clips (3s iPhone clips, merged when burst-captured)",
    )
    @click.option(
        "--include-photos",
        is_flag=True,
        default=False,
        help="Include photos as animated Ken Burns clips (blur background, face-aware pan)",
    )
    @click.option(
        "--photo-duration",
        type=float,
        default=None,
        help="Duration per photo clip in seconds (default: 4.0)",
    )
    @click.option(
        "--analysis-depth",
        type=click.Choice(["fast", "thorough"]),
        default=None,
        help="Analysis depth: fast (metadata gap-fill) or thorough (LLM gap-fill)",
    )
    @click.option(
        "--trip-index",
        type=int,
        default=None,
        help="Select a specific trip by index (use with --memory-type trip)",
    )
    @click.option(
        "--all-trips",
        is_flag=True,
        default=False,
        help="Generate a video for every detected trip (use with --memory-type trip)",
    )
    @click.option(
        "--years-back",
        type=int,
        default=None,
        help="Years to look back for on_this_day (default: all)",
    )
    @click.option(
        "--near-date",
        type=str,
        default=None,
        help="Select trip closest to this date (YYYY-MM-DD, use with --memory-type trip)",
    )
    @click.option("--quiet", is_flag=True, help="Suppress interactive progress, emit log lines")
    @click.pass_context
    def generate(
        ctx: click.Context,
        year: int | None,
        start: str | None,
        end: str | None,
        period: str | None,
        birthday: str | None,
        person: tuple[str, ...],
        memory_type: str | None,
        season: str | None,
        month: int | None,
        hemisphere: str,
        duration: float,
        orientation: str,
        scale_mode: str | None,
        transition: str,
        resolution: str,
        music_volume: float,
        output_format: str,
        quality: str | None,
        output: str | None,
        music: str | None,
        no_music: bool,
        dry_run: bool,
        upload_to_immich: bool,
        album: str | None,
        add_date: bool,
        keep_intermediates: bool,
        privacy_mode: bool,
        title_override: str | None,
        subtitle_override: str | None,
        include_live_photos: bool,
        include_photos: bool,
        photo_duration: float | None,
        analysis_depth: str | None,
        trip_index: int | None,
        all_trips: bool,
        years_back: int | None,
        near_date: str | None,
        quiet: bool,
    ) -> None:
        """Generate a video compilation.

        \b
        Memory type presets:
          --memory-type season --season summer --year 2024
          --memory-type person_spotlight --person "Alice" --year 2024
          --memory-type multi_person --person "Alice" --person "Bob" --year 2024
          --memory-type monthly_highlights --month 7 --year 2024
          --memory-type on_this_day

        \b
        Manual time period options:
          --year 2024                    Calendar year
          --year 2024 --birthday 02/07   Birthday-based year
          --start 2024-01-01 --end 2024-06-30   Custom range
          --start 2024-01-01 --period 6m        Period from start
        """
        from immich_memories.cli._live_display import LiveDisplay, ProgressDisplay, QuietDisplay

        config = ctx.obj["config"]

        # CLI quality flag overrides config
        if quality:
            config.output.quality = quality
            config.output.crf = None  # Let quality preset determine CRF

        person_names = list(person) if person else []

        if not config.immich.url or not config.immich.api_key:
            print_error("Immich not configured. Run 'immich-memories config' first.")
            sys.exit(1)

        # Validate memory type constraints
        if memory_type in ("person_spotlight", "multi_person") and not person_names:
            print_error(f"--person is required with --memory-type {memory_type}")
            sys.exit(1)

        if memory_type == "trip" and not year:
            print_error("--year is required with --memory-type trip")
            sys.exit(1)

        if (trip_index is not None or all_trips) and memory_type != "trip":
            print_error("--trip-index and --all-trips require --memory-type trip")
            sys.exit(1)

        if near_date and memory_type != "trip":
            print_error("--near-date requires --memory-type trip")
            sys.exit(1)

        if years_back is not None and memory_type != "on_this_day":
            print_error("--years-back requires --memory-type on_this_day")
            sys.exit(1)

        # Resolve date range(s)
        # WHY: birthday="auto" means detect from Immich later — don't pass to parser
        initial_birthday = None if birthday == "auto" else birthday
        try:
            date_result = resolve_date_range(
                year,
                start,
                end,
                period,
                initial_birthday,
                memory_type=memory_type,
                season=season,
                month=month,
                hemisphere=hemisphere,
                years_back=years_back,
            )
        except click.UsageError:
            raise

        # Normalize to single DateRange for display (multi-range for on_this_day)
        if isinstance(date_result, list):
            date_ranges = date_result
            # Use first and last range for display
            if date_ranges:
                date_range = DateRange(
                    start=date_ranges[-1].start,
                    end=date_ranges[0].end,
                )
            else:
                print_error("No date ranges generated for On This Day")
                sys.exit(1)
        else:
            date_range = date_result
            date_ranges = [date_result]

        # Determine output path
        if output:
            output_path = Path(output)
        else:
            output_dir = config.output.output_path
            output_dir.mkdir(parents=True, exist_ok=True)
            person_slug = (
                "_".join(n.lower().replace(" ", "_") for n in person_names)
                if person_names
                else "all"
            )
            type_slug = memory_type or "memories"
            if date_range.is_calendar_year:
                date_slug = str(date_range.start.year)
            else:
                date_slug = (
                    f"{date_range.start.strftime('%Y%m%d')}-{date_range.end.strftime('%Y%m%d')}"
                )
            output_path = output_dir / f"{person_slug}_{type_slug}_{date_slug}.mp4"

        if not quiet:
            console.print()
            console.print("[bold]Immich Memories Generator[/bold]")
            console.print()

        # Resolve live photos: CLI flag OR config
        use_live_photos = include_live_photos or config.analysis.include_live_photos

        # Resolve photo inclusion: CLI flag OR config
        use_photos = include_photos or config.photos.enabled
        if photo_duration is not None:
            config.photos.duration = photo_duration

        # Analysis depth: CLI override → stored for PipelineConfig
        effective_analysis_depth = analysis_depth or "fast"

        # Infer memory type from context when not explicitly set
        if memory_type is None and person_names:
            memory_type = "person_spotlight" if len(person_names) == 1 else "multi_person"

        # Resolve duration: CLI --duration > memory type default > date-range scaling
        if duration is None:
            duration = default_duration_for_type(memory_type, date_range)
            if duration is None:
                duration = duration_from_date_range(date_range)

        table = _build_params_table(
            config=config,
            memory_type=memory_type,
            date_range=date_range,
            person_names=person_names,
            duration=duration,
            orientation=orientation,
            scale_mode=scale_mode,
            transition=transition,
            resolution=resolution,
            output_format=output_format,
            output_path=output_path,
            add_date=add_date,
            keep_intermediates=keep_intermediates,
            privacy_mode=privacy_mode,
            title_override=title_override,
            subtitle_override=subtitle_override,
            use_live_photos=use_live_photos,
            music=music,
            music_volume=music_volume,
            no_music=no_music,
        )
        show_interactive = not quiet and sys.stdout.isatty()
        if not show_interactive:
            from immich_memories.cli._helpers import set_quiet_mode

            set_quiet_mode(True)
        else:
            console.print(table)
            console.print()

        if dry_run:
            print_info("Dry run - no video will be generated")
            return

        from immich_memories.api.immich import ImmichAPIError, SyncImmichClient
        from immich_memories.generate import GenerationError

        try:
            # WHY: LiveDisplay coordinates Rich Live output with logging to
            # prevent raw log lines from breaking cursor-controlled rendering.
            def _make_progress(interactive: bool) -> ProgressDisplay:
                if interactive:
                    return LiveDisplay(console=console)
                return QuietDisplay()

            with _make_progress(show_interactive) as progress:
                # Connect to Immich
                task = progress.add_task("Connecting to Immich...", total=None)

                with SyncImmichClient(
                    base_url=config.immich.url,
                    api_key=config.immich.api_key,
                ) as client:
                    progress.update(task, completed=True)
                    # Trip detection flow: branch early
                    if memory_type == "trip" and year:
                        handle_trip_generation(
                            client=client,
                            config=config,
                            progress=progress,
                            year=year,
                            month=month,
                            trip_index=trip_index,
                            all_trips=all_trips,
                            near_date=near_date,
                            person_names=person_names,
                            output_path=output_path,
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
                        )
                        return

                    # Find person(s) if specified
                    person_ids: list[str] = []
                    if person_names:
                        for pname in person_names:
                            task = progress.add_task(f"Finding person: {pname}...", total=None)
                            found_person = client.get_person_by_name(pname)
                            if not found_person:
                                print_error(f"Person not found: {pname}")
                                sys.exit(1)
                            person_ids.append(found_person.id)
                            progress.update(task, completed=True)
                            print_success(f"Found person: {found_person.name}")

                    # When --birthday flag used without value, detect from Immich
                    if birthday == "auto" and person_names:
                        found = client.get_person_by_name(person_names[0])
                        if found and found.birth_date:
                            birthday = found.birth_date.strftime("%m/%d")
                            print_success(f"Using birthday: {birthday}")
                        else:
                            print_error(f"No birthday found in Immich for {person_names[0]}")
                            sys.exit(1)

                        # Re-resolve date range with detected birthday
                        if birthday and birthday != "auto":
                            date_result = resolve_date_range(
                                year,
                                start,
                                end,
                                period,
                                birthday,
                                memory_type=memory_type,
                                season=season,
                                month=month,
                                hemisphere=hemisphere,
                                years_back=years_back,
                            )
                            if isinstance(date_result, list):
                                date_ranges = date_result
                                date_range = DateRange(
                                    start=date_ranges[-1].start, end=date_ranges[0].end
                                )
                            else:
                                date_range = date_result
                                date_ranges = [date_result]

                    # Fetch videos and optionally live photos
                    assets, live_photo_clips = fetch_videos_and_live_photos(
                        client=client,
                        config=config,
                        progress=progress,
                        date_ranges=date_ranges,
                        person_ids=person_ids,
                        use_live_photos=use_live_photos,
                    )

                    # Fetch photos (if enabled)
                    fetched_photos: list = []
                    if use_photos:
                        for dr in date_ranges:
                            pid = person_ids[0] if len(person_ids) == 1 else None
                            fetched_photos.extend(
                                client.get_photos_for_date_range(dr, person_id=pid)
                            )
                        if fetched_photos:
                            print_info(f"Found {len(fetched_photos)} photos")

                    if not assets and not live_photo_clips and not fetched_photos:
                        print_error("No videos or photos found matching criteria")
                        sys.exit(1)

                    # Display video summary
                    total_dur = sum(a.duration_seconds or 0 for a in assets)
                    print_info(f"Total video duration: {total_dur / 60:.1f} minutes")
                    if live_photo_clips:
                        print_info(f"Live photo clips: {len(live_photo_clips)}")
                    if fetched_photos:
                        print_info(f"Photo clips to render: {len(fetched_photos)}")

                    # Config fallbacks: CLI flag > config > hardcoded default
                    effective_transition = (
                        transition if transition != "smart" else config.defaults.transition
                    )
                    effective_scale_mode = scale_mode or config.defaults.scale_mode

                    resolved_music = resolve_music_arg(music)

                    result_path, should_upload, album_name = run_pipeline_and_generate(
                        assets=assets,
                        live_photo_clips=live_photo_clips,
                        photo_assets=fetched_photos if use_photos else None,
                        include_photos=use_photos and bool(fetched_photos),
                        analysis_depth=effective_analysis_depth,
                        client=client,
                        config=config,
                        progress=progress,
                        duration=duration,
                        transition=effective_transition,
                        music=resolved_music,
                        music_volume=music_volume,
                        no_music=no_music,
                        output_path=output_path,
                        output_resolution=resolution,
                        scale_mode=effective_scale_mode,
                        output_format=output_format,
                        add_date_overlay=add_date,
                        debug_preserve_intermediates=keep_intermediates,
                        privacy_mode=privacy_mode,
                        title_override=title_override,
                        subtitle_override=subtitle_override,
                        memory_type=memory_type,
                        person_names=person_names,
                        date_range=date_range,
                        upload_to_immich=upload_to_immich,
                        album=album,
                    )

                print_success(f"Video saved to: {result_path}")
                if should_upload:
                    print_success(f"Uploaded to Immich (album: {album_name or 'none'})")

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

    # Register analyze and export-project commands from separate module
    from immich_memories.cli._analyze_export import register_analyze_export_commands

    register_analyze_export_commands(main)
