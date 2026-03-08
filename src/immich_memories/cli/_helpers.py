"""Shared helpers for the Immich Memories CLI."""

from __future__ import annotations

from rich.console import Console

console = Console()


def print_error(message: str) -> None:
    """Print an error message."""
    console.print(f"[red]Error:[/red] {message}")


def print_success(message: str) -> None:
    """Print a success message."""
    console.print(f"[green]\u2713[/green] {message}")


def print_info(message: str) -> None:
    """Print an info message."""
    console.print(f"[blue]\u2139[/blue] {message}")
