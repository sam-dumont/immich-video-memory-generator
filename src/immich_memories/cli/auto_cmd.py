"""Automation CLI commands -- suggest, run, install, and history."""

from __future__ import annotations

import json as json_mod
import logging
from pathlib import Path
from typing import Any

import click
from rich.table import Table

from immich_memories.automation.models import AutoOutcome, AutoRunResult
from immich_memories.cli._helpers import console, print_error, print_info, print_success
from immich_memories.config_loader import Config

logger = logging.getLogger(__name__)


def _print_candidates_table(candidates: list) -> None:
    table = Table(title="Memory Candidates")
    table.add_column("#", style="dim", justify="right")
    table.add_column("Type", style="cyan")
    table.add_column("Category", style="magenta")
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
            c.category.value,
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
            "category": c.category.value,
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


def _auto_result_to_json(result: AutoRunResult, runtime: dict[str, Any]) -> str:
    """Serialize the stable machine-facing automation result contract."""
    return json_mod.dumps(
        {
            "runtime": runtime,
            "outcome": result.outcome.value,
            "action": result.action.value if result.action is not None else None,
            "reason": result.reason,
            "candidate_key": result.candidate.memory_key if result.candidate else None,
            "category": result.candidate.category.value if result.candidate else None,
            "run_id": result.run_id,
            # Always present, so a consumer never has to tell "no error"
            # from "field missing".
            "error": result.error,
            "output_path": str(result.output_path) if result.output_path else None,
            "recent_categories": list(result.recent_categories),
            "rejections": [
                {
                    "category": rejection.category,
                    "memory_key": rejection.memory_key,
                    "rule": rejection.rule,
                }
                for rejection in result.rejections
            ],
        }
    )


def _print_auto_run_result(result: AutoRunResult) -> None:
    """Render one auto run for a human; failures are reported by the caller on stderr."""
    if result.outcome is AutoOutcome.COMPLETED:
        print_success(f"{result.outcome.value}: {result.reason} ({result.output_path})")
    elif result.outcome is not AutoOutcome.FAILED:
        print_info(f"{result.outcome.value}: {result.reason}")

    if result.candidate is not None:
        print_info(f"Candidate: {result.candidate.category.value} ({result.candidate.memory_key})")
    if result.recent_categories:
        print_info(f"Recent auto categories: {', '.join(result.recent_categories)}")
    for rejection in result.rejections:
        print_info(f"Rejected {rejection.category} ({rejection.memory_key}): {rejection.rule}")


def _print_pending_delivery_status(payload: dict[str, Any]) -> None:
    """Render durable queue size separately from artifact retryability."""
    pending_count = payload["pending_delivery_count"]
    oldest_pending = payload["oldest_pending_delivery"]
    if oldest_pending:
        noun = "item" if pending_count == 1 else "items"
        print_info(
            f"Pending delivery queue: {pending_count} {noun}; "
            f"oldest retryable run {oldest_pending['run_id']}"
        )
    elif pending_count:
        noun = "item" if pending_count == 1 else "items"
        print_info(f"Pending delivery queue: {pending_count} {noun}; no retryable artifact exists")
    else:
        print_info("Pending delivery queue: empty")


def _print_notification_status(payload: dict[str, Any]) -> None:
    """Render optional delivery health without exposing configured provider URLs."""
    health = payload["notification_health"]
    if health and health["cooldown_active"]:
        print_info(
            "Notification delivery: paused "
            f"({health['failure_category']}, until {health['cooldown_until']})"
        )
    elif health and health["last_success_at"]:
        print_info(f"Notification delivery: ready (last success {health['last_success_at']})")
    else:
        print_info("Notification delivery: no successful delivery recorded")


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
    """Automation -- detect and generate memory candidates."""


