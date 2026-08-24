"""The flags `generate` takes, grouped by the part of the run each one decides.

Click builds `--help` — and `make docs-cli` builds the CLI reference — from the
order the options were applied, so each group is a contiguous slice of the stack
`generate` used to carry and the groups are applied in that same order. Resorting
them resorts the documentation.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import click

from immich_memories.cli._flags import output_path
from immich_memories.cli.generate_resolution import SHORT_FORM_SECONDS

FC = TypeVar("FC", bound=Callable[..., Any])


def _apply(command: Any, options: list[Any]) -> Any:
    """Apply option decorators so `--help` lists them in the order written here.

    Click reverses `__click_params__` when it builds the command, so decorators
    read top to bottom. A list read front to back has to be applied backwards to
    land in the same order.
    """
    for option in reversed(options):
        command = option(command)
    return command


def scope_options(command: FC) -> FC:
    """What the memory covers: the window to search, and whose memory it is."""
    options = [
        click.option(
            "--year", "-y", type=int, help="Year to generate video for (calendar year by default)"
        ),
        click.option("--start", type=str, help="Start date (YYYY-MM-DD or DD/MM/YYYY)"),
        click.option("--end", type=str, help="End date (use with --start)"),
        click.option("--period", type=str, help="Period from start date (e.g., 6m, 1y, 2w)"),
        click.option(
            "--birthday",
            "-b",
            is_flag=False,
            flag_value="auto",
            default=None,
            help="Use birthday-based year (auto-detects from Immich, or specify MM-DD, e.g. 03-15)",
        ),
        click.option(
            "--from-album",
            "from_album",
            type=str,
            default=None,
            help="Generate from an Immich album (name or ID) instead of a date range",
        ),
        click.option("--person", "-p", type=str, multiple=True, help="Person name (repeatable)"),
        click.option(
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
                    "holiday",
                    "then_and_now",
                ]
            ),
            default=None,
            help="Memory type preset",
        ),
        click.option(
            "--holiday",
            type=str,
            default=None,
            help="Holiday name or MM-DD (use with --memory-type holiday)",
        ),
        click.option(
            "--season",
            type=click.Choice(["spring", "summer", "fall", "autumn", "winter"]),
            default=None,
            help="Season (use with --memory-type season)",
        ),
        click.option(
            "--month",
            type=int,
            default=None,
            help="Month 1-12 (with --year, generates that month; selects trip by month)",
        ),
        click.option(
            "--hemisphere",
            type=click.Choice(["north", "south"]),
            default="north",
            help="Hemisphere for season calculation",
        ),
    ]
    return _apply(command, options)


def output_options(command: FC) -> FC:
    """The file that comes out: its shape, its quality, its soundtrack, its path."""
    options = [
        click.option(
            "--duration",
            "-d",
            type=int,
            default=None,
            help="Target duration in seconds (default: from memory type preset)",
        ),
        click.option(
            "--short-form",
            type=click.Choice(SHORT_FORM_SECONDS),
            default=None,
            help="Short-form preset: sets the duration and makes the video vertical",
        ),
        click.option(
            "--orientation",
            type=click.Choice(["landscape", "portrait", "square"]),
            default="landscape",
            help="Output orientation",
        ),
        click.option(
            "--scale-mode",
            "-s",
            type=click.Choice(["fit", "blur"]),
            default=None,
            help="How to fill an aspect mismatch: blurred background or black bars "
            "(default: from config, else blur)",
        ),
        click.option(
            "--transition",
            "-t",
            type=click.Choice(["smart", "cut", "crossfade", "none"]),
            default="smart",
            help="Transition style (default: smart — mix of fades & cuts)",
        ),
        click.option(
            "--resolution",
            "-r",
            type=click.Choice(["auto", "4k", "1080p", "720p"]),
            default=None,
            help="Output resolution (default: config value, 'auto' to match source clips)",
        ),
        click.option(
            "--music-volume",
            type=float,
            default=0.5,
            help="Music volume 0.0-1.0 (default: 0.5)",
        ),
        click.option(
            "--format",
            "output_format",
            type=click.Choice(["mp4", "h265", "prores"]),
            default=None,
            help="Output format override (default: config value)",
        ),
        click.option(
            "--quality",
            "-q",
            type=click.Choice(["high", "medium", "low"]),
            default=None,
            help="Output quality (default: from config, typically high)",
        ),
        click.option(
            "--output",
            "-o",
            "-O",
            type=click.Path(),
            callback=output_path,
            help="Output file path",
        ),
        click.option(
            "--music",
            "-m",
            type=str,
            default=None,
            help="Music: path to audio file, 'auto' to generate from config, or omit for default behavior",
        ),
        click.option(
            "--no-music",
            "no_music",
            is_flag=True,
            default=False,
            help="Disable all music (skip both provided files and AI generation)",
        ),
    ]
    return _apply(command, options)


def run_options(command: FC) -> FC:
    """How far the run goes, where the result lands, and what is written over the clips."""
    options = [
        click.option("--dry-run", is_flag=True, help="Show what would be done without generating"),
        click.option(
            "--no-render",
            is_flag=True,
            help=(
                "Run the real selection — analysis, verify, judge, review — and stop "
                "before encoding. Unlike --dry-run, which uses cached analysis only and "
                "skips the verify pass, this picks the clips it would actually ship"
            ),
        ),
        click.option(
            "--trace-selection",
            type=click.Path(dir_okay=False, path_type=Path),
            help="Write a stage-by-stage report of how the clips were chosen",
        ),
        click.option(
            "--upload-to-immich",
            is_flag=True,
            default=False,
            help="Upload generated video back to Immich",
        ),
        click.option(
            "--album", type=str, default=None, help="Immich album name for uploaded video"
        ),
        click.option(
            "--add-date", is_flag=True, default=False, help="Caption each clip with its date"
        ),
        click.option(
            "--add-place", is_flag=True, default=False, help="Caption each clip with its place"
        ),
        click.option(
            "--keep-intermediates",
            is_flag=True,
            default=False,
            help="Keep intermediate files for debugging",
        ),
        click.option(
            "--privacy-mode", is_flag=True, default=False, help="Blur faces and mute speech"
        ),
        click.option(
            "--title",
            "title_override",
            type=str,
            default=None,
            help="Override video title text",
        ),
        click.option(
            "--llm-title",
            is_flag=True,
            default=False,
            help="Ask the LLM for the title instead of using a template (--title still wins)",
        ),
        click.option(
            "--subtitle",
            "subtitle_override",
            type=str,
            default=None,
            help="Override video subtitle text",
        ),
    ]
    return _apply(command, options)


def selection_options(command: FC) -> FC:
    """What the candidate pool may hold, and how hard selection works on it."""
    options = [
        click.option(
            "--include-live-photos/--no-live-photos",
            "include_live_photos",
            default=None,
            help="Include Live Photo video clips (3s iPhone clips, merged when burst-captured)",
        ),
        click.option(
            "--include-photos/--no-photos",
            "include_photos",
            default=None,
            help="Include photos as animated Ken Burns clips (blur background, face-aware pan)",
        ),
        click.option(
            "--photo-duration",
            type=float,
            default=None,
            help="Duration per photo clip in seconds (default: 4.0)",
        ),
        click.option(
            "--refinement-passes",
            type=click.IntRange(1, 20),
            default=None,
            help=(
                "How many times selection may verify, judge and review before settling "
                "(default: 10). The biggest dial on warm-run time, and on the bill when "
                "llm.base_url points at a paid API"
            ),
        ),
        click.option(
            "--analysis-depth",
            type=click.Choice(["auto", "fast", "thorough"]),
            default=None,
            help=(
                "Analysis depth: auto (full analysis for manageable pools), "
                "fast (favorites first), or thorough (every eligible clip)"
            ),
        ),
    ]
    return _apply(command, options)


def per_memory_type_options(command: FC) -> FC:
    """Flags that mean something for one --memory-type only: trip picking, and how far back."""
    options = [
        click.option(
            "--trip-index",
            type=int,
            default=None,
            help="Select a specific trip by index (use with --memory-type trip)",
        ),
        click.option(
            "--all-trips",
            is_flag=True,
            default=False,
            help="Generate a video for every detected trip (use with --memory-type trip)",
        ),
        click.option(
            "--years-back",
            type=int,
            default=None,
            help="Years to look back for on_this_day, holiday or then_and_now",
        ),
        click.option(
            "--near-date",
            type=str,
            default=None,
            help="Select trip closest to this date (YYYY-MM-DD, use with --memory-type trip)",
        ),
    ]
    return _apply(command, options)


def automation_options(command: FC) -> FC:
    """The identity a scheduled or auto run carries so its attempt can be recorded."""
    options = [
        click.option(
            "--source",
            type=click.Choice(["manual", "scheduled", "auto"]),
            default="manual",
            hidden=True,
        ),
        click.option("--memory-key", type=str, default=None, hidden=True),
        click.option("--memory-category", type=str, default=None, hidden=True),
        click.option("--automation-attempt-id", type=str, default=None, hidden=True),
        click.option("--automation-target-date", type=str, default=None, hidden=True),
    ]
    return _apply(command, options)
