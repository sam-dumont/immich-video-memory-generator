"""Shared helpers for the Immich Memories CLI."""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from immich_memories.cli._live_display import LiveDisplay

console = Console()

# WHY: contextvars over global — works correctly with async and threads.
# When a LiveDisplay is active, print helpers route messages through it
# to avoid raw console.print() calls breaking Rich's Live cursor control.
_active_display: contextvars.ContextVar[LiveDisplay | None] = contextvars.ContextVar(
    "active_display", default=None
)


def set_active_display(display: LiveDisplay | None) -> None:
    """Set or clear the active LiveDisplay for print helpers."""
    _active_display.set(display)


def print_error(message: str) -> None:
    """Print an error message."""
    display = _active_display.get()
    text = f"[red]Error:[/red] {message}"
    if display is not None:
        display.print_message(text)
    else:
        console.print(text)


def print_success(message: str) -> None:
    """Print a success message."""
    display = _active_display.get()
    text = f"[green]\u2713[/green] {message}"
    if display is not None:
        display.print_message(text)
    else:
        console.print(text)


def print_info(message: str) -> None:
    """Print an info message."""
    display = _active_display.get()
    text = f"[blue]\u2139[/blue] {message}"
    if display is not None:
        display.print_message(text)
    else:
        console.print(text)
