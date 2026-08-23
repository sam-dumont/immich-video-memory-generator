"""Secure-by-default binding (#476): auth off means localhost unless the
operator says otherwise — in config or on the command line."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from immich_memories.cli import main
from immich_memories.config_loader import Config


def _config(**overrides) -> Config:
    return Config(**overrides)


class TestEffectiveHost:
    def test_auth_off_and_nothing_set_binds_localhost(self):
        config = _config()

        host = config.server.effective_host(auth_enabled=config.auth.enabled)

        assert host == "127.0.0.1"

    def test_auth_on_keeps_the_configured_default(self):
        config = _config()

        # auth validity is AuthConfig's concern; the resolver only needs the flag
        assert config.server.effective_host(auth_enabled=True) == "0.0.0.0"  # noqa: S104 — the assertion IS about the broad bind

    def test_an_explicit_host_is_the_operators_decision(self):
        config = _config(server={"host": "0.0.0.0"})  # noqa: S104 — explicit operator choice under test

        assert config.server.effective_host(auth_enabled=False) == "0.0.0.0"  # noqa: S104 — the assertion IS about the broad bind

    def test_the_named_escape_hatch_works(self):
        config = _config(server={"allow_unauthenticated_lan": True})

        assert config.server.effective_host(auth_enabled=False) == "0.0.0.0"  # noqa: S104 — the assertion IS about the broad bind


class TestSavingDoesNotDecideForTheOperator:
    """#507: a config the app wrote must not read back as a deliberate LAN bind."""

    def test_a_config_written_by_the_cli_still_binds_localhost(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"

        # WHY: init_config_dir writes to the real ~/.immich-memories
        with patch("immich_memories.cli.init_config_dir"):
            result = CliRunner().invoke(
                main,
                [
                    "--config",
                    str(config_path),
                    "config",
                    "--url",
                    "http://immich.test",
                    "--api-key",
                    "test-key",
                ],
                catch_exceptions=False,
            )
        assert result.exit_code == 0, result.output

        reloaded = Config.from_yaml(config_path)

        assert reloaded.server.effective_host(auth_enabled=False) == "127.0.0.1"

    def test_a_host_the_operator_chose_survives_the_round_trip(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        _config(server={"host": "192.168.1.10"}).save_yaml(config_path)

        reloaded = Config.from_yaml(config_path)

        assert reloaded.server.effective_host(auth_enabled=False) == "192.168.1.10"

    def test_the_rest_of_the_server_section_is_still_written(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        _config(server={"port": 9090}).save_yaml(config_path)

        assert Config.from_yaml(config_path).server.port == 9090


class TestConfigsAlreadyCarryingTheWildcard:
    """#507 migration: the wildcard the app used to write is not a decision."""

    def test_a_file_written_by_an_older_version_binds_localhost(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text("advanced:\n  server:\n    host: 0.0.0.0\n    port: 9090\n")

        reloaded = Config.from_yaml(config_path)

        assert reloaded.server.port == 9090
        assert reloaded.server.effective_host(auth_enabled=False) == "127.0.0.1"

    def test_the_env_var_is_still_a_decision(self, tmp_path: Path, monkeypatch) -> None:
        """Only the file is ambiguous — nothing ever wrote the env var but a human."""
        monkeypatch.setenv("IMMICH_MEMORIES_SERVER__HOST", "0.0.0.0")  # noqa: S104 — the operator's choice under test
        config_path = tmp_path / "config.yaml"
        config_path.write_text("advanced:\n  server:\n    host: 0.0.0.0\n")

        reloaded = Config.from_yaml(config_path)

        assert reloaded.server.effective_host(auth_enabled=False) == "0.0.0.0"  # noqa: S104 — the assertion IS about the broad bind
