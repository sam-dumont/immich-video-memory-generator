"""What a generation run prints before it starts, and after it finishes.

Split from generate.py, which crossed the 1000-line hard gate when the
short-form and holiday flags landed. The boundary is presentation: building the
parameters table and printing the result are about the terminal, while the rest
of that module is about resolving what to generate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.table import Table

from immich_memories.cli._helpers import print_success

if TYPE_CHECKING:
    from pathlib import Path

    from immich_memories.config_loader import Config
    from immich_memories.timeperiod import DateRange


def _add_scope_rows(table: Table, *, album_ref: str | None, date_range: DateRange) -> None:
    """Describe what the memory is drawn from: an album, or a span of time."""
    if album_ref:
        table.add_row("Album", album_ref)
        return
    table.add_row("Time Period", date_range.description)
    table.add_row("Duration", f"{date_range.days} days")


def _format_target_duration(duration: float | None) -> str:
    if duration is None:
        return "auto"
    return f"{duration / 60:.1f} min" if duration >= 60 else f"{duration:.0f}s"


def _has_music_backends(config: Config) -> bool:
    """Check if any music generation backend is enabled in config."""
    from immich_memories.generate_music import music_config_available

    return music_config_available(config)


def _build_params_table(
    *,
    config: Config,
    memory_type: str | None,
    date_range: DateRange,
    person_names: list[str],
    duration: float | None,
    album_ref: str | None = None,
    orientation: str,
    scale_mode: str | None,
    transition: str,
    resolution: str,
    output_format: str | None,
    output_path: Path,
    add_date: bool,
    add_place: bool,
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
    _add_scope_rows(table, album_ref=album_ref, date_range=date_range)
    table.add_row("Person", ", ".join(person_names) if person_names else "All people")
    table.add_row("Target Duration", _format_target_duration(duration))
    table.add_row("Orientation", orientation)
    table.add_row("Scale Mode", scale_mode or config.defaults.scale_mode)
    table.add_row("Transition", transition)
    table.add_row("Resolution", resolution)
    table.add_row("Format", output_format or config.output.codec)
    table.add_row("Output", str(output_path))
    if add_date:
        table.add_row("Date Overlay", "Enabled")
    if add_place:
        table.add_row("Place Overlay", "Enabled")
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


def _print_generation_result(
    *,
    dry_run: bool,
    result_path: Path,
    should_upload: bool,
    album_name: str | None,
    no_render: bool = False,
) -> None:
    """Report a plan without claiming an artifact or upload exists.

    Both flags stop at the render boundary and both return a path anyway — the
    one the run would have written. Saying "Video saved to" for it sends people
    looking for a file that does not exist.
    """
    if dry_run:
        print_success("Dry-run planning complete; no video was created")
        return
    if no_render:
        print_success("Selection complete; no video was created (--no-render)")
        return
    print_success(f"Video saved to: {result_path}")
    if should_upload:
        print_success(f"Uploaded to Immich (album: {album_name or 'none'})")
