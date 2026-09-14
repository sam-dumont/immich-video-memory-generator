"""Tests for the database backup command."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from immich_memories.cli import main
from immich_memories.config_loader import Config

# WHY: these are the database boundaries — commands import them inline
_CACHE_CLS = "immich_memories.cache.database.VideoAnalysisCache"


def _invoke(args: list[str]) -> object:
    """Invoke the CLI with mocked config and init_config_dir."""
    runner = CliRunner()
    with (
        patch("immich_memories.cli.init_config_dir"),
        patch("immich_memories.cli.get_config", return_value=Config()),
    ):
        return runner.invoke(main, args, catch_exceptions=False)


class TestCacheBackup:
    def test_backup_uses_the_database_in_the_selected_config(self, tmp_path, monkeypatch):
        monkeypatch.delenv("IMMICH_MEMORIES_CACHE__DATABASE")
        source = tmp_path / "selected.db"
        destination = tmp_path / "backup.db"
        with sqlite3.connect(source) as connection:
            connection.execute("CREATE TABLE backup_marker (value TEXT)")
            connection.execute("INSERT INTO backup_marker VALUES ('selected config')")
        config_path = tmp_path / "config.yaml"
        config_path.write_text(f"cache:\n  database: {source}\n")

        result = CliRunner().invoke(
            main, ["--config", str(config_path), "cache", "backup", str(destination)]
        )

        assert result.exit_code == 0, result.output
        with sqlite3.connect(destination) as connection:
            assert connection.execute("SELECT value FROM backup_marker").fetchone() == (
                "selected config",
            )

    def test_backs_up_via_sqlite_api(self, tmp_path):
        out_file = tmp_path / "backup.db"

        mock_conn = MagicMock()

        @contextmanager
        def fake_get_connection():
            yield mock_conn

        mock_db = MagicMock()
        mock_db._get_connection = fake_get_connection

        mock_dst = MagicMock(spec=sqlite3.Connection)

        with (
            patch(_CACHE_CLS, return_value=mock_db),
            patch(
                "immich_memories.cli.cache_cmd.sqlite3.connect", return_value=mock_dst
            ) as mock_sqlite_connect,
        ):
            result = _invoke(["cache", "backup", str(out_file)])

        assert result.exit_code == 0
        mock_sqlite_connect.assert_called_once_with(str(out_file))
        mock_conn.backup.assert_called_once_with(mock_dst)
        mock_dst.close.assert_called_once()
        assert "backed up" in result.output
