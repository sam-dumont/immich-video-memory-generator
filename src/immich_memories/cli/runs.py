"""Run history commands for Immich Memories CLI."""

from __future__ import annotations

import shutil
from pathlib import Path

import click
from rich.table import Table

from immich_memories.cli._helpers import console, print_error, print_info, print_success


def _print_run_details_table(run, format_duration) -> None:
    """Print the main run details table."""
    table = Table(title="Run Details")
    table.add_column("Property", style="cyan")
    table.add_column("Value")

    status_styles = {
        "completed": "[green]completed[/green]",
        "failed": "[red]failed[/red]",
        "running": "[yellow]running[/yellow]",
        "cancelled": "[dim]cancelled[/dim]",
        "interrupted": "[yellow]interrupted[/yellow]",
    }

    table.add_row("Status", status_styles.get(run.status, run.status))
    table.add_row("Created", run.created_at.strftime("%Y-%m-%d %H:%M:%S"))
    if run.completed_at:
        table.add_row("Completed", run.completed_at.strftime("%Y-%m-%d %H:%M:%S"))
    table.add_row("Person", run.person_name or "All")

    if run.date_range_start and run.date_range_end:
        table.add_row(
            "Date Range",
            f"{run.date_range_start.isoformat()} to {run.date_range_end.isoformat()}",
        )

    table.add_row("Clips", f"{run.clips_selected}/{run.clips_analyzed} selected")

    if run.output_path:
        table.add_row("Output", run.output_path)
        if run.output_duration_seconds > 0:
            table.add_row("Output Duration", format_duration(run.output_duration_seconds))
        if run.output_size_bytes > 0:
            size_mb = run.output_size_bytes / (1024 * 1024)
            table.add_row("Output Size", f"{size_mb:.1f} MB")

    if run.errors_count > 0:
        table.add_row("Errors", f"[red]{run.errors_count}[/red]")

    console.print(table)


def _print_run_phases_table(run, format_duration) -> None:
    """Print the phase timings table for a run."""
    console.print()
    console.print("[bold]Phase Timings[/bold]")
    console.print()

    phase_table = Table()
    phase_table.add_column("Phase", style="cyan")
    phase_table.add_column("Duration", justify="right")
    phase_table.add_column("Items", justify="right")
    phase_table.add_column("Errors", justify="right")

    total_duration = 0.0
    for phase in run.phases:
        total_duration += phase.duration_seconds
        items_str = (
            f"{phase.items_processed}/{phase.items_total}"
            if phase.items_total > 0
            else str(phase.items_processed)
            if phase.items_processed > 0
            else "-"
        )
        errors_str = str(len(phase.errors)) if phase.errors else "-"

        phase_table.add_row(
            phase.phase_name,
            format_duration(phase.duration_seconds),
            items_str,
            errors_str,
        )

    phase_table.add_row(
        "[bold]TOTAL[/bold]",
        f"[bold]{format_duration(total_duration)}[/bold]",
        "",
        "",
    )

    console.print(phase_table)


def _print_run_system_info(si) -> None:
    """Print system info section for a run."""
    console.print()
    console.print("[bold]System Info[/bold]")
    console.print()

    console.print(f"  Platform: {si.platform_version}")
    if si.cpu_brand:
        console.print(f"  CPU: {si.cpu_brand} ({si.cpu_cores} cores)")
    if si.ram_gb > 0:
        console.print(f"  RAM: {si.ram_gb:.1f} GB")
    if si.gpu_name:
        vram_str = f" ({si.vram_mb} MB)" if si.vram_mb > 0 else ""
        console.print(f"  GPU: {si.gpu_name}{vram_str}")
    if si.hw_accel_backend:
        console.print(f"  HW Accel: {si.hw_accel_backend}")
    if si.ffmpeg_version:
        console.print(f"  FFmpeg: {si.ffmpeg_version}")


