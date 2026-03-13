"""Generate command for Immich Memories CLI."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.table import Table

from immich_memories.cli._date_resolution import resolve_date_range
from immich_memories.cli._helpers import console, print_error, print_info, print_success
from immich_memories.timeperiod import DateRange


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
        "--birthday", "-b", type=str, help="Birthday date for year calculation (use with --year)"
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
        "--month", type=int, default=None, help="Month 1-12 (use with monthly_highlights)"
    )
    @click.option(
        "--hemisphere",
        type=click.Choice(["north", "south"]),
        default="north",
        help="Hemisphere for season calculation",
    )
    @click.option("--duration", "-d", type=int, default=10, help="Target duration in minutes")
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
        type=click.Choice(["fit", "fill", "smart_crop"]),
        default="smart_crop",
        help="Scaling mode",
    )
    @click.option(
        "--transition",
        "-t",
        type=click.Choice(["cut", "crossfade", "none"]),
        default="crossfade",
        help="Transition style",
    )
    @click.option("--output", "-O", type=click.Path(), help="Output file path")
    @click.option("--music", "-m", type=click.Path(exists=True), help="Background music file")
    @click.option("--dry-run", is_flag=True, help="Show what would be done without generating")
    @click.option(
        "--upload-to-immich",
        is_flag=True,
        default=False,
        help="Upload generated video back to Immich",
    )
    @click.option("--album", type=str, default=None, help="Immich album name for uploaded video")
    @click.option(
        "--include-live-photos",
        is_flag=True,
        default=False,
        help="Include Live Photo video clips (3s iPhone clips, merged when burst-captured)",
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
        duration: int,
        orientation: str,
        scale_mode: str,
        transition: str,
        output: str | None,
        music: str | None,
        dry_run: bool,
        upload_to_immich: bool,
        album: str | None,
        include_live_photos: bool,
        trip_index: int | None,
        all_trips: bool,
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
        from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

        config = ctx.obj["config"]
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

        # Resolve date range(s)
        try:
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

        console.print()
        console.print("[bold]Immich Memories Generator[/bold]")
        console.print()

        # Show parameters
        table = Table(title="Generation Parameters")
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="green")

        if memory_type:
            table.add_row("Memory Type", memory_type)
        table.add_row("Time Period", date_range.description)
        table.add_row("Duration", f"{date_range.days} days")
        table.add_row("Person", ", ".join(person_names) if person_names else "All people")
        table.add_row("Target Duration", f"{duration} minutes")
        table.add_row("Orientation", orientation)
        table.add_row("Scale Mode", scale_mode)
        table.add_row("Transition", transition)
        table.add_row("Output", str(output_path))
        # Resolve live photos: CLI flag OR config
        use_live_photos = include_live_photos or config.analysis.include_live_photos
        if use_live_photos:
            table.add_row("Live Photos", "Enabled")
        if music:
            table.add_row("Music", music)

        console.print(table)
        console.print()

        if dry_run:
            print_info("Dry run - no video will be generated")
            return

        from immich_memories.api.immich import ImmichAPIError, SyncImmichClient

        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console,
            ) as progress:
                # Connect to Immich
                task = progress.add_task("Connecting to Immich...", total=None)

                with SyncImmichClient(
                    base_url=config.immich.url,
                    api_key=config.immich.api_key,
                ) as client:
                    progress.update(task, completed=True)
                    print_success("Connected to Immich")

                    # Trip detection flow: branch early
                    if memory_type == "trip" and year:
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
                            selected = select_trips(trips, trip_index, all_trips)
                        except ValueError as e:
                            print_error(str(e))
                            sys.exit(1)

                        if not selected:
                            print_info(
                                "Use --trip-index N to generate a specific trip, "
                                "or --all-trips to generate all"
                            )
                            return

                        # TODO: pipe selected trips into generation pipeline
                        for trip in selected:
                            print_info(
                                f"Would generate: {trip.location_name} "
                                f"({trip.start_date} to {trip.end_date}, "
                                f"{trip.asset_count} videos)"
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

                    # Fetch videos using date range(s)
                    task = progress.add_task("Fetching videos...", total=None)

                    all_assets = []
                    for dr in date_ranges:
                        if len(person_ids) > 1:
                            batch = client.get_videos_for_any_person(person_ids, dr)
                        elif len(person_ids) == 1:
                            batch = client.get_videos_for_person_and_date_range(person_ids[0], dr)
                        else:
                            batch = client.get_videos_for_date_range(dr)
                        all_assets.extend(batch)

                    # Deduplicate across date ranges
                    seen: dict[str, object] = {}
                    assets = []
                    for a in all_assets:
                        if a.id not in seen:
                            seen[a.id] = True
                            assets.append(a)

                    progress.update(task, completed=True)
                    print_success(f"Found {len(assets)} videos")

                    if not assets:
                        print_error("No videos found matching criteria")
                        sys.exit(1)

                    # Display video summary
                    console.print()
                    total_duration = sum(a.duration_seconds or 0 for a in assets)
                    console.print(f"Total video duration: {total_duration / 60:.1f} minutes")
                    console.print()

                    # TODO: Implement full generation pipeline
                    # This is a placeholder showing the structure

                    print_info("Video generation pipeline:")
                    console.print("  1. Download videos from Immich")
                    console.print("  2. Analyze scenes and detect moments")
                    console.print("  3. Select best moments for target duration")
                    console.print("  4. Apply aspect ratio transforms")
                    console.print("  5. Assemble final video with transitions")
                    if music:
                        console.print("  6. Add background music")

                    console.print()
                    print_info("Full generation pipeline coming soon!")
                    print_info(f"Output would be saved to: {output_path}")

                    # Upload to Immich if requested (or config-enabled)
                    should_upload = upload_to_immich or config.upload.enabled
                    album_name = album or config.upload.album_name
                    if should_upload and output_path.exists():
                        task = progress.add_task("Uploading to Immich...", total=None)
                        result = client.upload_memory(output_path, album_name=album_name)
                        progress.update(task, completed=True)
                        print_success(f"Uploaded to Immich (asset: {result['asset_id']})")
                        if result["album_id"]:
                            print_success(f"Added to album: {album_name}")

        except ImmichAPIError as e:
            print_error(f"Immich API error: {e}")
            sys.exit(1)
        except Exception as e:
            print_error(f"Error: {e}")
            sys.exit(1)

    # Register analyze and export-project commands from separate module
    from immich_memories.cli._analyze_export import register_analyze_export_commands

    register_analyze_export_commands(main)
