"""Command-line interface for Immich Memories."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table

from immich_memories import __version__
from immich_memories.config import Config, get_config, init_config_dir
from immich_memories.timeperiod import (
    DateRange,
    birthday_year,
    calendar_year,
    custom_range,
    from_period,
    parse_date,
)

console = Console()


def print_error(message: str) -> None:
    """Print an error message."""
    console.print(f"[red]Error:[/red] {message}")


def print_success(message: str) -> None:
    """Print a success message."""
    console.print(f"[green]✓[/green] {message}")


def print_info(message: str) -> None:
    """Print an info message."""
    console.print(f"[blue]ℹ[/blue] {message}")


@click.group()
@click.version_option(version=__version__)
@click.option("--config", "-c", type=click.Path(), help="Path to config file")
@click.pass_context
def main(ctx: click.Context, config: str | None) -> None:
    """Immich Memories - Create video compilations from your Immich library."""
    ctx.ensure_object(dict)

    # Initialize config directory
    init_config_dir()

    # Load configuration
    if config:
        config_path = Path(config)
        ctx.obj["config"] = Config.from_yaml(config_path)
    else:
        ctx.obj["config"] = get_config()


@main.command()
@click.option("--port", "-p", default=8080, help="Port to run the UI on")
@click.option("--host", "-h", default="0.0.0.0", help="Host to bind to")  # noqa: S104 - intentional for Docker/container binding
@click.option(
    "--reload/--no-reload", default=False, help="Enable hot reload (for development only)"
)
def ui(port: int, host: str, reload: bool) -> None:
    """Launch the interactive NiceGUI UI."""
    print_info(f"Starting Immich Memories UI on http://{host}:{port}")

    # Import the app module to register routes and run
    from immich_memories.ui.app import main as ui_main  # noqa: F401

    try:
        ui_main(port=port, host=host, reload=reload)
    except KeyboardInterrupt:
        print_info("Shutting down...")


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
            date_slug = f"{date_range.start.strftime('%Y%m%d')}-{date_range.end.strftime('%Y%m%d')}"
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

    import json

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


@main.command()
@click.option("--url", "-u", type=str, help="Immich server URL")
@click.option("--api-key", "-k", type=str, help="Immich API key")
@click.option("--show", "-s", is_flag=True, help="Show current configuration")
@click.pass_context
def config(ctx: click.Context, url: str | None, api_key: str | None, show: bool) -> None:
    """Configure Immich connection settings."""
    cfg = ctx.obj["config"]
    config_path = Config.get_default_path()

    if show:
        # Display current config
        table = Table(title="Current Configuration")
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Config file", str(config_path))
        table.add_row("Immich URL", cfg.immich.url or "(not set)")
        table.add_row("API Key", "****" if cfg.immich.api_key else "(not set)")
        table.add_row("Output directory", str(cfg.output.output_path))
        table.add_row("Default orientation", cfg.defaults.output_orientation)
        table.add_row("Default scale mode", cfg.defaults.scale_mode)

        console.print(table)
        return

    if url:
        cfg.immich.url = url
    if api_key:
        cfg.immich.api_key = api_key

    if url or api_key:
        cfg.save_yaml(config_path)
        print_success(f"Configuration saved to {config_path}")
    else:
        # Interactive configuration
        console.print("[bold]Immich Memories Configuration[/bold]")
        console.print()

        new_url = click.prompt(
            "Immich server URL",
            default=cfg.immich.url or "https://photos.example.com",
        )
        new_api_key = click.prompt(
            "API key",
            default=cfg.immich.api_key or "",
            hide_input=True,
        )

        cfg.immich.url = new_url
        cfg.immich.api_key = new_api_key
        cfg.save_yaml(config_path)

        print_success(f"Configuration saved to {config_path}")

        # Test connection
        if click.confirm("Test connection now?", default=True):
            from immich_memories.api.immich import ImmichAPIError, SyncImmichClient

            try:
                with SyncImmichClient(
                    base_url=new_url,
                    api_key=new_api_key,
                ) as client:
                    user = client.get_current_user()
                    print_success(f"Connected! Logged in as: {user.name or user.email}")
            except ImmichAPIError as e:
                print_error(f"Connection failed: {e}")


@main.command()
@click.pass_context
def people(ctx: click.Context) -> None:
    """List all people in Immich."""
    cfg = ctx.obj["config"]

    if not cfg.immich.url or not cfg.immich.api_key:
        print_error("Immich not configured. Run 'immich-memories config' first.")
        sys.exit(1)

    from immich_memories.api.immich import SyncImmichClient

    with SyncImmichClient(
        base_url=cfg.immich.url,
        api_key=cfg.immich.api_key,
    ) as client:
        people_list = client.get_all_people()

        table = Table(title="People in Immich")
        table.add_column("Name", style="cyan")
        table.add_column("ID", style="dim")

        for person in sorted(people_list, key=lambda p: p.name or ""):
            if person.name:
                table.add_row(person.name, person.id[:8] + "...")

        console.print(table)
        console.print(f"\nTotal: {len([p for p in people_list if p.name])} named people")


@main.command()
@click.pass_context
def years(ctx: click.Context) -> None:
    """List years with video content."""
    cfg = ctx.obj["config"]

    if not cfg.immich.url or not cfg.immich.api_key:
        print_error("Immich not configured. Run 'immich-memories config' first.")
        sys.exit(1)

    from immich_memories.api.immich import SyncImmichClient

    with SyncImmichClient(
        base_url=cfg.immich.url,
        api_key=cfg.immich.api_key,
    ) as client:
        years_list = client.get_available_years()

        console.print("[bold]Years with video content:[/bold]")
        for year in years_list:
            console.print(f"  • {year}")


@main.command()
@click.option("--verbose", "-v", is_flag=True, help="Show detailed output")
@click.pass_context
def preflight(ctx: click.Context, verbose: bool) -> None:
    """Run preflight checks to validate all provider connections.

    Checks:
    - Immich server connection and API key
    - Ollama availability (for mood/content analysis)
    - OpenAI API key (fallback for analysis)
    - Pixabay API key (for music search)
    - Hardware acceleration
    """
    from immich_memories.preflight import CheckStatus, run_preflight_checks

    config = ctx.obj["config"]

    console.print("[bold]Running Preflight Checks[/bold]")
    console.print()

    results = run_preflight_checks(config)

    # Build results table
    table = Table(title="Provider Status")
    table.add_column("Provider", style="cyan")
    table.add_column("Status")
    table.add_column("Message")
    if verbose:
        table.add_column("Details", style="dim")

    status_styles = {
        CheckStatus.OK: "[green]OK[/green]",
        CheckStatus.WARNING: "[yellow]WARNING[/yellow]",
        CheckStatus.ERROR: "[red]ERROR[/red]",
        CheckStatus.SKIPPED: "[dim]SKIPPED[/dim]",
    }

    for check in results.checks:
        row = [
            check.name,
            status_styles.get(check.status, str(check.status)),
            check.message,
        ]
        if verbose:
            row.append(check.details or "")
        table.add_row(*row)

    console.print(table)
    console.print()

    # Summary
    ok_count = sum(1 for c in results.checks if c.status == CheckStatus.OK)
    warn_count = sum(1 for c in results.checks if c.status == CheckStatus.WARNING)
    error_count = sum(1 for c in results.checks if c.status == CheckStatus.ERROR)
    skip_count = sum(1 for c in results.checks if c.status == CheckStatus.SKIPPED)

    if results.all_ok:
        if results.has_warnings:
            print_info(
                f"Preflight complete: {ok_count} OK, {warn_count} warnings, {skip_count} skipped"
            )
        else:
            print_success(f"All checks passed! ({ok_count} OK, {skip_count} skipped)")
    else:
        print_error(f"Preflight failed: {error_count} errors, {warn_count} warnings")
        console.print()
        console.print("[dim]Fix the errors above before proceeding.[/dim]")
        sys.exit(1)


@main.command("hardware")
def hardware_info() -> None:
    """Show hardware acceleration information."""
    from immich_memories.processing.hardware import (
        HWAccelBackend,
        detect_hardware_acceleration,
    )

    console.print("[bold]Hardware Acceleration Detection[/bold]")
    console.print()

    caps = detect_hardware_acceleration()

    if caps.backend == HWAccelBackend.NONE:
        console.print("[yellow]No hardware acceleration detected[/yellow]")
        console.print()
        console.print("Video encoding will use CPU (libx264).")
        console.print()
        console.print("To enable hardware acceleration:")
        console.print("  • NVIDIA: Install CUDA drivers and FFmpeg with NVENC support")
        console.print("  • Apple: Use macOS with VideoToolbox (built-in)")
        console.print("  • Intel: Install oneVPL/QSV drivers")
        console.print("  • AMD/Linux: Install VAAPI drivers")
        return

    table = Table(title=f"Hardware Acceleration: {caps.backend.value.upper()}")
    table.add_column("Feature", style="cyan")
    table.add_column("Status", style="green")

    table.add_row("Device", caps.device_name or "Unknown")
    if caps.vram_mb > 0:
        table.add_row("VRAM", f"{caps.vram_mb} MB")
    table.add_row("H.264 Encode", "✓" if caps.supports_h264_encode else "✗")
    table.add_row("H.265 Encode", "✓" if caps.supports_h265_encode else "✗")
    table.add_row("H.264 Decode", "✓" if caps.supports_h264_decode else "✗")
    table.add_row("H.265 Decode", "✓" if caps.supports_h265_decode else "✗")
    table.add_row("GPU Scaling", "✓" if caps.supports_scaling else "✗")
    table.add_row("OpenCV CUDA", "✓" if caps.opencv_cuda else "✗")

    console.print(table)
    console.print()

    if caps.has_encoding:
        print_success("Hardware encoding is available!")
        console.print("Video processing will use GPU acceleration for faster encoding.")
    else:
        console.print("[yellow]Hardware decoding only - encoding will use CPU[/yellow]")


@main.group()
def music() -> None:
    """Music and audio commands."""
    pass


@music.command("search")
@click.option("--mood", "-m", type=str, help="Mood (happy, calm, energetic, etc.)")
@click.option("--genre", "-g", type=str, help="Genre (acoustic, electronic, cinematic, etc.)")
@click.option("--tempo", "-t", type=click.Choice(["slow", "medium", "fast"]), help="Tempo")
@click.option("--min-duration", type=float, default=60, help="Minimum duration in seconds")
@click.option("--limit", "-n", type=int, default=10, help="Number of results")
@click.pass_context
def music_search(
    ctx: click.Context,
    mood: str | None,
    genre: str | None,
    tempo: str | None,
    min_duration: float,
    limit: int,
) -> None:
    """Search for royalty-free music."""
    import asyncio

    from immich_memories.audio.music_sources import PixabayMusicSource

    async def search():
        source = PixabayMusicSource()
        try:
            tracks = await source.search(
                mood=mood,
                genre=genre,
                tempo=tempo,
                min_duration=min_duration,
                limit=limit,
            )
            return tracks
        finally:
            await source.close()

    console.print("[bold]Searching for music...[/bold]")
    console.print()

    if mood:
        console.print(f"Mood: {mood}")
    if genre:
        console.print(f"Genre: {genre}")
    if tempo:
        console.print(f"Tempo: {tempo}")
    console.print()

    tracks = asyncio.get_event_loop().run_until_complete(search())

    if not tracks:
        print_error("No tracks found matching criteria")
        return

    table = Table(title=f"Found {len(tracks)} tracks")
    table.add_column("Title", style="cyan")
    table.add_column("Artist", style="green")
    table.add_column("Duration", style="yellow")
    table.add_column("Tags")

    for track in tracks:
        duration = f"{int(track.duration_seconds // 60)}:{int(track.duration_seconds % 60):02d}"
        tags = ", ".join(track.tags[:3]) if track.tags else ""
        table.add_row(track.title, track.artist, duration, tags)

    console.print(table)


@music.command("analyze")
@click.argument("video_path", type=click.Path(exists=True))
@click.option("--ollama-url", default=None, help="Ollama API URL (default: from config)")
@click.option("--ollama-model", default=None, help="Ollama vision model (default: from config)")
@click.pass_context
def music_analyze(
    ctx: click.Context,
    video_path: str,
    ollama_url: str | None,
    ollama_model: str | None,
) -> None:
    """Analyze a video to determine its mood for music selection."""
    import asyncio
    from pathlib import Path

    from immich_memories.audio.mood_analyzer import get_mood_analyzer

    config = ctx.obj["config"]

    # Use config values as defaults, allow CLI overrides
    effective_ollama_url = ollama_url or config.llm.ollama_url
    effective_ollama_model = ollama_model or config.llm.ollama_model

    async def analyze():
        analyzer = await get_mood_analyzer(
            ollama_url=effective_ollama_url,
            ollama_model=effective_ollama_model,
            openai_api_key=config.llm.openai_api_key,
            openai_model=config.llm.openai_model,
            openai_base_url=config.llm.openai_base_url,
        )
        return await analyzer.analyze_video(Path(video_path))

    console.print("[bold]Analyzing video mood...[/bold]")
    console.print()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Extracting keyframes and analyzing...", total=None)

        try:
            mood = asyncio.get_event_loop().run_until_complete(analyze())
            progress.update(task, completed=True)
        except Exception as e:
            print_error(f"Analysis failed: {e}")
            return

    table = Table(title="Video Mood Analysis")
    table.add_column("Attribute", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Primary Mood", mood.primary_mood)
    if mood.secondary_mood:
        table.add_row("Secondary Mood", mood.secondary_mood)
    table.add_row("Energy Level", mood.energy_level)
    table.add_row("Suggested Tempo", mood.tempo_suggestion)
    table.add_row("Color Palette", mood.color_palette)
    table.add_row("Genre Suggestions", ", ".join(mood.genre_suggestions))
    table.add_row("Confidence", f"{mood.confidence:.0%}")

    console.print(table)
    console.print()

    if mood.description:
        console.print(f"[dim]Description: {mood.description}[/dim]")


@main.group()
def titles() -> None:
    """Title screen generation and testing commands."""
    pass


@titles.command("test")
@click.option("--year", "-y", type=int, help="Year for title screen (e.g., 2024)")
@click.option("--birthday-age", type=int, help="Age for birthday title (e.g., 1 for '1st Year')")
@click.option("--person", "-p", type=str, help="Person name for subtitle")
@click.option("--month", "-m", type=int, help="Month for month divider (1-12)")
@click.option(
    "--orientation",
    "-o",
    type=click.Choice(["landscape", "portrait", "square"]),
    default="landscape",
    help="Output orientation",
)
@click.option(
    "--resolution",
    "-r",
    type=click.Choice(["720p", "1080p", "4k"]),
    default="1080p",
    help="Output resolution",
)
@click.option("--locale", "-l", type=click.Choice(["en", "fr"]), default="en", help="Language")
@click.option(
    "--style",
    "-s",
    type=click.Choice(
        [
            "modern_warm",
            "elegant_minimal",
            "vintage_charm",
            "playful_bright",
            "soft_romantic",
            "random",
        ]
    ),
    default="random",
    help="Visual style",
)
@click.option("--output", "-O", type=click.Path(), help="Output file path")
@click.option(
    "--type",
    "screen_type",
    type=click.Choice(["title", "month", "ending"]),
    default="title",
    help="Screen type",
)
@click.option("--download-fonts", is_flag=True, help="Download fonts before generating")
@click.option(
    "--no-animated-background", is_flag=True, help="Disable animated backgrounds (static gradient)"
)
@click.pass_context
def titles_test(
    ctx: click.Context,
    year: int | None,
    birthday_age: int | None,
    person: str | None,
    month: int | None,
    orientation: str,
    resolution: str,
    locale: str,
    style: str,
    output: str | None,
    screen_type: str,
    download_fonts: bool,
    no_animated_background: bool,
) -> None:
    """Generate a test title screen to preview styles.

    Examples:

    \b
    # Simple year title
    immich-memories titles test --year 2024

    \b
    # Birthday title with person name
    immich-memories titles test --birthday-age 1 --person "Emma"

    \b
    # Month divider
    immich-memories titles test --month 6 --year 2024 --type month

    \b
    # Portrait orientation (for social media)
    immich-memories titles test --year 2024 --orientation portrait

    \b
    # French locale with specific style
    immich-memories titles test --year 2024 --locale fr --style vintage_charm
    """
    from pathlib import Path

    from immich_memories.titles import (
        TitleScreenConfig,
        TitleScreenGenerator,
        download_all_fonts,
        get_resolution_for_orientation,
    )
    from immich_memories.titles.styles import PRESET_STYLES, get_random_style

    console.print("[bold]Title Screen Test Generator[/bold]")
    console.print()

    # Download fonts if requested
    if download_fonts:
        print_info("Downloading fonts...")
        results = download_all_fonts()
        for font, success in results.items():
            if success:
                print_success(f"Font ready: {font}")
            else:
                print_error(f"Failed to download: {font}")
        console.print()

    # Determine output path
    output_path = Path(output) if output else Path.cwd() / "title_screen_preview.mp4"

    # Resolve style
    if style == "random":
        selected_style = get_random_style()
    else:
        selected_style = PRESET_STYLES.get(style, get_random_style())

    # Create config
    config = TitleScreenConfig(
        locale=locale,
        orientation=orientation,
        resolution=resolution,
        style_mode=style if style != "random" else "random",
        animated_background=not no_animated_background,
    )

    # Get resolution for display
    width, height = get_resolution_for_orientation(orientation, resolution)

    # Show parameters
    table = Table(title="Generation Parameters")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Screen Type", screen_type)
    if screen_type == "title":
        if birthday_age:
            table.add_row(
                "Title Type",
                f"Birthday ({birthday_age}{'st' if birthday_age == 1 else 'nd' if birthday_age == 2 else 'rd' if birthday_age == 3 else 'th'} Year)",
            )
        else:
            table.add_row("Title Type", "Calendar Year")
        if year:
            table.add_row("Year", str(year))
        if person:
            table.add_row("Person", person)
    elif screen_type == "month":
        table.add_row("Month", str(month) if month else "(not set)")
    table.add_row("Orientation", orientation)
    table.add_row("Resolution", f"{width}x{height}")
    table.add_row("Locale", locale)
    table.add_row("Style", selected_style.name)
    table.add_row("Animated Background", "Yes" if config.animated_background else "No")
    table.add_row("Output", str(output_path))

    console.print(table)
    console.print()

    # Validate parameters
    if screen_type == "title":
        if not year and not birthday_age:
            year = datetime.now().year
            print_info(f"No year specified, using current year: {year}")
    elif screen_type == "month" and not month:
        print_error("Month divider requires --month parameter (1-12)")
        sys.exit(1)

    # Generate
    from rich.progress import Progress, SpinnerColumn, TextColumn

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Generating title screen...", total=None)

        try:
            generator = TitleScreenGenerator(
                config=config,
                style=selected_style,
                output_dir=output_path.parent,
            )

            if screen_type == "title":
                result = generator.generate_title_screen(
                    year=year,
                    person_name=person,
                    birthday_age=birthday_age,
                )
                # Rename to desired output path
                if result.path != output_path:
                    result.path.rename(output_path)
            elif screen_type == "month":
                assert month is not None  # Validated above
                result = generator.generate_month_divider(month, year=year)
                if result.path != output_path:
                    result.path.rename(output_path)
            elif screen_type == "ending":
                result = generator.generate_ending_screen()
                if result.path != output_path:
                    result.path.rename(output_path)

            progress.update(task, completed=True)

        except Exception as e:
            print_error(f"Generation failed: {e}")
            raise

    print_success(f"Title screen generated: {output_path}")
    console.print()
    console.print(f"[dim]Duration: {result.duration}s[/dim]")


@titles.command("fonts")
@click.option("--download", "-d", is_flag=True, help="Download all fonts")
@click.option("--clear", is_flag=True, help="Clear font cache")
@click.option("--list", "list_fonts", is_flag=True, help="List cached fonts")
def titles_fonts(download: bool, clear: bool, list_fonts: bool) -> None:
    """Manage title screen fonts.

    Downloads OFL-licensed fonts from Google Fonts and caches
    them locally in ~/.immich-memories/fonts/.
    """
    from immich_memories.titles import (
        FONT_DEFINITIONS,
        FontManager,
        download_all_fonts,
        get_available_fonts,
        get_fonts_cache_dir,
    )

    manager = FontManager()

    if clear:
        print_info("Clearing font cache...")
        manager.clear_cache()
        print_success("Font cache cleared")
        return

    if download:
        console.print("[bold]Downloading fonts...[/bold]")
        console.print()

        results = download_all_fonts()

        for font, success in results.items():
            if success:
                print_success(f"{font}")
            else:
                print_error(f"{font} - download failed")

        console.print()
        print_success(f"Fonts cached in: {get_fonts_cache_dir()}")
        return

    # Default: list fonts
    cached = get_available_fonts()

    table = Table(title="Title Screen Fonts")
    table.add_column("Font", style="cyan")
    table.add_column("Status")
    table.add_column("Weights", style="dim")

    for font_name, font_def in FONT_DEFINITIONS.items():
        is_cached = font_name in cached
        status = "[green]Cached[/green]" if is_cached else "[yellow]Not downloaded[/yellow]"
        weights = ", ".join(font_def["weights"].keys())
        table.add_row(font_name, status, weights)

    console.print(table)
    console.print()
    console.print(f"[dim]Cache location: {get_fonts_cache_dir()}[/dim]")

    if not cached:
        console.print()
        console.print("Run [cyan]immich-memories titles fonts --download[/cyan] to download fonts")


@music.command("add")
@click.argument("video_path", type=click.Path(exists=True))
@click.argument("output_path", type=click.Path())
@click.option(
    "--music", "-m", type=click.Path(exists=True), help="Music file (auto-select if not provided)"
)
@click.option("--mood", type=str, help="Override mood for music selection")
@click.option("--genre", "-g", type=str, help="Override genre for music selection")
@click.option("--volume", "-v", type=float, default=-6.0, help="Music volume in dB")
@click.option("--fade-in", type=float, default=2.0, help="Fade in duration in seconds")
@click.option("--fade-out", type=float, default=3.0, help="Fade out duration in seconds")
@click.pass_context
def music_add(
    ctx: click.Context,
    video_path: str,
    output_path: str,
    music: str | None,
    mood: str | None,
    genre: str | None,
    volume: float,
    fade_in: float,
    fade_out: float,
) -> None:
    """Add background music to a video with automatic ducking.

    If no music file is provided, automatically selects music based on video mood.
    Music volume is automatically lowered when speech/sounds are detected.
    """
    import asyncio
    from pathlib import Path

    from immich_memories.audio.mixer import AudioMixer

    ctx.obj["config"]

    async def add_music():
        mixer = AudioMixer()
        return await mixer.add_music_to_video(
            video_path=Path(video_path),
            output_path=Path(output_path),
            music_path=Path(music) if music else None,
            mood=mood,
            genre=genre,
            fade_in=fade_in,
            fade_out=fade_out,
            music_volume_db=volume,
            auto_select=music is None,
        )

    console.print("[bold]Adding Music to Video[/bold]")
    console.print()
    console.print(f"Input: {video_path}")
    console.print(f"Output: {output_path}")
    if music:
        console.print(f"Music: {music}")
    else:
        console.print("Music: [dim]Auto-select based on video mood[/dim]")
    console.print()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Processing...", total=None)

        try:
            result = asyncio.get_event_loop().run_until_complete(add_music())
            progress.update(task, completed=True)
        except Exception as e:
            print_error(f"Failed: {e}")
            return

    print_success(f"Video saved to: {result}")


# === Run History Commands ===


@main.group()
def runs() -> None:
    """Browse and manage pipeline run history."""
    pass


@runs.command("list")
@click.option("--limit", "-n", default=20, help="Number of runs to show")
@click.option("--person", "-p", type=str, help="Filter by person name")
@click.option(
    "--status",
    "-s",
    type=click.Choice(["completed", "failed", "running", "cancelled", "interrupted"]),
    help="Filter by status",
)
def runs_list(limit: int, person: str | None, status: str | None) -> None:
    """List recent pipeline runs.

    Examples:

    \b
    # List recent runs
    immich-memories runs list

    \b
    # Filter by person
    immich-memories runs list --person "John"

    \b
    # Show only failed runs
    immich-memories runs list --status failed
    """
    from immich_memories.tracking import RunDatabase, format_duration

    db = RunDatabase()
    runs_data = db.list_runs(limit=limit, person_name=person, status=status)

    if not runs_data:
        print_info("No runs found")
        return

    table = Table(title="Pipeline Runs")
    table.add_column("Run ID", style="cyan")
    table.add_column("Date", style="green")
    table.add_column("Person")
    table.add_column("Status")
    table.add_column("Duration")
    table.add_column("Output")

    status_styles = {
        "completed": "[green]completed[/green]",
        "failed": "[red]failed[/red]",
        "running": "[yellow]running[/yellow]",
        "cancelled": "[dim]cancelled[/dim]",
        "interrupted": "[yellow]interrupted[/yellow]",
    }

    for run in runs_data:
        status_display = status_styles.get(run.status, run.status)
        duration = format_duration(run.total_duration_seconds)
        output_name = Path(run.output_path).name if run.output_path else "-"

        table.add_row(
            run.run_id[:20],
            run.created_at.strftime("%Y-%m-%d %H:%M"),
            run.person_name or "All",
            status_display,
            duration,
            output_name,
        )

    console.print(table)
    console.print(f"\nShowing {len(runs_data)} runs")


@runs.command("show")
@click.argument("run_id")
def runs_show(run_id: str) -> None:
    """Show detailed information about a specific run.

    Example:
        immich-memories runs show 20260105_143052_a7b3
    """
    from immich_memories.tracking import RunDatabase, format_duration

    db = RunDatabase()
    run = db.get_run(run_id)

    if not run:
        # Try partial match
        all_runs = db.list_runs(limit=100)
        matches = [r for r in all_runs if r.run_id.startswith(run_id)]
        if len(matches) == 1:
            run = matches[0]
        elif len(matches) > 1:
            print_error(f"Ambiguous run ID. Matches: {[r.run_id for r in matches]}")
            return
        else:
            print_error(f"Run not found: {run_id}")
            return

    # Display run metadata
    console.print()
    console.print(f"[bold]Run: {run.run_id}[/bold]")
    console.print()

    table = Table(title="Run Details")
    table.add_column("Property", style="cyan")
    table.add_column("Value")

    status_styles = {
        "completed": "[green]completed[/green]",
        "failed": "[red]failed[/red]",
        "running": "[yellow]running[/yellow]",
        "cancelled": "[dim]cancelled[/dim]",
        "interrupted": "[yellow]interrupted[/yellow]",
    }

    table.add_row("Status", status_styles.get(run.status, run.status))
    table.add_row("Created", run.created_at.strftime("%Y-%m-%d %H:%M:%S"))
    if run.completed_at:
        table.add_row("Completed", run.completed_at.strftime("%Y-%m-%d %H:%M:%S"))
    table.add_row("Person", run.person_name or "All")

    if run.date_range_start and run.date_range_end:
        table.add_row(
            "Date Range",
            f"{run.date_range_start.isoformat()} to {run.date_range_end.isoformat()}",
        )

    table.add_row("Clips", f"{run.clips_selected}/{run.clips_analyzed} selected")

    if run.output_path:
        table.add_row("Output", run.output_path)
        if run.output_duration_seconds > 0:
            table.add_row("Output Duration", format_duration(run.output_duration_seconds))
        if run.output_size_bytes > 0:
            size_mb = run.output_size_bytes / (1024 * 1024)
            table.add_row("Output Size", f"{size_mb:.1f} MB")

    if run.errors_count > 0:
        table.add_row("Errors", f"[red]{run.errors_count}[/red]")

    console.print(table)

    # Display phase timings
    if run.phases:
        console.print()
        console.print("[bold]Phase Timings[/bold]")
        console.print()

        phase_table = Table()
        phase_table.add_column("Phase", style="cyan")
        phase_table.add_column("Duration", justify="right")
        phase_table.add_column("Items", justify="right")
        phase_table.add_column("Errors", justify="right")

        total_duration = 0.0
        for phase in run.phases:
            total_duration += phase.duration_seconds
            items_str = (
                f"{phase.items_processed}/{phase.items_total}"
                if phase.items_total > 0
                else str(phase.items_processed)
                if phase.items_processed > 0
                else "-"
            )
            errors_str = str(len(phase.errors)) if phase.errors else "-"

            phase_table.add_row(
                phase.phase_name,
                format_duration(phase.duration_seconds),
                items_str,
                errors_str,
            )

        # Add total row
        phase_table.add_row(
            "[bold]TOTAL[/bold]",
            f"[bold]{format_duration(total_duration)}[/bold]",
            "",
            "",
        )

        console.print(phase_table)

    # Display system info
    if run.system_info:
        console.print()
        console.print("[bold]System Info[/bold]")
        console.print()

        si = run.system_info
        console.print(f"  Platform: {si.platform_version}")
        if si.cpu_brand:
            console.print(f"  CPU: {si.cpu_brand} ({si.cpu_cores} cores)")
        if si.ram_gb > 0:
            console.print(f"  RAM: {si.ram_gb:.1f} GB")
        if si.gpu_name:
            vram_str = f" ({si.vram_mb} MB)" if si.vram_mb > 0 else ""
            console.print(f"  GPU: {si.gpu_name}{vram_str}")
        if si.hw_accel_backend:
            console.print(f"  HW Accel: {si.hw_accel_backend}")
        if si.ffmpeg_version:
            console.print(f"  FFmpeg: {si.ffmpeg_version}")


@runs.command("stats")
def runs_stats() -> None:
    """Show aggregate statistics across all runs."""
    from immich_memories.tracking import RunDatabase, format_duration

    db = RunDatabase()
    stats = db.get_aggregate_stats()

    console.print()
    console.print("[bold]Aggregate Statistics[/bold]")
    console.print()

    table = Table()
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    table.add_row("Total Runs", str(stats["total_runs"]))
    table.add_row("Completed Runs", f"[green]{stats['completed_runs']}[/green]")
    table.add_row("Failed Runs", f"[red]{stats['failed_runs']}[/red]")
    table.add_row("", "")
    table.add_row("Total Video Generated", format_duration(stats["total_output_seconds"]))
    table.add_row("Total Processing Time", format_duration(stats["total_processing_seconds"]))
    table.add_row("", "")
    table.add_row("Avg Processing Time/Run", format_duration(stats["avg_run_seconds"]))
    table.add_row("Avg Clips/Run", f"{stats['avg_clips']:.1f}")
    table.add_row("Total Clips Processed", str(stats["total_clips"]))

    console.print(table)


@runs.command("delete")
@click.argument("run_id")
@click.option("--keep-output", is_flag=True, help="Keep the output video file")
@click.confirmation_option(prompt="Are you sure you want to delete this run?")
def runs_delete(run_id: str, keep_output: bool) -> None:
    """Delete a run and optionally its output files.

    Examples:

    \b
    # Delete run and its output
    immich-memories runs delete 20260105_143052_a7b3

    \b
    # Delete run but keep the video
    immich-memories runs delete 20260105_143052_a7b3 --keep-output
    """
    import shutil

    from immich_memories.tracking import RunDatabase

    db = RunDatabase()
    run = db.get_run(run_id)

    if not run:
        # Try partial match
        all_runs = db.list_runs(limit=100)
        matches = [r for r in all_runs if r.run_id.startswith(run_id)]
        if len(matches) == 1:
            run = matches[0]
        elif len(matches) > 1:
            print_error(f"Ambiguous run ID. Matches: {[r.run_id for r in matches]}")
            return
        else:
            print_error(f"Run not found: {run_id}")
            return

    # Delete output directory if requested
    if not keep_output and run.output_path:
        output_path = Path(run.output_path)
        # Check if output is in a run-specific directory (contains run_id)
        if run.run_id in str(output_path.parent):
            # Delete the entire run directory
            output_dir = output_path.parent
            if output_dir.exists():
                shutil.rmtree(output_dir)
                print_info(f"Deleted output directory: {output_dir}")
        elif output_path.exists():
            # Just delete the output file
            output_path.unlink()
            print_info(f"Deleted output file: {output_path}")

    # Delete database record
    db.delete_run(run.run_id)
    print_success(f"Deleted run: {run.run_id}")


if __name__ == "__main__":
    main()
