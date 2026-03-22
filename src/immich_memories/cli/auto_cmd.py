"""Smart automation CLI commands -- suggest, run, install, and history."""

from __future__ import annotations

import json as json_mod
from pathlib import Path

import click
from rich.table import Table

from immich_memories.cli._helpers import console, print_info, print_success
from immich_memories.config_loader import Config


def _print_candidates_table(candidates: list) -> None:
    table = Table(title="Memory Candidates")
    table.add_column("#", style="dim", justify="right")
    table.add_column("Type", style="cyan")
    table.add_column("Date Range", style="green")
    table.add_column("Score", justify="right")
    table.add_column("Reason")
    table.add_column("Assets", justify="right")

    for i, c in enumerate(candidates, 1):
        label = c.memory_type
        if c.person_names:
            label += f"\n({', '.join(c.person_names)})"
        table.add_row(
            str(i),
            label,
            f"{c.date_range_start} to {c.date_range_end}",
            f"{c.score:.3f}",
            c.reason,
            str(c.asset_count),
        )

    console.print(table)


def _candidates_to_json(candidates: list) -> str:
    rows = [
        {
            "memory_type": c.memory_type,
            "date_range": f"{c.date_range_start} to {c.date_range_end}",
            "score": round(c.score, 3),
            "reason": c.reason,
            "asset_count": c.asset_count,
            "person_names": c.person_names,
            "memory_key": c.memory_key,
        }
        for c in candidates
    ]
    return json_mod.dumps(rows, indent=2)


def _print_history_table(runs: list) -> None:
    table = Table(title="Auto-Generated Memories")
    table.add_column("Date", style="green")
    table.add_column("Type", style="cyan")
    table.add_column("Date Range")
    table.add_column("Output")

    for run in runs:
        date_range = ""
        if run.date_range_start and run.date_range_end:
            date_range = f"{run.date_range_start} to {run.date_range_end}"
        output = Path(run.output_path).name if run.output_path else "-"
        table.add_row(
            run.created_at.strftime("%Y-%m-%d %H:%M"),
            run.memory_type or "-",
            date_range,
            output,
        )

    console.print(table)


@click.group()
def auto() -> None:
    """Smart automation -- detect and generate memory candidates."""


@auto.command()
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
@click.option("--limit", default=10, help="Max candidates to show")
@click.option("--type", "memory_type", default=None, help="Filter by memory type")
@click.pass_context
def suggest(ctx: click.Context, as_json: bool, limit: int, memory_type: str | None) -> None:
    """Show prioritized memory candidates."""
    from immich_memories.automation.runner import AutoRunner

    config: Config = ctx.obj["config"]
    candidates = AutoRunner(config).suggest(limit=limit)

    if memory_type:
        candidates = [c for c in candidates if c.memory_type == memory_type]

    if not candidates:
        click.echo("[]") if as_json else print_info("No candidates found")
        return

    click.echo(_candidates_to_json(candidates)) if as_json else _print_candidates_table(candidates)


@auto.command("run")
@click.option("--dry-run", is_flag=True, help="Show what would be generated")
@click.option("--force", is_flag=True, help="Skip cooldown check")
@click.option("--cooldown", type=int, default=None, help="Min hours since last auto-run")
@click.option("--upload", is_flag=True, help="Upload to Immich")
@click.option("--quiet", is_flag=True, help="Machine-friendly output")
@click.pass_context
def run_cmd(
    ctx: click.Context,
    dry_run: bool,
    force: bool,
    cooldown: int | None,
    upload: bool,
    quiet: bool,
) -> None:
    """Generate the top-scoring memory candidate."""
    from immich_memories.automation.runner import AutoRunner

    config: Config = ctx.obj["config"]
    result = AutoRunner(config).run_one(
        force=force, cooldown_hours=cooldown, upload=upload, dry_run=dry_run
    )

    if result:
        click.echo(str(result)) if quiet else print_success(f"Generated: {result}")
    elif not quiet:
        print_info("Nothing generated (cooldown active, no candidates, or dry run)")


@auto.command()
@click.option("--limit", default=10, help="Number of entries to show")
@click.pass_context
def history(ctx: click.Context, limit: int) -> None:
    """Show recent auto-generated memories."""
    from immich_memories.tracking.run_database import RunDatabase

    config: Config = ctx.obj["config"]
    db = RunDatabase(db_path=config.cache.database_path)
    all_runs = db.list_runs(limit=limit, status="completed")
    auto_runs = [r for r in all_runs if r.source == "auto"]

    if not auto_runs:
        print_info("No auto-generated memories found")
        return

    _print_history_table(auto_runs)


@auto.command()
@click.option("--hour", default=9, type=click.IntRange(0, 23), help="Hour to run (0-23)")
@click.option("--minute", default=0, type=click.IntRange(0, 59), help="Minute to run (0-59)")
@click.option("--cooldown", default=24, help="Cooldown hours between runs")
@click.option("--uninstall", is_flag=True, help="Remove installed scheduler")
@click.option("--show", is_flag=True, help="Show config without installing")
def install(hour: int, minute: int, cooldown: int, uninstall: bool, show: bool) -> None:
    """Install system-level scheduler (launchd/systemd/cron)."""
    from immich_memories.automation.system_scheduler import (
        install_scheduler,
        show_scheduler_config,
        uninstall_scheduler,
    )

    if show:
        content = show_scheduler_config(hour, minute, cooldown)
        if content:
            click.echo(content)
        else:
            print_info("immich-memories binary not found in PATH")
        return

    if uninstall:
        if uninstall_scheduler():
            print_success("Scheduler removed")
        else:
            print_info("No scheduler found to remove")
        return

    try:
        result = install_scheduler(hour, minute, cooldown)
    except FileNotFoundError:
        print_info("immich-memories binary not found in PATH")
        return

    print_success(f"Installed {result.platform} scheduler")
    for path in result.files_written:
        print_info(f"  Written: {path}")
    print_info(f"  Activate:   {result.activate_command}")
    print_info(f"  Deactivate: {result.deactivate_command}")


@auto.command("test-notification")
@click.pass_context
def test_notification(ctx: click.Context) -> None:
    """Send a test notification to verify Apprise URL configuration."""
    from immich_memories.automation.notifications import send_test_notification

    config: Config = ctx.obj["config"]
    notif = config.notifications

    if not notif.enabled:
        print_info("Notifications are disabled — set notifications.enabled: true in config")
        return
    if not notif.urls:
        print_info("No notification URLs configured — add URLs to notifications.urls in config")
        return

    if send_test_notification(notif.urls):
        print_success("Test notification sent successfully")
    else:
        print_info("Test notification failed — check URLs and apprise installation")


def register_auto_commands(cli_group: click.Group) -> None:
    """Register the auto command group on the main CLI group."""
    cli_group.add_command(auto)
