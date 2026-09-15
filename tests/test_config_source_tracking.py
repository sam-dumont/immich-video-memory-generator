"""One UI/CLI process has one configuration source.

`immich-memories --config PATH ui` used PATH only for host/port: UI startup then
reloaded the default config, so authentication (and everything else) silently
came from ~/.immich-memories/config.yaml instead of the file the operator named.
A reload must reload the same source, and the settings pages must read, save and
display that same source.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from immich_memories.cli import main
from immich_memories.config_loader import (
    Config,
    get_config,
    get_config_path,
    set_config,
)


@pytest.fixture(autouse=True)
def _pristine_config_source():
    """Start and leave the process with no global config and no recorded source.

    The tests monkeypatch `Config.get_default_path`, so a leftover global or a
    leftover `--config` path from another test would silently reload through
    the wrong file.
    """
    set_config(None)
    yield
    set_config(None)


def _write(path: Path, body: str) -> Path:
    path.write_text(body)
    return path


def test_reload_reloads_the_configured_source_not_the_default(tmp_path: Path, monkeypatch) -> None:
    default = _write(tmp_path / "default.yaml", "auth:\n  enabled: false\n")
    alternate = _write(
        tmp_path / "alternate.yaml",
        "auth:\n  enabled: true\n  username: alt-admin\n  password: alt-secret\n",
    )
    monkeypatch.setattr(Config, "get_default_path", classmethod(lambda _cls: default))

    set_config(Config.from_yaml(alternate), path=alternate)

    reloaded = get_config(reload=True)

    assert reloaded.auth.enabled is True, (
        "a reload must come from the configured source, not the default path"
    )
    assert reloaded.auth.username == "alt-admin"
    assert get_config_path() == alternate


def test_the_default_source_still_wins_when_no_custom_path_is_set(
    tmp_path: Path, monkeypatch
) -> None:
    default = _write(tmp_path / "default.yaml", "auth:\n  enabled: false\n")
    monkeypatch.setattr(Config, "get_default_path", classmethod(lambda _cls: default))

    reloaded = get_config(reload=True)

    assert reloaded.auth.enabled is False
    assert get_config_path() == default


def test_cli_config_option_installs_the_global_source(tmp_path: Path, monkeypatch) -> None:
    default = _write(tmp_path / "default.yaml", "auth:\n  enabled: false\n")
    alternate = _write(
        tmp_path / "alternate.yaml",
        "auth:\n  enabled: true\n  username: alt-admin\n  password: alt-secret\n",
    )
    monkeypatch.setattr(Config, "get_default_path", classmethod(lambda _cls: default))

    result = CliRunner().invoke(main, ["--config", str(alternate), "hardware"])

    assert result.exit_code == 0, result.output
    live = get_config(reload=True)
    assert live.auth.enabled is True, (
        "--config PATH ui starts the UI with PATH only for host/port; "
        "authentication must come from that same file"
    )
    assert get_config_path() == alternate
