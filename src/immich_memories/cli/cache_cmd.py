"""Cache management CLI commands."""

from __future__ import annotations

import sqlite3

import click
from rich.console import Console

console = Console()


def register_cache_commands(cli_group: click.Group) -> None:
    """Register cache subcommands."""

    @cli_group.group()
    def cache() -> None:
        """Back up the database containing run history and automation state."""

    @cache.command()
    @click.argument("output_path", type=click.Path())
    @click.pass_context
    def backup(ctx: click.Context, output_path: str) -> None:
        """Back up cache.db; excludes the separate annotation store and media files."""
        from immich_memories.cache.database import VideoAnalysisCache

        db = VideoAnalysisCache(db_path=ctx.obj["config"].cache.database_path)
        with db._get_connection() as src_conn:
            dst = sqlite3.connect(output_path)
            src_conn.backup(dst)
            dst.close()

        console.print(f"Cache backed up to {output_path}")
