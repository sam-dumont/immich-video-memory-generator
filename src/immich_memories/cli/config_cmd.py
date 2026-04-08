"""Config and connection commands for Immich Memories CLI."""

from __future__ import annotations

import sys

import click
from rich.table import Table

from immich_memories.cli._helpers import console, print_error, print_info, print_success
from immich_memories.config import Config


def register_config_commands(main: click.Group) -> None:
    """Register config, people, years, and preflight commands on the main CLI group."""

    @main.command()
    @click.option("--url", "-u", type=str, help="Immich server URL")
    @click.option("--api-key", "-k", type=str, help="Immich API key")
    @click.option("--show", "-s", is_flag=True, help="Show current configuration")
    @click.pass_context
    def config(ctx: click.Context, url: str | None, api_key: str | None, show: bool) -> None:
        """Configure Immich connection settings."""
        cfg = ctx.obj["config"]
        config_path = Config.get_default_path()

        if show:
            # Display current config
            table = Table(title="Current Configuration")
            table.add_column("Setting", style="cyan")
            table.add_column("Value", style="green")

            table.add_row("Config file", str(config_path))
            table.add_row("Immich URL", cfg.immich.url or "(not set)")
            table.add_row("API Key", "****" if cfg.immich.api_key else "(not set)")
            table.add_row("Output directory", str(cfg.output.output_path))
            table.add_row("Default orientation", cfg.defaults.output_orientation)
            table.add_row("Default scale mode", cfg.defaults.scale_mode)

            console.print(table)
            return

        if url:
            cfg.immich.url = url
        if api_key:
            cfg.immich.api_key = api_key

        if url or api_key:
            cfg.save_yaml(config_path)
            print_success(f"Configuration saved to {config_path}")
        else:
            # Interactive configuration
            console.print("[bold]Immich Memories Configuration[/bold]")
            console.print()

            new_url = click.prompt(
                "Immich server URL",
                default=cfg.immich.url or "https://photos.example.com",
            )
            new_api_key = click.prompt(
                "API key",
                default=cfg.immich.api_key or "",
                hide_input=True,
            )

            cfg.immich.url = new_url
            cfg.immich.api_key = new_api_key
            cfg.save_yaml(config_path)

            print_success(f"Configuration saved to {config_path}")

            # Test connection
            if click.confirm("Test connection now?", default=True):
                from immich_memories.api.immich import ImmichAPIError, SyncImmichClient

                try:
                    with SyncImmichClient(
                        base_url=new_url,
                        api_key=new_api_key,
                    ) as client:
                        user = client.get_current_user()
                        print_success(f"Connected! Logged in as: {user.name or user.email}")
                except ImmichAPIError as e:
                    print_error(f"Connection failed: {e}")

    @main.command()
    @click.pass_context
    def people(ctx: click.Context) -> None:
        """List all people in Immich."""
        cfg = ctx.obj["config"]

        if not cfg.immich.url or not cfg.immich.api_key:
            print_error("Immich not configured. Run 'immich-memories config' first.")
            sys.exit(1)

        from immich_memories.api.immich import SyncImmichClient

        with SyncImmichClient(
            base_url=cfg.immich.url,
            api_key=cfg.immich.api_key,
        ) as client:
            people_list = client.get_all_people()

            table = Table(title="People in Immich")
            table.add_column("Name", style="cyan")
            table.add_column("ID", style="dim")

            for person in sorted(people_list, key=lambda p: p.name):
                if person.name:
                    table.add_row(person.name, person.id[:8] + "...")

            console.print(table)
            console.print(f"\nTotal: {len([p for p in people_list if p.name])} named people")

    @main.command()
    @click.pass_context
    def years(ctx: click.Context) -> None:
        """List years with video content."""
        cfg = ctx.obj["config"]

        if not cfg.immich.url or not cfg.immich.api_key:
            print_error("Immich not configured. Run 'immich-memories config' first.")
            sys.exit(1)

        from immich_memories.api.immich import SyncImmichClient

        with SyncImmichClient(
            base_url=cfg.immich.url,
            api_key=cfg.immich.api_key,
        ) as client:
            years_list = client.get_available_years()

            console.print("[bold]Years with video content:[/bold]")
            for year in years_list:
                console.print(f"  \u2022 {year}")

    @main.command()
    @click.option("--verbose", "-v", is_flag=True, help="Show detailed output")
    @click.pass_context
    def preflight(ctx: click.Context, verbose: bool) -> None:
        """Run preflight checks to validate all provider connections.

        Checks:
        - Immich server connection and API key
        - LLM availability (Ollama or OpenAI-compatible)
        - Hardware acceleration
        """
        from immich_memories.preflight import CheckStatus, run_preflight_checks

        config = ctx.obj["config"]

        console.print("[bold]Running Preflight Checks[/bold]")
        console.print()

        checks = run_preflight_checks(config)

        # Build results table
        table = Table(title="Provider Status")
        table.add_column("Provider", style="cyan")
        table.add_column("Status")
        table.add_column("Message")
        if verbose:
            table.add_column("Details", style="dim")

        status_styles = {
            CheckStatus.OK: "[green]OK[/green]",
            CheckStatus.WARNING: "[yellow]WARNING[/yellow]",
            CheckStatus.ERROR: "[red]ERROR[/red]",
            CheckStatus.SKIPPED: "[dim]SKIPPED[/dim]",
        }

        for check in checks:
            row = [
                check.name,
                status_styles.get(check.status, str(check.status)),
                check.message,
            ]
            if verbose:
                row.append(check.details or "")
            table.add_row(*row)

        console.print(table)
        console.print()

        # Summary
        ok_count = sum(1 for c in checks if c.status == CheckStatus.OK)
        warn_count = sum(1 for c in checks if c.status == CheckStatus.WARNING)
        error_count = sum(1 for c in checks if c.status == CheckStatus.ERROR)
        skip_count = sum(1 for c in checks if c.status == CheckStatus.SKIPPED)

        all_ok = all(c.status != CheckStatus.ERROR for c in checks)
        has_warnings = any(c.status == CheckStatus.WARNING for c in checks)

        if all_ok:
            if has_warnings:
                print_info(
                    f"Preflight complete: {ok_count} OK, {warn_count} warnings, {skip_count} skipped"
                )
            else:
                print_success(f"All checks passed! ({ok_count} OK, {skip_count} skipped)")
        else:
            print_error(f"Preflight failed: {error_count} errors, {warn_count} warnings")
            console.print()
            console.print("[dim]Fix the errors above before proceeding.[/dim]")
            sys.exit(1)
