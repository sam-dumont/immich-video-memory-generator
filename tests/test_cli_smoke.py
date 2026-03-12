"""CLI smoke tests using Click's CliRunner."""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from immich_memories import __version__
from immich_memories.cli import main
from immich_memories.config_loader import Config


def _invoke(args: list[str], config: Config | None = None) -> object:
    """Invoke the CLI with mocked config and init_config_dir."""
    config = config or Config()
    runner = CliRunner()
    with (
        patch("immich_memories.cli.init_config_dir"),
        patch("immich_memories.cli.get_config", return_value=config),
    ):
        return runner.invoke(main, args, catch_exceptions=False)


class TestCLIHelp:
    """Test that --help works for all commands."""

    def test_main_help(self):
        """Main group --help returns 0 and shows description."""
        result = _invoke(["--help"])
        assert result.exit_code == 0
        assert "Immich Memories" in result.output

    def test_version(self):
        """--version shows the version string."""
        result = _invoke(["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_generate_help(self):
        """generate --help lists expected flags."""
        result = _invoke(["generate", "--help"])
        assert result.exit_code == 0
        assert "--year" in result.output
        assert "--start" in result.output
        assert "--end" in result.output

    def test_config_help(self):
        """config --help lists subcommands."""
        result = _invoke(["config", "--help"])
        assert result.exit_code == 0

    def test_titles_help(self):
        """titles --help lists subcommands."""
        result = _invoke(["titles", "--help"])
        assert result.exit_code == 0

    def test_music_help(self):
        """music --help lists subcommands."""
        result = _invoke(["music", "--help"])
        assert result.exit_code == 0

    def test_runs_help(self):
        """runs --help lists subcommands."""
        result = _invoke(["runs", "--help"])
        assert result.exit_code == 0

    def test_hardware_help(self):
        """hardware --help works."""
        result = _invoke(["hardware", "--help"])
        assert result.exit_code == 0

    def test_ui_help(self):
        """ui --help works."""
        result = _invoke(["ui", "--help"])
        assert result.exit_code == 0


class TestCLIGenerateErrors:
    """Test that generate command handles errors gracefully."""

    def test_generate_no_time_period(self):
        """generate without date args fails with usage error."""
        result = _invoke(["generate"])
        # Should fail since no time period specified
        assert result.exit_code != 0

    def test_generate_dry_run_no_immich(self):
        """generate --dry-run with empty Immich URL shows error."""
        config = Config()  # Empty URL
        result = _invoke(["generate", "--year", "2024", "--dry-run"], config=config)
        # Should error about missing Immich connection
        assert result.exit_code != 0
