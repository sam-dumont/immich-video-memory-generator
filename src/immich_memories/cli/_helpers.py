"""Shared helpers for the Immich Memories CLI."""

from __future__ import annotations

import contextvars
import logging
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from immich_memories.cli._live_display import LiveDisplay

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


def print_error(message: str) -> None:
    """Print an error message."""
    display = _active_display.get()
    if display is not None:
        display.print_message(f"[red]Error:[/red] {message}")
    elif _quiet_mode.get():
        _logger.error(message)
    else:
        console.print(f"[red]Error:[/red] {message}")


def print_success(message: str) -> None:
    """Print a success message."""
    display = _active_display.get()
    if display is not None:
        display.print_message(f"[green]\u2713[/green] {message}")
    elif _quiet_mode.get():
        _logger.info(message)
    else:
        console.print(f"[green]\u2713[/green] {message}")


def print_info(message: str) -> None:
    """Print an info message."""
    display = _active_display.get()
    if display is not None:
        display.print_message(f"[blue]\u2139[/blue] {message}")
    elif _quiet_mode.get():
        _logger.info(message)
    else:
        console.print(f"[blue]\u2139[/blue] {message}")
