"""Title screen commands for Immich Memories CLI."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import click
from rich.table import Table

from immich_memories.cli._helpers import console, print_error, print_info, print_success


def _download_and_report_fonts(download_all_fonts) -> None:
    """Download fonts and print success/failure for each."""
    print_info("Downloading fonts...")
    results = download_all_fonts()
    for font, success in results.items():
        if success:
            print_success(f"Font ready: {font}")
        else:
            print_error(f"Failed to download: {font}")
    console.print()


def _print_title_test_params(
    screen_type,
    year,
    birthday_age,
    person,
    month,
    orientation,
    width,
    height,
    locale,
    selected_style,
    config,
    output_path,
) -> None:
    """Print a table of title test generation parameters."""
    table = Table(title="Generation Parameters")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Screen Type", screen_type)
    if screen_type == "title":
        if birthday_age:
            ordinal = (
                "st"
                if birthday_age == 1
                else "nd"
                if birthday_age == 2
                else "rd"
                if birthday_age == 3
                else "th"
            )
            table.add_row("Title Type", f"Birthday ({birthday_age}{ordinal} Year)")
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


def _generate_title_screen(
    generator,
    screen_type,
    output_path,
    year,
    person,
    birthday_age,
    month,
):
    """Generate the requested title screen type and move to output_path.

    Returns:
        The generation result object.
    """
    if screen_type == "title":
        result = generator.generate_title_screen(
            year=year,
            person_name=person,
            birthday_age=birthday_age,
        )
    elif screen_type == "month":
        assert month is not None  # Validated by caller
        result = generator.generate_month_divider(month, year=year)
    elif screen_type == "ending":
        result = generator.generate_ending_screen()
    else:
        raise ValueError(f"Unknown screen type: {screen_type}")

    if result.path != output_path:
        result.path.rename(output_path)

    return result


def register_titles_commands(main: click.Group) -> None:
    """Register the titles command group on the main CLI group."""

    @main.group()
    def titles() -> None:
        """Title screen generation and testing commands."""
        pass

    main.add_command(titles)

    @titles.command("test")
    @click.option("--year", "-y", type=int, help="Year for title screen (e.g., 2024)")
    @click.option(
        "--birthday-age", type=int, help="Age for birthday title (e.g., 1 for '1st Year')"
    )
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
        "--no-animated-background",
        is_flag=True,
        help="Disable animated backgrounds (static gradient)",
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
            _download_and_report_fonts(download_all_fonts)

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
        _print_title_test_params(
            screen_type,
            year,
            birthday_age,
            person,
            month,
            orientation,
            width,
            height,
            locale,
            selected_style,
            config,
            output_path,
        )

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

                result = _generate_title_screen(
                    generator,
                    screen_type,
                    output_path,
                    year,
                    person,
                    birthday_age,
                    month,
                )

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
    def titles_fonts(download: bool, clear: bool, _list_fonts: bool) -> None:
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
            console.print(
                "Run [cyan]immich-memories titles fonts --download[/cyan] to download fonts"
            )
