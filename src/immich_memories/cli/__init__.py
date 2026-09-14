"""Command-line interface for Immich Memories."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path

# WHY: the kernel library prints a banner to stdout at import time and init
# time, which corrupts the Rich Live display. Must be set before ANY module
# imports it (including tracking/system_info); titles/gpu_kernel_backend.py sets
# the same pair for every other entry point.
os.environ.setdefault("ENABLE_QUADRANTS_HEADER_PRINT", "0")
os.environ.setdefault("QD_LOG_LEVEL", "error")

import click

from immich_memories import __version__
from immich_memories.cli._helpers import console, print_error, print_info, print_success
from immich_memories.config import Config, get_config, init_config_dir

# Re-export helpers so external code can still do `from immich_memories.cli import console` etc.
__all__ = ["console", "main", "print_error", "print_info", "print_success"]


def _is_loopback_host(host: str) -> bool:
    """Return whether a UI bind target is unambiguously local-only."""
    normalized = host.strip().strip("[]").rstrip(".").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _warn_about_unauthenticated_external_bind(config: Config, host: str) -> None:
    """Warn before a stateful unauthenticated UI listens beyond loopback."""
    if config.auth.enabled or _is_loopback_host(host):
        return
    click.echo(
        f"Warning: authentication is disabled while the UI binds to {host}. "
        "Any client that can reach this address can use the app. "
        "The UI is single-user, single-replica; enable authentication before exposing it.",
        err=True,
    )


@click.group()
@click.version_option(version=__version__)
@click.option("--config", "-c", type=click.Path(), help="Path to config file")
@click.option(
    "--preset",
    type=click.Choice(["fast"]),
    default=None,
    help="Config preset for this run: fast = lower-cost render settings (1080p h264, balanced picture quality, "
    "fast encoder preset, static title backgrounds). It changes nothing about what the editor "
    "reads. Anything you set explicitly wins",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Log at DEBUG level. Shorthand for --log-level DEBUG",
)
@click.option(
    "--log-level",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    default=None,
    help="Log level for this run (default: IMMICH_MEMORIES_LOG_LEVEL or INFO)",
)
@click.pass_context
def main(
    ctx: click.Context,
    config: str | None,
    preset: str | None,
    verbose: bool,
    log_level: str | None,
) -> None:
    """Immich Memories - Create video compilations from your Immich library."""
    ctx.ensure_object(dict)

    # Configure logging early
    from immich_memories.logging_config import configure_logging

    level = "DEBUG" if verbose else (log_level.upper() if log_level else None)
    configure_logging(level=level)
    ctx.obj["log_level"] = level

    # Initialize config directory
    init_config_dir()

    # Load configuration
    import sys

    import yaml
    from pydantic import ValidationError

    from immich_memories.cli._config_errors import format_validation_error, format_yaml_error

    try:
        if config:
            config_path = Path(config).expanduser().resolve()
            ctx.obj["config"] = Config.from_yaml(config_path)
            ctx.obj["config_path"] = config_path
        else:
            ctx.obj["config"] = get_config()
            ctx.obj["config_path"] = None
        if preset:
            from immich_memories.config_presets import apply_preset

            ctx.obj["config"].preset = preset
            apply_preset(ctx.obj["config"])
    except ValidationError as e:
        print_error(format_validation_error(e))
        sys.exit(1)
    except yaml.YAMLError as e:
        print_error(format_yaml_error(e))
        sys.exit(1)


@main.command()
@click.option(
    "--port", "-p", default=None, type=int, help="Port to run the UI on (default: config or 8080)"
)
@click.option("--host", "-h", default=None, help="Host to bind to (default: config or 0.0.0.0)")  # noqa: S104
@click.option(
    "--reload/--no-reload", default=False, help="Enable hot reload (for development only)"
)
@click.pass_context
def ui(ctx: click.Context, port: int | None, host: str | None, reload: bool) -> None:
    """Launch the interactive NiceGUI UI."""
    config: Config = ctx.obj["config"]
    host = host or config.server.effective_host(auth_enabled=config.auth.enabled)
    port = port or config.server.port
    _warn_about_unauthenticated_external_bind(config, host)
    print_info(f"Starting Immich Memories UI on http://{host}:{port}")

    # Import the app module to register routes and run
    from immich_memories.ui.app import main as ui_main  # noqa: F401

    try:
        ui_main(port=port, host=host, reload=reload, log_level=ctx.obj.get("log_level"))
    except KeyboardInterrupt:
        print_info("Shutting down...")


# Register all sub-command groups
from immich_memories.cli.auto_cmd import register_auto_commands  # noqa: E402
from immich_memories.cli.cache_cmd import register_cache_commands  # noqa: E402
from immich_memories.cli.config_cmd import register_config_commands  # noqa: E402
from immich_memories.cli.generate import register_generate_commands  # noqa: E402
from immich_memories.cli.hardware_cmd import register_hardware_commands  # noqa: E402
from immich_memories.cli.models_cmd import register_models_commands  # noqa: E402
from immich_memories.cli.music_cmd import register_music_commands  # noqa: E402
from immich_memories.cli.people_cmd import register_people_commands  # noqa: E402
from immich_memories.cli.prepare_cmd import register_prepare_commands  # noqa: E402
from immich_memories.cli.runs import register_runs_commands  # noqa: E402
from immich_memories.cli.special_days_cmd import register_special_day_commands  # noqa: E402
from immich_memories.cli.titles import register_titles_commands  # noqa: E402

register_generate_commands(main)
register_config_commands(main)
register_hardware_commands(main)
register_titles_commands(main)
register_music_commands(main)
register_runs_commands(main)
register_special_day_commands(main)
register_people_commands(main)
register_prepare_commands(main)
register_cache_commands(main)
register_models_commands(main)
register_auto_commands(main)


if __name__ == "__main__":
    main()
