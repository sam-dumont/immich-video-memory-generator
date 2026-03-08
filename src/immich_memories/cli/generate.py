"""Generate and analyze commands for Immich Memories CLI."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import click
from rich.table import Table

from immich_memories.cli._helpers import console, print_error, print_info, print_success
from immich_memories.timeperiod import (
    DateRange,
    birthday_year,
    calendar_year,
    custom_range,
    from_period,
    parse_date,
)


def _resolve_date_range(
    year: int | None,
    start: str | None,
    end: str | None,
    period: str | None,
    birthday: str | None,
) -> DateRange:
    """Resolve date range from command line options.

    Priority:
    1. --start and --end (custom range)
    2. --start and --period (period from start)
    3. --year with --birthday (birthday-based year)
    4. --year alone (calendar year)

    Returns:
        DateRange for the selected period

    Raises:
        click.UsageError: If invalid combination of options
    """
    # Custom range with start and end
    if start and end:
        try:
            start_date = parse_date(start)
            end_date = parse_date(end)
            return custom_range(start_date, end_date)
        except ValueError as e:
            raise click.UsageError(str(e))

    # Period from start date
    if start and period:
        try:
            start_date = parse_date(start)
            return from_period(start_date, period)
        except ValueError as e:
            raise click.UsageError(str(e))

    # Year-based options
    if year:
        if birthday:
            try:
                bday = parse_date(birthday)
                return birthday_year(bday, year)
            except ValueError as e:
                raise click.UsageError(str(e))
        else:
            return calendar_year(year)

    # No valid combination
    raise click.UsageError(
        "You must specify a time period. Use one of:\n"
        "  --year YEAR                    Calendar year (Jan 1 - Dec 31)\n"
        "  --year YEAR --birthday DATE    Year from birthday (e.g., Feb 7 - Feb 6)\n"
        "  --start DATE --end DATE        Custom date range\n"
        "  --start DATE --period PERIOD   Period from start (e.g., 6m, 1y)"
    )


def register_generate_commands(main: click.Group) -> None:
    """Register generate, analyze, and export-project commands on the main CLI group."""

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
    @click.option("--person", "-p", type=str, help="Person name to filter by")
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
    @click.pass_context
    def generate(
        ctx: click.Context,
        year: int | None,
        start: str | None,
        end: str | None,
        period: str | None,
        birthday: str | None,
        person: str | None,
        duration: int,
        orientation: str,
        scale_mode: str,
        transition: str,
        output: str | None,
        music: str | None,
        dry_run: bool,
    ) -> None:
        """Generate a video compilation.

        Time period options:

        \b
        Calendar year:
          --year 2024                    (Jan 1, 2024 - Dec 31, 2024)

        \b
        Birthday-based year:
          --year 2024 --birthday 02/07   (Feb 7, 2024 - Feb 6, 2025)

        \b
        Custom date range:
          --start 2024-01-01 --end 2024-06-30

        \b
        Period from start date:
          --start 2024-01-01 --period 6m   (6 months)
          --start 2024-01-01 --period 1y   (1 year)
        """
        from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn

        config = ctx.obj["config"]

        if not config.immich.url or not config.immich.api_key:
            print_error("Immich not configured. Run 'immich-memories config' first.")
            sys.exit(1)

        # Resolve date range
        try:
            date_range = _resolve_date_range(year, start, end, period, birthday)
        except click.UsageError:
            raise

        # Determine output path
        if output:
            output_path = Path(output)
        else:
            output_dir = config.output.output_path
            output_dir.mkdir(parents=True, exist_ok=True)
            person_slug = person.lower().replace(" ", "_") if person else "all"
            # Use descriptive filename based on date range
            if date_range.is_calendar_year:
                date_slug = str(date_range.start.year)
            else:
                date_slug = (
                    f"{date_range.start.strftime('%Y%m%d')}-{date_range.end.strftime('%Y%m%d')}"
                )
            output_path = output_dir / f"{person_slug}_{date_slug}_memories.mp4"

        console.print()
        console.print("[bold]Immich Memories Generator[/bold]")
        console.print()

        # Show parameters
        table = Table(title="Generation Parameters")
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Time Period", date_range.description)
        table.add_row("Duration", f"{date_range.days} days")
        table.add_row("Person", person or "All people")
        table.add_row("Target Duration", f"{duration} minutes")
        table.add_row("Orientation", orientation)
        table.add_row("Scale Mode", scale_mode)
        table.add_row("Transition", transition)
        table.add_row("Output", str(output_path))
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

                    # Find person if specified
                    person_id = None
                    if person:
                        task = progress.add_task(f"Finding person: {person}...", total=None)
                        found_person = client.get_person_by_name(person)
                        if not found_person:
                            print_error(f"Person not found: {person}")
                            sys.exit(1)
                        person_id = found_person.id
                        progress.update(task, completed=True)
                        print_success(f"Found person: {found_person.name}")

                    # Fetch videos using date range
                    task = progress.add_task("Fetching videos...", total=None)

                    if person_id:
                        assets = client.get_videos_for_person_and_date_range(person_id, date_range)
                    else:
                        assets = client.get_videos_for_date_range(date_range)

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

        except ImmichAPIError as e:
            print_error(f"Immich API error: {e}")
            sys.exit(1)
        except Exception as e:
            print_error(f"Error: {e}")
            sys.exit(1)

    @main.command()
    @click.option("--year", "-y", type=int, help="Year to analyze")
    @click.option("--force", "-f", is_flag=True, help="Force re-analysis of cached videos")
    @click.pass_context
    def analyze(ctx: click.Context, year: int | None, force: bool) -> None:  # noqa: ARG001
        """Analyze videos and cache metadata."""
        from rich.progress import Progress, SpinnerColumn, TextColumn

        config = ctx.obj["config"]

        if not config.immich.url or not config.immich.api_key:
            print_error("Immich not configured. Run 'immich-memories config' first.")
            sys.exit(1)

        from immich_memories.api.immich import SyncImmichClient

        console.print("[bold]Analyzing videos...[/bold]")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Connecting to Immich...", total=None)

            with SyncImmichClient(
                base_url=config.immich.url,
                api_key=config.immich.api_key,
            ) as client:
                progress.update(task, completed=True)

                if year:
                    years = [year]
                else:
                    task = progress.add_task("Getting available years...", total=None)
                    years = client.get_available_years()
                    progress.update(task, completed=True)

                for y in years:
                    task = progress.add_task(f"Fetching videos for {y}...", total=None)
                    assets = client.get_all_videos_for_year(y)
                    progress.update(task, completed=True)
                    console.print(f"  {y}: {len(assets)} videos")

        print_success("Analysis complete")

    @main.command("export-project")
    @click.option("--year", "-y", type=int, required=True, help="Year")
    @click.option("--person", "-p", type=str, help="Person name")
    @click.option("--output", "-o", type=click.Path(), required=True, help="Output JSON file")
    @click.pass_context
    def export_project(
        ctx: click.Context,
        year: int,
        person: str | None,
        output: str,
    ) -> None:
        """Export project state for later editing."""
        config = ctx.obj["config"]

        if not config.immich.url or not config.immich.api_key:
            print_error("Immich not configured.")
            sys.exit(1)

        from immich_memories.api.immich import SyncImmichClient

        with SyncImmichClient(
            base_url=config.immich.url,
            api_key=config.immich.api_key,
        ) as client:
            # Find person
            person_id = None
            person_name = None
            if person:
                found = client.get_person_by_name(person)
                if found:
                    person_id = found.id
                    person_name = found.name

            # Get assets
            if person_id:
                assets = client.get_videos_for_person_and_year(person_id, year)
            else:
                assets = client.get_all_videos_for_year(year)

            # Build project structure
            project = {
                "version": "1.0",
                "created_at": datetime.now().isoformat(),
                "settings": {
                    "year": year,
                    "person_id": person_id,
                    "person_name": person_name,
                },
                "clips": [
                    {
                        "asset_id": a.id,
                        "filename": a.original_file_name,
                        "date": a.file_created_at.isoformat(),
                        "duration": a.duration_seconds,
                        "selected": True,
                        "segment": {
                            "start": 0,
                            "end": a.duration_seconds or 0,
                        },
                    }
                    for a in assets
                ],
            }

            # Write to file
            output_path = Path(output)
            with open(output_path, "w") as f:
                json.dump(project, f, indent=2)

            print_success(f"Project exported to {output_path}")
            console.print(f"  {len(assets)} clips included")
