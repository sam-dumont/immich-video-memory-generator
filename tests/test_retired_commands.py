"""Superseded commands must not remain callable through the public CLI."""

import pytest
from click.testing import CliRunner

from immich_memories.cli import main


@pytest.mark.parametrize("command", ["scheduler", "analyze", "export-project"])
def test_retired_command_is_unavailable(command: str) -> None:
    result = CliRunner().invoke(main, [command, "--help"])

    assert result.exit_code == 2
    assert f"No such command '{command}'" in result.output


@pytest.mark.parametrize("command", ["stats", "export", "import"])
def test_retired_score_cache_command_is_unavailable(command: str) -> None:
    result = CliRunner().invoke(main, ["cache", command, "--help"])

    assert result.exit_code == 2
    assert f"No such command '{command}'" in result.output
