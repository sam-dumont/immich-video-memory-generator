"""Cache management CLI commands."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

console = Console()


def register_cache_commands(cli_group: click.Group) -> None:
    """Register cache subcommands."""

    @cli_group.group()
    def cache() -> None:
        """Manage the analysis cache (LLM scores, video metadata)."""

    @cache.command()
    def stats() -> None:
        """Show cache statistics."""
        from immich_memories.cache.database import VideoAnalysisCache

        db = VideoAnalysisCache()
        s = db.get_cache_stats()

        table = Table(title="Cache Statistics")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Total scored assets", str(s["total"]))
        for asset_type, count in s.get("by_type", {}).items():
            table.add_row(f"  {asset_type}", str(count))
        table.add_row("With LLM analysis", str(s["with_llm"]))
        table.add_row("Oldest entry", s["oldest"] or "—")
        table.add_row("Newest entry", s["newest"] or "—")

        console.print(table)

    @cache.command()
    @click.argument("output_path", type=click.Path())
    def export(output_path: str) -> None:
        """Export asset scores to JSON (safe, lock-aware)."""
        from immich_memories.cache.database import VideoAnalysisCache

        db = VideoAnalysisCache()
        with db._get_connection() as conn:
            rows = conn.execute("SELECT * FROM asset_scores").fetchall()
            data = [dict(row) for row in rows]

        Path(output_path).write_text(json.dumps(data, indent=2, default=str))
        console.print(f"Exported {len(data)} asset scores to {output_path}")

    @cache.command(name="import")
    @click.argument("input_path", type=click.Path(exists=True))
    def import_scores(input_path: str) -> None:
        """Import asset scores from JSON backup."""
        from immich_memories.cache.database import VideoAnalysisCache

        data = json.loads(Path(input_path).read_text())
        db = VideoAnalysisCache()

        imported = 0
        for row in data:
            db.save_asset_score(
                asset_id=row["asset_id"],
                asset_type=row.get("asset_type", "unknown"),
                metadata_score=row.get("metadata_score", 0),
                combined_score=row.get("combined_score", 0),
                llm_interest=row.get("llm_interest"),
                llm_quality=row.get("llm_quality"),
                llm_emotion=row.get("llm_emotion"),
                llm_description=row.get("llm_description"),
                model_version=row.get("model_version"),
            )
            imported += 1

        console.print(f"Imported {imported} asset scores from {input_path}")

    @cache.command()
    @click.argument("output_path", type=click.Path())
    def backup(output_path: str) -> None:
        """Backup the entire cache DB (safe SQLite backup API)."""
        from immich_memories.cache.database import VideoAnalysisCache

        db = VideoAnalysisCache()
        with db._get_connection() as src_conn:
            dst = sqlite3.connect(output_path)
            src_conn.backup(dst)
            dst.close()

        console.print(f"Cache backed up to {output_path}")
