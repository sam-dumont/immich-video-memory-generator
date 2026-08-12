"""Storage audit contracts, including mutation and symlink boundaries."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from immich_memories.cli import main
from immich_memories.config_loader import Config
from immich_memories.operations.storage_report import (
    ReadOnlyRunStore,
    StorageClass,
    build_storage_report,
)
from immich_memories.tracking.models import DeliveryStatus, RunMetadata
from immich_memories.tracking.run_database import RunDatabase


def _config(tmp_path: Path) -> Config:
    return Config(
        output={"directory": str(tmp_path / "outputs")},
        cache={"directory": str(tmp_path / "cache"), "database": str(tmp_path / "state.db")},
    )


def _write(directory: Path, name: str, size: int) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"x" * size)
    return path


def _save_run(
    db: RunDatabase, run_id: str, output: Path, status: str, delivery: DeliveryStatus
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


def test_storage_report_is_read_only_and_classifies_exact_output_paths(tmp_path: Path) -> None:
    config = _config(tmp_path)
    complete = _write(config.output.output_path / "complete", "memory.mp4", 11)
    failed = _write(config.output.output_path / "failed", "partial.mp4", 13)
    running = _write(config.output.output_path / "running", "render.tmp", 17)
    pending = _write(config.output.output_path / "pending", "memory.mp4", 19)
    _write(config.output.output_path / "complete-copy", "junk.bin", 23)
    _write(config.cache.cache_path / "video", "asset.mp4", 29)
    db = RunDatabase(config.cache.database_path)
    _save_run(db, "complete", complete, "completed", DeliveryStatus.NOT_REQUESTED)
    _save_run(db, "failed", failed, "failed", DeliveryStatus.NOT_REQUESTED)
    _save_run(db, "running", running, "running", DeliveryStatus.NOT_REQUESTED)
    _save_run(db, "pending", pending, "completed", DeliveryStatus.PENDING)
    before = config.cache.database_path.read_bytes()

    report = build_storage_report(config, db)

    by_name = {entry.path.name: entry for entry in report.directories}
    assert config.cache.database_path.read_bytes() == before
    assert by_name["complete"].classification is StorageClass.COMPLETED
    assert by_name["failed"].classification is StorageClass.FAILED
    assert by_name["running"].classification is StorageClass.RUNNING
    assert by_name["pending"].classification is StorageClass.PENDING_DELIVERY
    assert by_name["complete-copy"].classification is StorageClass.ORPHANED
    assert by_name["video"].classification is StorageClass.UNKNOWN
    assert report.total_bytes == 112


def test_storage_report_never_follows_configured_root_or_child_symlinks(tmp_path: Path) -> None:
    config = _config(tmp_path)
    outside = _write(tmp_path / "outside", "secret.mp4", 101)
    config.output.output_path.parent.mkdir(parents=True, exist_ok=True)
    config.output.output_path.symlink_to(outside.parent, target_is_directory=True)
    config.cache.cache_path.mkdir(parents=True)
    (config.cache.cache_path / "escape").symlink_to(outside.parent, target_is_directory=True)

    report = build_storage_report(config, RunDatabase(config.cache.database_path))

    assert report.directories == ()
    assert report.total_bytes == 0


def test_read_only_store_rejects_symlinked_database_paths_before_connecting(
    tmp_path: Path, monkeypatch
) -> None:
    """Read-only inventory cannot follow a configured database symlink or its ancestor."""
    import sqlite3

    outside_database = tmp_path / "outside.db"
    RunDatabase(outside_database)
    direct_link = tmp_path / "direct-link.db"
    direct_link.symlink_to(outside_database)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(tmp_path, target_is_directory=True)
    ancestor_link = linked_parent / "outside.db"
    connected: list[str] = []
    original_connect = sqlite3.connect

    def no_connect(*args, **kwargs):
        connected.append(str(args[0]))
        return original_connect(*args, **kwargs)

    monkeypatch.setattr("immich_memories.operations.storage_report.sqlite3.connect", no_connect)

    assert ReadOnlyRunStore(direct_link).list_runs() == []
    assert ReadOnlyRunStore(ancestor_link).list_runs() == []
    assert connected == []


def test_read_only_store_fails_closed_when_database_is_swapped_at_connect(
    tmp_path: Path, monkeypatch
) -> None:
    """Validation and SQLite open must use the same database identity."""
    import sqlite3

    safe_database = tmp_path / "safe.db"
    outside_database = tmp_path / "outside.db"
    RunDatabase(safe_database)
    outside = RunDatabase(outside_database)
    _save_run(
        outside,
        "outside-run",
        tmp_path / "outside.mp4",
        "completed",
        DeliveryStatus.NOT_REQUESTED,
    )
    original_connect = sqlite3.connect
    swapped = False

    def swap_then_connect(*args, **kwargs):
        nonlocal swapped
        if not swapped:
            safe_database.unlink()
            safe_database.symlink_to(outside_database)
            swapped = True
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(
        "immich_memories.operations.storage_report.sqlite3.connect", swap_then_connect
    )

    assert ReadOnlyRunStore(safe_database).list_runs() == []
    assert swapped


def test_storage_report_fails_closed_when_no_follow_descriptor_support_is_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    """Unsupported descriptor APIs produce an empty read-only report rather than a crash."""
    config = _config(tmp_path)
    _write(config.output.output_path / "orphan", "memory.mp4", 9)

    monkeypatch.delattr("immich_memories.operations.storage_report.os.O_NOFOLLOW", raising=False)

    report = build_storage_report(config, MagicMock(list_runs=lambda **_kwargs: []))

    assert report.directories == ()
    assert report.root_files == ()
    assert report.total_bytes == 0


def test_storage_report_fails_closed_when_fd_scandir_is_not_supported(
    tmp_path: Path, monkeypatch
) -> None:
    """An fd-scandir capability gap cannot turn storage reporting into a path-following scan."""
    import os

    config = _config(tmp_path)
    _write(config.output.output_path / "orphan", "memory.mp4", 9)
    original_scandir = os.scandir

    def unsupported_fd_scandir(target):
        if isinstance(target, int):
            raise NotImplementedError("fd scandir unsupported")
        return original_scandir(target)

    monkeypatch.setattr(
        "immich_memories.operations.storage_report.os.scandir", unsupported_fd_scandir
    )

    report = build_storage_report(config, MagicMock(list_runs=lambda **_kwargs: []))

    assert report.directories == ()
    assert report.root_files == ()


def test_storage_report_missing_roots_and_top_ten_are_safe(tmp_path: Path) -> None:
    config = _config(tmp_path)
    db = RunDatabase(config.cache.database_path)

    empty = build_storage_report(config, db)
    assert empty.directories == ()
    assert not config.output.output_path.exists()
    assert not config.cache.cache_path.exists()

    for index in range(12):
        _write(config.output.output_path / f"orphan-{index}", "data", index + 1)
    report = build_storage_report(config, db)
    assert len(report.directories) == 12
    assert len(report.largest_directories) == 10
    assert report.total_files == 12
    assert report.total_bytes == sum(range(1, 13))


def test_runs_storage_json_and_human_output_do_not_create_or_mutate_database(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _write(config.output.output_path / "orphan", "junk.bin", 7)
    assert not config.cache.database_path.exists()
    patches = (
        patch("immich_memories.cli.init_config_dir"),
        patch("immich_memories.cli.get_config", return_value=config),
        patch("immich_memories.config.get_config", return_value=config),
    )
    with patches[0], patches[1], patches[2]:
        json_result = CliRunner().invoke(main, ["runs", "storage", "--json"])
    assert json_result.exit_code == 0
    assert json.loads(json_result.stdout)["total_bytes"] == 7
    assert not config.cache.database_path.exists()

    with patches[0], patches[1], patches[2]:
        human_result = CliRunner().invoke(main, ["runs", "storage"])
    assert human_result.exit_code == 0
    assert "Storage by Status" in human_result.output
    assert "delete" not in human_result.output.casefold()


def test_storage_report_requires_an_exact_output_directory_join(tmp_path: Path) -> None:
    config = _config(tmp_path)
    nested_output = _write(config.output.output_path / "run" / "nested", "memory.mp4", 3)
    db = RunDatabase(config.cache.database_path)
    _save_run(db, "nested", nested_output, "completed", DeliveryStatus.NOT_REQUESTED)

    report = build_storage_report(config, db)

    entry = next(entry for entry in report.directories if entry.path.name == "run")
    assert entry.classification is StorageClass.ORPHANED
    assert entry.run_id is None


def test_storage_report_does_not_join_a_directory_when_its_exact_output_is_missing(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    directory = config.output.output_path / "run"
    _write(directory, "different-file.mp4", 3)
    db = RunDatabase(config.cache.database_path)
    _save_run(
        db,
        "missing-exact-output",
        directory / "expected-memory.mp4",
        "completed",
        DeliveryStatus.NOT_REQUESTED,
    )

    report = build_storage_report(config, db)

    entry = next(entry for entry in report.directories if entry.path.name == "run")
    assert entry.classification is StorageClass.ORPHANED
    assert entry.run_id is None


def test_storage_report_tolerates_a_file_disappearing_during_a_read(tmp_path: Path) -> None:
    from immich_memories.operations import storage_report

    config = _config(tmp_path)
    disappearing = _write(config.output.output_path / "orphan", "vanishing.bin", 9)
    original_stat = storage_report.os.stat

    def stat_once(name: str, *args, **kwargs):
        if name == disappearing.name:
            raise FileNotFoundError("gone during report")
        return original_stat(name, *args, **kwargs)

    with patch.object(storage_report.os, "stat", stat_once):
        report = build_storage_report(config, MagicMock(list_runs=lambda **_kwargs: []))

    assert report.total_bytes == 0
    assert report.total_files == 0


def test_storage_report_rejects_a_file_swapped_for_an_outside_symlink(
    tmp_path: Path, monkeypatch
) -> None:
    """A mutation between discovery and measurement cannot pull bytes through a symlink."""
    config = _config(tmp_path)
    volatile = _write(config.output.output_path / "orphan", "volatile.bin", 3)
    outside = _write(tmp_path / "outside", "secret.bin", 101)
    from immich_memories.operations import storage_report

    original_stat = storage_report._entry_stat_no_follow
    swapped = False

    def swap_at_measurement_boundary(directory_fd: int, name: str):
        nonlocal swapped
        if name == volatile.name and not swapped:
            volatile.unlink()
            volatile.symlink_to(outside)
            swapped = True
        return original_stat(directory_fd, name)

    monkeypatch.setattr(storage_report, "_entry_stat_no_follow", swap_at_measurement_boundary)

    report = build_storage_report(config, MagicMock(list_runs=lambda **_kwargs: []))

    assert swapped
    assert report.total_files == 0
    assert report.total_bytes == 0


def test_directory_usage_uses_a_single_no_follow_file_measurement_boundary(
    tmp_path: Path, monkeypatch
) -> None:
    """A regular child replaced at measurement time cannot count an outside target."""
    from immich_memories.operations import storage_report
    from immich_memories.operations.storage_report import _directory_usage

    directory = tmp_path / "inventory"
    volatile = _write(directory, "volatile.bin", 3)
    outside = _write(tmp_path / "outside", "secret.bin", 97)
    original_stat = storage_report._entry_stat_no_follow
    swapped = False

    def swap_at_measurement_boundary(directory_fd: int, name: str):
        nonlocal swapped
        if name == volatile.name and not swapped:
            volatile.unlink()
            volatile.symlink_to(outside)
            swapped = True
        return original_stat(directory_fd, name)

    monkeypatch.setattr(storage_report, "_entry_stat_no_follow", swap_at_measurement_boundary)

    assert _directory_usage(directory) == (0, 0)
    assert swapped


def test_storage_report_revalidates_a_directory_swapped_for_an_outside_symlink(
    tmp_path: Path, monkeypatch
) -> None:
    """A configured child changed after discovery cannot be walked through its target."""
    config = _config(tmp_path)
    child = config.output.output_path / "orphan"
    _write(child, "inside.bin", 3)
    outside = _write(tmp_path / "outside", "secret.bin", 97)
    original_stat = Path.stat
    child_checks = 0
    swapped = False

    def swap_after_discovery(path: Path, *args, **kwargs):
        nonlocal child_checks, swapped
        if path == child:
            child_checks += 1
            result = original_stat(path, *args, **kwargs)
            if child_checks == 1 and not swapped:
                moved = tmp_path / "moved-child"
                child.rename(moved)
                child.symlink_to(outside.parent, target_is_directory=True)
                swapped = True
            return result
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", swap_after_discovery)

    report = build_storage_report(config, MagicMock(list_runs=lambda **_kwargs: []))

    assert swapped
    assert report.total_files == 0
    assert report.total_bytes == 0


def test_storage_report_never_opens_a_configured_root_through_a_swapped_ancestor(
    tmp_path: Path, monkeypatch
) -> None:
    """Replacing an ancestor cannot redirect the configured root outside its lexical path."""
    config = _config(tmp_path)
    _write(config.output.output_path / "orphan", "inside.bin", 3)
    outside = _write(tmp_path / "outside", "secret.bin", 97)
    original_open = __import__("os").open
    ancestor = config.output.output_path
    swapped = False

    def swap_ancestor(path, flags, *args, **kwargs):
        nonlocal swapped
        if str(path) == ancestor.name and not swapped:
            ancestor.rename(tmp_path / "moved-output-root")
            ancestor.symlink_to(outside.parent, target_is_directory=True)
            swapped = True
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr("immich_memories.operations.storage_report.os.open", swap_ancestor)

    report = build_storage_report(config, MagicMock(list_runs=lambda **_kwargs: []))

    assert swapped
    assert report.total_files == 0
    assert report.total_bytes == 0


def test_directory_usage_binds_entry_measurement_to_the_opened_directory(
    tmp_path: Path, monkeypatch
) -> None:
    """A rename after scan cannot replace an opened directory's entries with outside bytes."""
    import os

    from immich_memories.operations.storage_report import _directory_usage

    directory = tmp_path / "inventory"
    _write(directory, "inside.bin", 3)
    outside = _write(tmp_path / "outside", "secret.bin", 97)
    original_scandir = os.scandir
    swapped = False

    def swap_after_scan(target):
        nonlocal swapped
        entries = original_scandir(target)
        if isinstance(target, int) and not swapped:
            moved = tmp_path / "moved-inventory"
            directory.rename(moved)
            directory.symlink_to(outside.parent, target_is_directory=True)
            swapped = True
        return entries

    monkeypatch.setattr("immich_memories.operations.storage_report.os.scandir", swap_after_scan)

    assert _directory_usage(directory) == (1, 3)
    assert swapped


