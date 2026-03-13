"""Scheduler CLI commands."""

from __future__ import annotations

from datetime import UTC, datetime

import click
from rich.table import Table

from immich_memories.cli._helpers import console, print_error, print_info, print_success


def register_scheduler_commands(main: click.Group) -> None:
    """Register scheduler commands on the main CLI group."""

    @main.group()
    def scheduler() -> None:
        """Manage scheduled automatic memory generation."""

    @scheduler.command("list")
    @click.pass_context
    def list_schedules(ctx: click.Context) -> None:
        """List all configured schedules."""
        config = ctx.obj["config"]
        schedules = config.scheduler.schedules

        if not schedules:
            print_info(
                "No schedules configured. Add them to your config.yaml under scheduler.schedules"
            )
            return

        table = Table(title="Configured Schedules")
        table.add_column("Name", style="cyan")
        table.add_column("Type", style="green")
        table.add_column("Cron", style="yellow")
        table.add_column("Enabled", style="bold")
        table.add_column("Upload", style="dim")
        table.add_column("Next Run")

        from immich_memories.scheduling.engine import Scheduler

        sched = Scheduler(config.scheduler)
        now = datetime.now(tz=UTC)
        next_jobs = {j.schedule.name: j.fire_time for j in sched.get_next_jobs(now)}

        for entry in schedules:
            next_run = next_jobs.get(entry.name)
            next_str = next_run.strftime("%Y-%m-%d %H:%M UTC") if next_run else "—"
            table.add_row(
                entry.name,
                entry.memory_type,
                entry.cron,
                "Yes" if entry.enabled else "No",
                "Yes" if entry.upload_to_immich else "No",
                next_str,
            )

        console.print(table)

    @scheduler.command()
    @click.pass_context
    def status(ctx: click.Context) -> None:
        """Show scheduler status."""
        config = ctx.obj["config"]

        if not config.scheduler.enabled:
            print_info("Scheduler is not enabled. Set scheduler.enabled: true in config.yaml")
            return

        print_success(f"Scheduler enabled (timezone: {config.scheduler.timezone})")

        n = len(config.scheduler.schedules)
        enabled = sum(1 for s in config.scheduler.schedules if s.enabled)
        print_info(f"{enabled}/{n} schedules active")

        if enabled > 0:
            from immich_memories.scheduling.engine import Scheduler

            sched = Scheduler(config.scheduler)
            now = datetime.now(tz=UTC)
            wait = sched.seconds_until_next(now)
            if wait is not None:
                jobs = sched.get_next_jobs(now)
                next_job = jobs[0]
                print_info(
                    f"Next: '{next_job.schedule.name}' at "
                    f"{next_job.fire_time.strftime('%Y-%m-%d %H:%M UTC')} "
                    f"(in {wait / 3600:.1f}h)"
                )

    @scheduler.command()
    @click.option("--foreground", is_flag=True, help="Run in foreground (don't daemonize)")
    @click.pass_context
    def start(ctx: click.Context, foreground: bool) -> None:
        """Start the scheduler daemon."""
        config = ctx.obj["config"]

        if not config.scheduler.enabled:
            print_error("Scheduler is not enabled. Set scheduler.enabled: true in config.yaml")
            return

        if not config.scheduler.schedules:
            print_error("No schedules configured")
            return

        mode = "foreground" if foreground else "background"
        print_info(f"Starting scheduler daemon ({mode} mode)...")
        print_info(f"Timezone: {config.scheduler.timezone}")
        print_info(f"Schedules: {len(config.scheduler.schedules)}")

        # TODO: Implement the actual daemon loop
        # This will use Scheduler + resolve_schedule_params + pipeline execution
        print_info("Scheduler daemon loop coming soon!")