@auto.command()
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
@click.option("--limit", default=10, help="Max candidates to show")
@click.option("--type", "memory_type", default=None, help="Filter by memory type")
@click.pass_context
def suggest(ctx: click.Context, as_json: bool, limit: int, memory_type: str | None) -> None:
    """Show prioritized memory candidates."""
    from immich_memories.automation.runner import AutoRunner
    from immich_memories.automation.status import SuggestOutcome

    config: Config = ctx.obj["config"]
    runner = AutoRunner(config, config_path=ctx.obj["config_path"])
    candidates = runner.suggest(limit=limit)

    if runner.last_suggest_status.outcome is SuggestOutcome.PREFLIGHT_FAILED:
        error = runner.last_suggest_status.error or "Immich preflight failed"
        if as_json:
            click.echo(json_mod.dumps({"error": error}))
        else:
            click.echo(error, err=True)
        ctx.exit(1)

    if not as_json:
        for key, reason in sorted(runner.last_backoff_skips.items()):
            print_info(f"Backing off {key} — {reason}")

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
    from immich_memories.automation import runtime_provenance as provenance_module
    from immich_memories.automation.runner import AutoRunner

    config: Config = ctx.obj["config"]
    provenance = provenance_module.runtime_provenance()
    previous_logging_disable = logging.root.manager.disable
    if quiet:
        logging.disable(logging.CRITICAL)
    try:
        logger.info("auto run starting — %s", provenance.describe())
        if provenance.is_stale:
            # WHY: --quiet turns logging off entirely, and a scheduled job is exactly
            # where nobody is watching. Staleness must reach the error log by itself.
            click.echo(f"warning: scheduled code is stale — {provenance.describe()}", err=True)
        result = AutoRunner(config, config_path=ctx.obj["config_path"]).run_one(
            force=force, cooldown_hours=cooldown, upload=upload, dry_run=dry_run
        )
    finally:
        if quiet:
            logging.disable(previous_logging_disable)

    if quiet:
        click.echo(_auto_result_to_json(result, provenance.to_dict()))
    else:
        _print_auto_run_result(result)

    if result.outcome is AutoOutcome.FAILED:
        click.echo(f"{result.outcome.value}: {result.reason}: {result.error}", err=True)
        ctx.exit(1)


@auto.command()
@click.option("--limit", default=10, help="Number of entries to show")
@click.pass_context
def history(ctx: click.Context, limit: int) -> None:
    """Show recent auto-generated memories."""
    from immich_memories.tracking.run_database import RunDatabase

    config: Config = ctx.obj["config"]
    db = RunDatabase(db_path=config.cache.database_path)
    auto_runs = db.list_runs(limit=limit, status="completed", source="auto")

    if not auto_runs:
        print_info("No auto-generated memories found")
        return

    _print_history_table(auto_runs)


@auto.command()
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
@click.pass_context
def status(ctx: click.Context, as_json: bool) -> None:
    """Show durable automation and external scheduler state."""
    from immich_memories.automation import runtime_provenance as provenance_module
    from immich_memories.automation.runner import AutoRunner
    from immich_memories.automation.system_scheduler import get_scheduler_status

    config: Config = ctx.obj["config"]
    provenance = provenance_module.runtime_provenance()
    previous_logging_disable = logging.root.manager.disable
    if as_json:
        logging.disable(logging.CRITICAL)
    try:
        payload = (
            AutoRunner(config, config_path=ctx.obj["config_path"])
            .status(refresh_suggestion=True)
            .to_dict()
        )
        scheduler = get_scheduler_status()
        scheduler_state = (
            "unknown" if scheduler.active is None else "active" if scheduler.active else "inactive"
        )
        payload["scheduler"] = {
            "platform": scheduler.platform,
            "installed": scheduler.installed,
            "active": scheduler.active,
            "state": scheduler_state,
            "paths": [str(path) for path in scheduler.paths],
        }
        payload["runtime"] = provenance.to_dict()
    finally:
        if as_json:
            logging.disable(previous_logging_disable)

    if as_json:
        click.echo(json_mod.dumps(payload))
        return

    last_attempt = payload["last_attempt"]
    last_run = payload["last_completed_auto_run"]
    cooldown_status = payload["cooldown"]
    scheduler_installation = (
        "installation unknown"
        if scheduler.installed is None
        else "installed"
        if scheduler.installed
        else "not installed"
    )
    print_info(f"Scheduler: {scheduler.platform}, {scheduler_installation}, {scheduler_state}")
    if provenance.is_stale:
        print_error(f"Running code: {provenance.describe()}")
    else:
        print_info(f"Running code: {provenance.describe()}")
    print_info(
        "Last attempt: "
        + (
            f"{last_attempt['outcome']} — {last_attempt['reason']}"
            + (f" (phase: {last_attempt['last_phase']})" if last_attempt["last_phase"] else "")
            if last_attempt
            else "none"
        )
    )
    print_info(
        "Last completed auto run: "
        + (f"{last_run['run_id']} ({last_run['category'] or '-'})" if last_run else "none")
    )
    _print_last_run_model_spend(config, last_run)
    print_info(
        f"Cooldown: {'active' if cooldown_status['active'] else 'ready'} "
        f"({cooldown_status['hours']}h)"
    )
    categories = payload["recent_categories"]
    print_info(f"Recent categories: {', '.join(categories) if categories else 'none'}")
    rejections = payload["rejection_reasons"]
    print_info(f"Current rejection rules: {', '.join(rejections) if rejections else 'none'}")
    suggestion = payload["suggestion"]
    if suggestion["outcome"] in {"preflight_failed", "discovery_failed"}:
        print_info(f"Suggestion snapshot unavailable: {suggestion['error']}")
    _print_notification_status(payload)
    _print_pending_delivery_status(payload)