def test_storage_report_counts_regular_files_at_configured_roots_without_putting_them_in_top_ten(
    tmp_path: Path,
) -> None:
    """Root files are accounted by exact path while largest entries remain directories."""
    config = _config(tmp_path)
    output = _write(config.output.output_path, "memory.mp4", 41)
    _write(config.cache.cache_path, "cache.sqlite", 59)
    db = RunDatabase(config.cache.database_path)
    _save_run(db, "root-output", output, "completed", DeliveryStatus.NOT_REQUESTED)

    report = build_storage_report(config, db)

    assert report.directories == ()
    assert report.total_files == 2
    assert report.total_bytes == 100
    assert report.largest_directories == ()
    assert report.summary[StorageClass.COMPLETED].to_dict() == {
        "directory_count": 0,
        "file_count": 1,
        "bytes": 41,
    }
    assert report.summary[StorageClass.UNKNOWN].to_dict() == {
        "directory_count": 0,
        "file_count": 1,
        "bytes": 59,
    }
    assert [entry.classification for entry in report.root_files] == [
        StorageClass.UNKNOWN,
        StorageClass.COMPLETED,
    ]


def test_storage_report_never_calls_mutating_paths_or_database_methods(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _write(config.output.output_path / "orphan", "data.bin", 5)
    db = MagicMock(list_runs=lambda **_kwargs: [])
    configured_secret = "-".join(("storage", "report", "secret", "551"))
    config.immich.api_key = configured_secret

    with (
        patch.object(Path, "unlink", side_effect=AssertionError("must not unlink")) as unlink,
        patch.object(Path, "rename", side_effect=AssertionError("must not rename")) as rename,
    ):
        report = build_storage_report(config, db)

    assert configured_secret not in str(report.to_dict())
    unlink.assert_not_called()
    rename.assert_not_called()
    assert db.method_calls == []
