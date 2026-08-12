"""Read-only storage classification and CLI contracts."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from immich_memories.cli import main
from immich_memories.config_loader import Config
from immich_memories.operations.storage_report import StorageClass, build_storage_report
from immich_memories.tracking.models import DeliveryStatus, RunMetadata
from immich_memories.tracking.run_database import RunDatabase


def _snapshot(root: Path) -> tuple[tuple[str, bool, int, int], ...]:
    return tuple(
        sorted(
            (
                str(path.relative_to(root)),
                path.is_dir(),
                path.stat().st_size,
                path.stat().st_mtime_ns,
            )
            for path in root.rglob("*")
        )
    )


def _write(directory: Path, name: str, size: int) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"x" * size)
    return path


def _config(tmp_path: Path) -> Config:
    return Config(
        output={"directory": str(tmp_path / "outputs")},
        cache={
            "directory": str(tmp_path / "cache"),
            "database": str(tmp_path / "state.db"),
        },
    )


def _save_run(
    db: RunDatabase,
    run_id: str,
    output: Path,
    status: str,
    delivery: DeliveryStatus = DeliveryStatus.NOT_REQUESTED,
) -> None:
    db.save_run(
        RunMetadata(
            run_id=run_id,
            created_at=datetime(2026, 8, 12, 8, 0),
            status=status,  # type: ignore[arg-type]
            output_path=str(output),
            delivery_status=delivery,
        )
    )


def test_storage_report_classifies_exact_output_joins_without_mutation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    output_root = config.output.output_path
    cache_root = config.cache.cache_path
    completed = _write(output_root / "completed", "memory.mp4", 11)
    failed = _write(output_root / "failed", "partial.bin", 13)
    running = _write(output_root / "running", "render.tmp", 17)
    pending = _write(output_root / "pending", "memory.mp4", 19)
    _write(output_root / "completed-copy", "junk.bin", 23)
    _write(cache_root / "video-cache", "asset.mp4", 29)
    _write(cache_root / "thumbnails", "asset.jpg", 31)

    db = RunDatabase(config.cache.database_path)
    _save_run(db, "completed", completed, "completed")
    _save_run(db, "failed", failed, "failed")
    _save_run(db, "running", running, "running")
    _save_run(db, "pending", pending, "completed", DeliveryStatus.PENDING)
    before = (_snapshot(output_root), _snapshot(cache_root))

    report = build_storage_report(config, db)

    after = (_snapshot(output_root), _snapshot(cache_root))
    by_name = {entry.path.name: entry for entry in report.directories}
    assert after == before
    assert by_name["completed"].classification is StorageClass.COMPLETED
    assert by_name["failed"].classification is StorageClass.FAILED
    assert by_name["running"].classification is StorageClass.RUNNING
    assert by_name["pending"].classification is StorageClass.PENDING_DELIVERY
    assert by_name["completed-copy"].classification is StorageClass.ORPHANED
    assert by_name["video-cache"].classification is StorageClass.UNKNOWN
    assert by_name["thumbnails"].classification is StorageClass.UNKNOWN
    assert report.summary[StorageClass.UNKNOWN].bytes == 60
    assert report.summary[StorageClass.UNKNOWN].file_count == 2
    assert [entry.path.name for entry in report.largest_directories[:3]] == [
        "thumbnails",
        "video-cache",
        "completed-copy",
    ]


def test_storage_report_does_not_create_missing_configured_roots(tmp_path: Path) -> None:
    config = _config(tmp_path)
    db = RunDatabase(config.cache.database_path)

    report = build_storage_report(config, db)

    assert report.total_bytes == 0
    assert report.directories == ()
    assert not config.output.output_path.exists()
    assert not config.cache.cache_path.exists()


def test_runs_storage_json_is_one_read_only_document(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _write(config.output.output_path / "orphan", "junk.bin", 7)
    RunDatabase(config.cache.database_path)

    with (
        patch("immich_memories.cli.init_config_dir"),
        patch("immich_memories.cli.get_config", return_value=config),
        patch("immich_memories.config.get_config", return_value=config),
    ):
        result = CliRunner().invoke(main, ["runs", "storage", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["total_bytes"] == 7
    assert payload["summary"]["orphaned"]["directory_count"] == 1
    assert "delete" not in result.output.casefold()


def test_runs_storage_does_not_create_a_missing_database(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _write(config.output.output_path / "orphan", "junk.bin", 7)
    assert not config.cache.database_path.exists()

    with (
        patch("immich_memories.cli.init_config_dir"),
        patch("immich_memories.cli.get_config", return_value=config),
        patch("immich_memories.config.get_config", return_value=config),
    ):
        result = CliRunner().invoke(main, ["runs", "storage", "--json"])

    assert result.exit_code == 0
    assert not config.cache.database_path.exists()


def test_runs_storage_does_not_change_existing_database_bytes_or_mtime(tmp_path: Path) -> None:
    config = _config(tmp_path)
    db = RunDatabase(config.cache.database_path)
    _write(config.output.output_path / "known", "memory.mp4", 5)
    _save_run(db, "known", config.output.output_path / "known" / "memory.mp4", "completed")
    before = (
        config.cache.database_path.read_bytes(),
        config.cache.database_path.stat().st_mtime_ns,
    )

    with (
        patch("immich_memories.cli.init_config_dir"),
        patch("immich_memories.cli.get_config", return_value=config),
        patch("immich_memories.config.get_config", return_value=config),
    ):
        result = CliRunner().invoke(main, ["runs", "storage", "--json"])

    after = (config.cache.database_path.read_bytes(), config.cache.database_path.stat().st_mtime_ns)
    assert result.exit_code == 0
    assert after == before
