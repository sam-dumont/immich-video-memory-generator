"""Cache commands and preview cleanup must operate on the configured cache."""

import json
import sqlite3
from unittest.mock import patch

from click.testing import CliRunner

from immich_memories.cache.asset_score_cache import AssetScoreCache
from immich_memories.cache.database import VideoAnalysisCache
from immich_memories.cli import main
from immich_memories.config_loader import Config
from immich_memories.ui.pages.step1_cache import _clear_preview_cache, _get_preview_cache_stats


def test_cli_backup_and_score_commands_use_the_explicit_config(tmp_path):
    custom = tmp_path / "custom.db"
    default = tmp_path / "default.db"
    for path, asset in [(custom, "custom-only"), (default, "default-only")]:
        VideoAnalysisCache(db_path=path)
        AssetScoreCache(db_path=path).save_asset_score(
            asset_id=asset,
            asset_type="VIDEO",
            metadata_score=0.5,
            combined_score=0.5,
        )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"cache:\n  database: {custom}\n  directory: {tmp_path / 'custom-cache'}\n"
    )
    fallback = Config(
        cache={"database": str(default), "directory": str(tmp_path / "default-cache")}
    )
    backup, exported, imported = (
        tmp_path / name for name in ("backup.db", "export.json", "import.json")
    )
    imported.write_text('[{"asset_id": "imported", "asset_type": "VIDEO"}]')
    runner = CliRunner()
    # WHY: pin the global fallback separately; the explicit file is loaded by the real CLI.
    with (
        patch("immich_memories.cli.init_config_dir"),
        patch("immich_memories.config.get_config", return_value=fallback),
    ):
        for args in (
            ["backup", str(backup)],
            ["export", str(exported)],
            ["import", str(imported)],
            ["stats"],
        ):
            result = runner.invoke(
                main,
                ["--config", str(config_path), "cache", *args],
                env={
                    "IMMICH_MEMORIES_CACHE__DATABASE": None,
                    "IMMICH_MEMORIES_CACHE__DIRECTORY": None,
                },
            )
            assert result.exit_code == 0, result.output
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT asset_id FROM asset_scores").fetchall() == [("custom-only",)]
    assert [row["asset_id"] for row in json.loads(exported.read_text())] == ["custom-only"]
    with sqlite3.connect(custom) as db:
        assert set(db.execute("SELECT asset_id FROM asset_scores")) == {
            ("custom-only",),
            ("imported",),
        }
    with sqlite3.connect(default) as db:
        assert db.execute("SELECT asset_id FROM asset_scores").fetchall() == [("default-only",)]


def test_preview_stats_and_cleanup_follow_config_changes_at_call_time(tmp_path):
    first, second = (tmp_path / name for name in ("first", "second"))
    for path in (first, second):
        (path / "preview-cache").mkdir(parents=True)
        (path / "preview-cache" / "preview.mp4").write_bytes(b"preview")
    # WHY: the active configuration is the UI's boundary; no real user's cache is touched.
    with (
        patch("immich_memories.config.get_config") as active,
        patch(
            "immich_memories.ui.pages.step1_cache._PREVIEW_CACHE_DIR",
            first / "preview-cache",
            create=True,
        ),
    ):
        active.return_value = Config(cache={"directory": str(first)})
        assert _get_preview_cache_stats() == {"file_count": 1, "total_size_bytes": 7}
        active.return_value = Config(cache={"directory": str(second)})
        assert _clear_preview_cache() == 1
        assert _get_preview_cache_stats() == {"file_count": 0, "total_size_bytes": 0}
    assert (first / "preview-cache" / "preview.mp4").read_bytes() == b"preview"
    assert not (second / "preview-cache").exists()


def test_score_stats_initializes_a_new_configured_cache(tmp_path):
    database = tmp_path / "new-cache" / "analysis.db"
    config = tmp_path / "config.yaml"
    config.write_text(f"cache:\n  database: {database}\n")
    with patch("immich_memories.cli.init_config_dir"):
        result = CliRunner().invoke(
            main,
            ["--config", str(config), "cache", "stats"],
            env={"IMMICH_MEMORIES_CACHE__DATABASE": None},
        )
    assert result.exit_code == 0, result.output
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM asset_scores").fetchone() == (0,)
