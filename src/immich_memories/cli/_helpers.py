"""Shared helpers for the Immich Memories CLI."""

from __future__ import annotations

import contextvars
import logging
import sys
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from pathlib import Path

    from immich_memories.cli._live_display import LiveDisplay
    from immich_memories.config_loader import Config

console = Console()

_logger = logging.getLogger("immich_memories.cli")

# WHY: contextvars over global — works correctly with async and threads.
# When a LiveDisplay is active, print helpers route messages through it
# to avoid raw console.print() calls breaking Rich's Live cursor control.
_active_display: contextvars.ContextVar[LiveDisplay | None] = contextvars.ContextVar(
    "active_display", default=None
)

_quiet_mode: contextvars.ContextVar[bool] = contextvars.ContextVar("quiet_mode", default=False)


def set_active_display(display: LiveDisplay | None) -> None:
    """Set or clear the active LiveDisplay for print helpers."""
    _active_display.set(display)


def set_quiet_mode(quiet: bool) -> None:
    """Enable quiet mode — print helpers emit log lines instead of Rich output."""
    _quiet_mode.set(quiet)


def get_active_display() -> LiveDisplay | None:
    """Get the active LiveDisplay, or None if not in interactive mode."""
    return _active_display.get()


def described_error(error: BaseException) -> str:
    """What to print for an exception, including one carrying no message at all.

    httpx raises a read error with an empty string when a provider closes a connection
    mid-request, and an hour-long run then ended on "Error: " and nothing else. The
    exception's type and the last stage the run announced are what make that line
    diagnosable without reading file timestamps.
    """
    from immich_memories.operations.cut_progress import last_announced_stage

    message = str(error).strip()
    if message:
        return message
    named = f"{type(error).__module__}.{type(error).__name__}".removeprefix("builtins.")
    stage = last_announced_stage()
    return f"{named} with no message" + (f", during {stage.stage_label}" if stage else "")


def print_error(message: str) -> None:
    """Print an error message."""
    display = _active_display.get()
    if display is not None:
        display.print_message(f"[red]Error:[/red] {message}")
    elif _quiet_mode.get():
        _logger.error(message)
    else:
        console.print(f"[red]Error:[/red] {message}")


def print_warning(message: str) -> None:
    """Print a warning — something the run survived but the reader must see."""
    display = _active_display.get()
    if display is not None:
        display.print_message(f"[yellow]Warning:[/yellow] {message}")
    elif _quiet_mode.get():
        _logger.warning(message)
    else:
        console.print(f"[yellow]Warning:[/yellow] {message}")


def print_success(message: str, *, highlight: bool = True) -> None:
    """Print a success message.

    ``highlight=False`` keeps Rich from colouring paths and numbers inside the
    message: a saved-file path painted magenta reads as an error.
    """
    display = _active_display.get()
    if display is not None:
        display.print_message(f"[green]\u2713[/green] {message}")
    elif _quiet_mode.get():
        # WHY: quiet mode routes the same user-facing path strings the rich console
        # would print; CodeQL mistakes the home-directory prefix for credentials.
        _logger.info(message)  # codeql[py/clear-text-logging-sensitive-data]
    else:
        console.print(f"[green]\u2713[/green] {message}", highlight=highlight)


def print_info(message: str) -> None:
    """Print an info message."""
    display = _active_display.get()
    if display is not None:
        display.print_message(f"[blue]\u2139[/blue] {message}")
    elif _quiet_mode.get():
        _logger.info(message)
    else:
        console.print(f"[blue]\u2139[/blue] {message}")


def refuse_blocked_host(config: Config, *, output_directory: Path | None) -> None:
    """Exit with every pre-run install error (models, output directory), before any Immich call."""
    from rich.markup import escape

    from immich_memories.preflight_run import run_blockers

    blockers = run_blockers(config, output_directory=output_directory)
    for blocker in blockers:
        print_error(escape(f"{blocker.message}: {blocker.details}"))
    if blockers:
        sys.exit(1)
