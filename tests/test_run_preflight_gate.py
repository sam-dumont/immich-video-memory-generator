"""A cut refuses to start when this host cannot finish it, before it reads a picture."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

from immich_memories.cli import main
from immich_memories.preflight import CheckStatus
from immich_memories.preflight_run import check_output_directory

pytestmark = pytest.mark.install_checks


def _config_file(tmp_path: Path, **overrides: object) -> Path:
    body: dict = {
        # A closed port: nothing in these tests may reach it.
        "immich": {"url": "http://127.0.0.1:9", "api_key": "test-key"},
        "output": {"directory": str(tmp_path / "output")},
        "advanced": {"triage": {"encoder": str(tmp_path / "models" / "dinov2-small.onnx")}},
    }
    body.update(overrides)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(body))
    return path


def _generate(config_path: Path, *extra: str):
    # WHY: Immich is the external boundary; the gate has to fire before it is
    # contacted, so reaching the client at all is the failure.
    with patch(
        "immich_memories.api.immich.SyncImmichClient",
        side_effect=AssertionError("the run reached Immich"),
    ) as client:
        result = CliRunner().invoke(
            main,
            ["--config", str(config_path), "generate", "--year", "2024", "--quiet", *extra],
        )
    return result, client


def test_a_cut_without_the_models_names_the_fetch_command_before_touching_immich(
    tmp_path,
) -> None:
    result, client = _generate(_config_file(tmp_path))

    assert result.exit_code == 1
    assert "immich-memories models fetch" in result.output
    client.assert_not_called()


def _metadata_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A config whose tier needs no model files, so only the output directory is in question."""
    # The suite routes the output directory through the environment, which
    # outranks the file; this test's directory has to win.
    monkeypatch.setenv("IMMICH_MEMORIES_OUTPUT__DIRECTORY", str(tmp_path / "output"))
    return _config_file(
        tmp_path,
        advanced={"editorial": {"preparation": {"tier": "metadata_only"}}},
    )


def test_a_missing_output_directory_is_created_before_the_run_starts(tmp_path, monkeypatch) -> None:
    result, client = _generate(_metadata_only(tmp_path, monkeypatch))

    assert (tmp_path / "output").is_dir()
    assert "not writable" not in result.output
    client.assert_called_once()


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="root writes anywhere")
def test_an_unwritable_output_directory_stops_the_run_before_touching_immich(
    tmp_path, monkeypatch
) -> None:
    output = tmp_path / "output"
    output.mkdir(mode=0o555)
    try:
        result, client = _generate(_metadata_only(tmp_path, monkeypatch))
    finally:
        output.chmod(0o755)

    assert result.exit_code == 1
    assert "Output directory is not writable" in result.output
    assert str(output) in result.output.replace("\n", "")
    client.assert_not_called()


def test_preflight_creates_a_nested_output_directory(tmp_path) -> None:
    directory = tmp_path / "Videos" / "Memories"

    result = check_output_directory(directory)

    assert result.status is CheckStatus.OK
    assert directory.is_dir()


def test_prepare_without_the_models_names_the_fetch_command_before_touching_immich(
    tmp_path,
) -> None:
    # WHY: Immich is the external boundary; reaching the client at all is the failure.
    with patch(
        "immich_memories.api.sync_client.SyncImmichClient",
        side_effect=AssertionError("the run reached Immich"),
    ) as client:
        result = CliRunner().invoke(
            main,
            ["--config", str(_config_file(tmp_path)), "prepare", "--year", "2024", "--month", "1"],
        )

    assert result.exit_code == 1
    assert "immich-memories models fetch" in result.output
    client.assert_not_called()
