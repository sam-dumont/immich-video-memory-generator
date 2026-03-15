"""Analyze and export-project commands for Immich Memories CLI."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import click

from immich_memories.cli._helpers import console, print_error, print_success


def register_analyze_export_commands(main: click.Group) -> None:
    """Register analyze and export-project commands on the main CLI group."""

    @main.command()
    @click.option("--year", "-y", type=int, help="Year to analyze")
    @click.option("--force", "-f", is_flag=True, help="Force re-analysis of cached videos")
    @click.pass_context
    def analyze(ctx: click.Context, year: int | None, force: bool) -> None:  # noqa: ARG001
        """Analyze videos and cache metadata."""
        from rich.progress import Progress, SpinnerColumn, TextColumn

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
            with output_path.open("w") as f:
                json.dump(project, f, indent=2)

            print_success(f"Project exported to {output_path}")
            console.print(f"  {len(assets)} clips included")