@auto.command()
@click.option("--hour", default=9, type=click.IntRange(0, 23), help="Hour to run (0-23)")
@click.option("--minute", default=0, type=click.IntRange(0, 59), help="Minute to run (0-59)")
@click.option("--cooldown", default=24, help="Cooldown hours between runs")
@click.option("--uninstall", is_flag=True, help="Remove installed scheduler")
@click.option("--show", is_flag=True, help="Show config without installing")
@click.option(
    "--force",
    is_flag=True,
    help="Schedule this install even when its checkout is a worktree or behind its upstream",
)
@click.pass_context
def install(
    ctx: click.Context,
    hour: int,
    minute: int,
    cooldown: int,
    uninstall: bool,
    show: bool,
    force: bool,
) -> None:
    """Install system-level scheduler (launchd/systemd/cron)."""
    from immich_memories.automation.system_scheduler import (
        StaleScheduledCodeError,
        install_scheduler,
        show_scheduler_config,
        uninstall_scheduler,
    )

    config_path: Path | None = ctx.obj["config_path"]

    if show:
        content = show_scheduler_config(hour, minute, cooldown, config_path=config_path)
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
        result = install_scheduler(hour, minute, cooldown, config_path=config_path, force=force)
    except FileNotFoundError:
        print_info("immich-memories binary not found in PATH")
        return
    except StaleScheduledCodeError as exc:
        print_error(str(exc))
        ctx.exit(1)

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

    if send_test_notification(
        notif.urls,
        db_path=config.cache.database_path,
        attach_thumbnail=notif.attach_thumbnail,
        cooldown_hours=notif.cooldown_hours,
    ):
        print_success("Test notification sent successfully")
    else:
        print_info("Test notification failed — check URLs and apprise installation")


def register_auto_commands(cli_group: click.Group) -> None:
    """Register the auto command group on the main CLI group."""
    cli_group.add_command(auto)


def _print_last_run_model_spend(config: Config, last_run: dict | None) -> None:
    """What the last automatic run spent on the model, if anything.

    The same line `runs show` prints, from the same stored totals -- an
    unattended run is exactly the one nobody watched, so its bill has to be
    visible without going looking for it.
    """
    if not last_run or not last_run.get("run_id"):
        return
    from immich_memories.cli._run_summary import render_llm_totals
    from immich_memories.tracking import RunDatabase

    run = RunDatabase(config.cache.database_path).get_run(last_run["run_id"])
    line = render_llm_totals(getattr(run, "llm_metrics", None) or {}) if run else ""
    if line:
        print_info(line.replace("\n", " — ").strip())