def register_runs_commands(main: click.Group) -> None:
    """Register the runs command group on the main CLI group."""

    @main.group()
    def runs() -> None:
        """Browse and manage pipeline run history."""
        pass

    main.add_command(runs)

    @runs.command("list")
    @click.option("--limit", "-n", default=20, help="Number of runs to show")
    @click.option("--person", "-p", type=str, help="Filter by person name")
    @click.option(
        "--status",
        "-s",
        type=click.Choice(["completed", "failed", "running", "cancelled", "interrupted"]),
        help="Filter by status",
    )
    def runs_list(limit: int, person: str | None, status: str | None) -> None:
        """List recent pipeline runs.

        Examples:

        \b
        # List recent runs
        immich-memories runs list

        \b
        # Filter by person
        immich-memories runs list --person "John"

        \b
        # Show only failed runs
        immich-memories runs list --status failed
        """
        from immich_memories.config import get_config
        from immich_memories.tracking import RunDatabase, format_duration

        runs_data = RunDatabase(db_path=get_config().cache.database_path).list_runs(
            limit=limit, person_name=person, status=status
        )

        if not runs_data:
            print_info("No runs found")
            return

        table = Table(title="Pipeline Runs")
        table.add_column("Run ID", style="cyan")
        table.add_column("Date", style="green")
        table.add_column("Person")
        table.add_column("Status")
        table.add_column("Duration")
        table.add_column("Output")

        status_styles = {
            "completed": "[green]completed[/green]",
            "failed": "[red]failed[/red]",
            "running": "[yellow]running[/yellow]",
            "cancelled": "[dim]cancelled[/dim]",
            "interrupted": "[yellow]interrupted[/yellow]",
        }

        for run in runs_data:
            status_display = status_styles.get(run.status, run.status)
            duration = format_duration(run.total_duration_seconds)
            output_name = Path(run.output_path).name if run.output_path else "-"

            table.add_row(
                run.run_id[:20],
                run.created_at.strftime("%Y-%m-%d %H:%M"),
                run.person_name or "All",
                status_display,
                duration,
                output_name,
            )

        console.print(table)
        console.print(f"\nShowing {len(runs_data)} runs")

    @runs.command("show")
    @click.argument("run_id")
    def runs_show(run_id: str) -> None:
        """Show detailed information about a specific run.

        Example:
            immich-memories runs show 20260105_143052_a7b3
        """
        from immich_memories.config import get_config
        from immich_memories.tracking import RunDatabase, format_duration

        db = RunDatabase(db_path=get_config().cache.database_path)
        run = db.get_run(run_id)

        if not run:
            # Try partial match
            all_runs = db.list_runs(limit=100)
            matches = [r for r in all_runs if r.run_id.startswith(run_id)]
            if len(matches) == 1:
                run = matches[0]
            elif len(matches) > 1:
                print_error(f"Ambiguous run ID. Matches: {[r.run_id for r in matches]}")
                return
            else:
                print_error(f"Run not found: {run_id}")
                return

        # Display run metadata
        console.print()
        console.print(f"[bold]Run: {run.run_id}[/bold]")
        console.print()

        _print_run_details_table(run, format_duration)

        if run.phases:
            _print_run_phases_table(run, format_duration)

        if run.system_info:
            _print_run_system_info(run.system_info)

    @runs.command("stats")
    def runs_stats() -> None:
        """Show aggregate statistics across all runs."""
        from immich_memories.config import get_config
        from immich_memories.tracking import RunDatabase, format_duration

        stats = RunDatabase(db_path=get_config().cache.database_path).get_aggregate_stats()

        console.print()
        console.print("[bold]Aggregate Statistics[/bold]")
        console.print()

        table = Table()
        table.add_column("Metric", style="cyan")
        table.add_column("Value", justify="right")

        table.add_row("Total Runs", str(stats["total_runs"]))
        table.add_row("Completed Runs", f"[green]{stats['completed_runs']}[/green]")
        table.add_row("Failed Runs", f"[red]{stats['failed_runs']}[/red]")
        table.add_row("", "")
        table.add_row("Total Video Generated", format_duration(stats["total_output_seconds"]))
        table.add_row("Total Processing Time", format_duration(stats["total_processing_seconds"]))
        table.add_row("", "")
        table.add_row("Avg Processing Time/Run", format_duration(stats["avg_run_seconds"]))
        table.add_row("Avg Clips/Run", f"{stats['avg_clips']:.1f}")
        table.add_row("Total Clips Processed", str(stats["total_clips"]))

        console.print(table)

    @runs.command("delete")
    @click.argument("run_id")
    @click.option("--keep-output", is_flag=True, help="Keep the output video file")
    @click.confirmation_option(prompt="Are you sure you want to delete this run?")
    def runs_delete(run_id: str, keep_output: bool) -> None:
        """Delete a run and optionally its output files.

        Examples:

        \b
        # Delete run and its output
        immich-memories runs delete 20260105_143052_a7b3

        \b
        # Delete run but keep the video
        immich-memories runs delete 20260105_143052_a7b3 --keep-output
        """
        from immich_memories.config import get_config
        from immich_memories.tracking import RunDatabase

        db = RunDatabase(db_path=get_config().cache.database_path)
        run = db.get_run(run_id)

        if not run:
            # Try partial match
            all_runs = db.list_runs(limit=100)
            matches = [r for r in all_runs if r.run_id.startswith(run_id)]
            if len(matches) == 1:
                run = matches[0]
            elif len(matches) > 1:
                print_error(f"Ambiguous run ID. Matches: {[r.run_id for r in matches]}")
                return
            else:
                print_error(f"Run not found: {run_id}")
                return

        # Delete output directory if requested
        if not keep_output and run.output_path:
            output_path = Path(run.output_path)
            # Check if output is in a run-specific directory (contains run_id)
            if run.run_id in str(output_path.parent):
                # Delete the entire run directory
                output_dir = output_path.parent
                if output_dir.exists():
                    shutil.rmtree(output_dir)
                    print_info(f"Deleted output directory: {output_dir}")
            elif output_path.exists():
                # Just delete the output file
                output_path.unlink()
                print_info(f"Deleted output file: {output_path}")

        # Delete database record
        db.delete_run(run.run_id)
        print_success(f"Deleted run: {run.run_id}")
