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

    # Initialize config directory
    init_config_dir()

    # Load configuration
    if config:
        config_path = Path(config)
        ctx.obj["config"] = Config.from_yaml(config_path)
    else:
        ctx.obj["config"] = get_config()


@main.command()
@click.option("--port", "-p", default=8080, help="Port to run the UI on")
@click.option("--host", "-h", default="0.0.0.0", help="Host to bind to")  # noqa: S104 - intentional for Docker/container binding
@click.option(
    "--reload/--no-reload", default=False, help="Enable hot reload (for development only)"
)
def ui(port: int, host: str, reload: bool) -> None:
    """Launch the interactive NiceGUI UI."""
    print_info(f"Starting Immich Memories UI on http://{host}:{port}")

    # Import the app module to register routes and run
    from immich_memories.ui.app import main as ui_main  # noqa: F401

    try:
        ui_main(port=port, host=host, reload=reload)
    except KeyboardInterrupt:
        print_info("Shutting down...")


# Register all sub-command groups
from immich_memories.cli.config_cmd import register_config_commands  # noqa: E402
from immich_memories.cli.generate import register_generate_commands  # noqa: E402
from immich_memories.cli.hardware_cmd import register_hardware_commands  # noqa: E402
from immich_memories.cli.music_cmd import register_music_commands  # noqa: E402
from immich_memories.cli.runs import register_runs_commands  # noqa: E402
from immich_memories.cli.titles import register_titles_commands  # noqa: E402

register_generate_commands(main)
register_config_commands(main)
register_hardware_commands(main)
register_titles_commands(main)
register_music_commands(main)
register_runs_commands(main)


if __name__ == "__main__":
    main()
