"""Command-line interface for Immich Memories."""

from __future__ import annotations

from pathlib import Path

import click

from immich_memories import __version__
from immich_memories.cli._helpers import console, print_error, print_info, print_success
from immich_memories.config import Config, get_config, init_config_dir

# Re-export helpers so external code can still do `from immich_memories.cli import console` etc.
__all__ = ["console", "main", "print_error", "print_info", "print_success"]


@click.group()
@click.version_option(version=__version__)
@click.option("--config", "-c", type=click.Path(), help="Path to config file")
@click.pass_context
def main(ctx: click.Context, config: str | None) -> None:
    """Immich Memories - Create video compilations from your Immich library."""
    ctx.ensure_object(dict)

    # Configure logging early
    from immich_memories.logging_config import configure_logging

    configure_logging()

    # Initialize config directory
    init_config_dir()

    # Load configuration
    import sys

    import yaml
    from pydantic import ValidationError

    from immich_memories.cli._config_errors import format_validation_error, format_yaml_error

    try:
        if config:
            config_path = Path(config)
            ctx.obj["config"] = Config.from_yaml(config_path)
        else:
            ctx.obj["config"] = get_config()
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
    host = host or config.server.host
    port = port or config.server.port
    print_info(f"Starting Immich Memories UI on http://{host}:{port}")

    # Import the app module to register routes and run
    from immich_memories.ui.app import main as ui_main  # noqa: F401

    try:
        ui_main(port=port, host=host, reload=reload)
    except KeyboardInterrupt:
        print_info("Shutting down...")


# Register all sub-command groups
from immich_memories.cli.auto_cmd import register_auto_commands  # noqa: E402
from immich_memories.cli.cache_cmd import register_cache_commands  # noqa: E402
from immich_memories.cli.config_cmd import register_config_commands  # noqa: E402
from immich_memories.cli.generate import register_generate_commands  # noqa: E402
from immich_memories.cli.hardware_cmd import register_hardware_commands  # noqa: E402
from immich_memories.cli.music_cmd import register_music_commands  # noqa: E402
from immich_memories.cli.runs import register_runs_commands  # noqa: E402
from immich_memories.cli.scheduler_cmd import register_scheduler_commands  # noqa: E402
from immich_memories.cli.titles import register_titles_commands  # noqa: E402

register_generate_commands(main)
register_config_commands(main)
register_hardware_commands(main)
register_titles_commands(main)
register_music_commands(main)
register_runs_commands(main)
register_scheduler_commands(main)
register_cache_commands(main)
register_auto_commands(main)


if __name__ == "__main__":
    main()
