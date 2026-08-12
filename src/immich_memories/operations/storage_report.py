"""Read-only storage inventory for configured output and cache roots."""

from __future__ import annotations

import os
import sqlite3
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from immich_memories.tracking.models import DeliveryStatus, RunMetadata

if TYPE_CHECKING:
    from immich_memories.config_loader import Config


class RunReader(Protocol):
    """The narrow read seam required by storage reporting."""

    def list_runs(self, limit: int = 50, offset: int = 0) -> list[RunMetadata]: ...


class ReadOnlyRunStore:
    """SQLite reader that never creates, migrates, or updates a database."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def list_runs(self, limit: int = 50, offset: int = 0) -> list[RunMetadata]:
        database_fd = _open_regular_file_with_safe_ancestors(self.db_path)
        if database_fd is None:
            return []
        try:
            uri = f"file:/dev/fd/{database_fd}?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only = ON")
                has_runs = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pipeline_runs'"
                ).fetchone()
                if has_runs is None:
                    return []
                rows = conn.execute(
                    "SELECT * FROM pipeline_runs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
        except (sqlite3.Error, OSError):
            return []
        finally:
            os.close(database_fd)
        from immich_memories.tracking.run_database import row_to_run

        return [row_to_run(row) for row in rows]


class StorageClass(StrEnum):
    """Operator-facing ownership and lifecycle classes."""

    COMPLETED = "completed"
    FAILED = "failed"
    RUNNING = "running"
    PENDING_DELIVERY = "pending-delivery"
    ORPHANED = "orphaned"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class StorageSummary:
    """Aggregate usage for one storage class."""

    directory_count: int = 0
    file_count: int = 0
    bytes: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "directory_count": self.directory_count,
            "file_count": self.file_count,
            "bytes": self.bytes,
        }


@dataclass(frozen=True)
class StorageDirectory:
    """One direct, real directory below an explicitly configured root."""

    path: Path
    root: str
    classification: StorageClass
    file_count: int
    bytes: int
    run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "root": self.root,
            "classification": self.classification.value,
            "file_count": self.file_count,
            "bytes": self.bytes,
            "run_id": self.run_id,
        }


@dataclass(frozen=True)
class StorageFile:
    """One direct regular file at an explicitly configured root."""

    path: Path
    root: str
    classification: StorageClass
    bytes: int
    run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "root": self.root,
            "classification": self.classification.value,
            "bytes": self.bytes,
            "run_id": self.run_id,
        }


@dataclass(frozen=True)
class StorageReport:
    """Complete immutable storage inventory."""

    directories: tuple[StorageDirectory, ...]
    root_files: tuple[StorageFile, ...]
    summary: dict[StorageClass, StorageSummary]
    largest_directories: tuple[StorageDirectory, ...]

    @property
    def total_bytes(self) -> int:
        return sum(entry.bytes for entry in self.directories) + sum(
            entry.bytes for entry in self.root_files
        )

    @property
    def total_files(self) -> int:
        return sum(entry.file_count for entry in self.directories) + len(self.root_files)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_bytes": self.total_bytes,
            "total_files": self.total_files,
            "summary": {
                classification.value: self.summary[classification].to_dict()
                for classification in StorageClass
            },
            "directories": [entry.to_dict() for entry in self.directories],
            "root_files": [entry.to_dict() for entry in self.root_files],
            "largest_directories": [entry.to_dict() for entry in self.largest_directories],
        }


def _no_follow_stat(path: Path) -> os.stat_result | None:
    """Read exactly one path boundary, never following a symlink."""
    try:
        return path.stat(follow_symlinks=False)
    except OSError:
        return None


def _is_real_directory(path: Path) -> bool:
    path_stat = _no_follow_stat(path)
    return path_stat is not None and stat.S_ISDIR(path_stat.st_mode)


def _configured_root_entries(root: Path) -> tuple[list[Path], list[tuple[Path, int]]]:
    """Snapshot direct entries from a fully no-follow configured root descriptor."""
    root_fd = _open_configured_root_no_follow(root)
    if root_fd is None:
        return [], []
    try:
        entries = list(os.scandir(root_fd))
        directories = sorted(
            (
                root / entry.name
                for entry in entries
                if _directory_entry_is_real_directory(root_fd, entry.name)
            ),
            key=str,
        )
        files = sorted(
            (
                (root / entry.name, size)
                for entry in entries
                if (size := _regular_file_size_at(root_fd, entry.name)) is not None
            ),
            key=lambda item: str(item[0]),
        )
        return directories, files
    except (OSError, TypeError, NotImplementedError):
        return [], []
    finally:
        os.close(root_fd)


def _is_contained_lexically(root: Path, path: Path) -> bool:
    """Keep inventory paths beneath the configured root without resolving links."""
    try:
        path.absolute().relative_to(root.absolute())
    except ValueError:
        return False
    return True


def _open_directory_no_follow(path: Path, *, dir_fd: int | None = None) -> int | None:
    """Open a directory only when this exact path boundary is not a symlink."""
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        return None
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | no_follow
    try:
        return os.open(path if dir_fd is None else path.name, flags, dir_fd=dir_fd)
    except (OSError, TypeError, NotImplementedError):
        return None


def _open_configured_root_no_follow(root: Path) -> int | None:
    """Open every configured-root path component without traversing a symlink."""
    absolute_root = root.absolute()
    current_fd = _open_directory_no_follow(Path(absolute_root.anchor))
    if current_fd is None:
        return None
    try:
        for part in absolute_root.parts[1:]:
            next_fd = _open_directory_no_follow(Path(part), dir_fd=current_fd)
            os.close(current_fd)
            if next_fd is None:
                return None
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        return None


def _open_regular_file_with_safe_ancestors(path: Path) -> int | None:
    """Open a database through no-follow ancestors and retain its exact identity."""
    if not hasattr(os, "O_NOFOLLOW"):
        return None
    absolute_path = path.absolute()
    current_fd = _open_directory_no_follow(Path(absolute_path.anchor))
    if current_fd is None:
        return None
    try:
        for part in absolute_path.parts[1:-1]:
            next_fd = _open_directory_no_follow(Path(part), dir_fd=current_fd)
            if next_fd is None:
                return None
            os.close(current_fd)
            current_fd = next_fd
        try:
            return os.open(
                absolute_path.name,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
        except (OSError, TypeError, NotImplementedError):
            return None
    finally:
        os.close(current_fd)


def _directory_usage(directory: Path, root: Path | None = None) -> tuple[int, int]:
    """Measure a configured child through no-follow directory descriptors."""
    root = root or directory.parent
    if not _is_contained_lexically(root, directory) or not _is_real_directory(directory):
        return 0, 0
    root_fd = _open_configured_root_no_follow(root)
    if root_fd is None:
        return 0, 0
    try:
        if directory.absolute().parent != root.absolute():
            return 0, 0
        directory_fd = _open_directory_no_follow(directory, dir_fd=root_fd)
    finally:
        os.close(root_fd)
    if directory_fd is None:
        return 0, 0
    try:
        return _directory_fd_usage(directory_fd, directory, root)
    finally:
        os.close(directory_fd)


def _directory_fd_usage(directory_fd: int, directory: Path, root: Path) -> tuple[int, int]:
    """Traverse only descendants opened from an already verified directory descriptor."""
    file_count = 0
    byte_count = 0
    try:
        entries = list(os.scandir(directory_fd))
    except (OSError, TypeError, NotImplementedError):
        return 0, 0
    for entry in entries:
        path = directory / entry.name
        if not _is_contained_lexically(root, path):
            continue
        path_stat = _entry_stat_no_follow(directory_fd, entry.name)
        if path_stat is None:
            continue
        if stat.S_ISREG(path_stat.st_mode):
            file_count += 1
            byte_count += path_stat.st_size
            continue
        if not stat.S_ISDIR(path_stat.st_mode):
            continue
        child_fd = _open_directory_no_follow(path, dir_fd=directory_fd)
        if child_fd is None:
            continue
        try:
            child_count, child_bytes = _directory_fd_usage(child_fd, path, root)
            file_count += child_count
            byte_count += child_bytes
        finally:
            os.close(child_fd)
    return file_count, byte_count


def _directory_entry_is_real_directory(directory_fd: int, name: str) -> bool:
    """Identify a direct directory entry without traversing a symlink."""
    entry_stat = _entry_stat_no_follow(directory_fd, name)
    if entry_stat is None:
        return False
    return stat.S_ISDIR(entry_stat.st_mode)


def _regular_file_size_at(directory_fd: int, name: str) -> int | None:
    """Measure a direct entry through its verified parent directory descriptor."""
    entry_stat = _entry_stat_no_follow(directory_fd, name)
    if entry_stat is None:
        return None
    return entry_stat.st_size if stat.S_ISREG(entry_stat.st_mode) else None


def _entry_stat_no_follow(directory_fd: int, name: str) -> os.stat_result | None:
    """Read one child boundary relative to an already verified directory descriptor."""
    try:
        return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError:
        return None


def _regular_file_size(path: Path) -> int | None:
    """Return a stable regular-file size, or omit a concurrent mutation."""
    path_stat = _no_follow_stat(path)
    return path_stat.st_size if path_stat is not None and stat.S_ISREG(path_stat.st_mode) else None


def _all_runs(db: RunReader) -> list[RunMetadata]:
    runs: list[RunMetadata] = []
    offset = 0
    while True:
        page = db.list_runs(limit=500, offset=offset)
        runs.extend(page)
        if len(page) < 500:
            return runs
        offset += 500


def _run_by_exact_output_path(db: RunReader) -> dict[Path, RunMetadata]:
    matches: dict[Path, RunMetadata] = {}
    for run in reversed(_all_runs(db)):
        if run.output_path:
            output_path = Path(run.output_path).expanduser()
            if _regular_file_size(output_path) is not None:
                matches[output_path.absolute()] = run
    return matches


def _classification_for_run(run: RunMetadata) -> StorageClass:
    if run.status == "completed" and run.delivery_status is DeliveryStatus.PENDING:
        return StorageClass.PENDING_DELIVERY
    if run.status == "completed":
        return StorageClass.COMPLETED
    if run.status == "failed":
        return StorageClass.FAILED
    if run.status == "running":
        return StorageClass.RUNNING
    return StorageClass.UNKNOWN


def _classification_for_path(
    root_name: str, path: Path, runs_by_output_path: dict[Path, RunMetadata]
) -> tuple[StorageClass, RunMetadata | None]:
    """Classify one exact path, retaining output ownership only at the output root."""
    run = runs_by_output_path.get(path.absolute()) if root_name == "output" else None
    if run is not None:
        return _classification_for_run(run), run
    return (StorageClass.ORPHANED if root_name == "output" else StorageClass.UNKNOWN), None


def _directory_run_by_path(runs_by_output_path: dict[Path, RunMetadata]) -> dict[Path, RunMetadata]:
    """Map only direct output-parent directories to their exact output run."""
    return {path.parent: run for path, run in runs_by_output_path.items()}


def _storage_directory(
    root_name: str,
    root: Path,
    directory: Path,
    runs_by_directory: dict[Path, RunMetadata],
) -> StorageDirectory:
    """Measure and classify one direct child directory."""
    run = runs_by_directory.get(directory.absolute()) if root_name == "output" else None
    classification = (
        _classification_for_run(run)
        if run is not None
        else StorageClass.ORPHANED
        if root_name == "output"
        else StorageClass.UNKNOWN
    )
    file_count, byte_count = _directory_usage(directory, root)
    return StorageDirectory(
        path=directory,
        root=root_name,
        classification=classification,
        file_count=file_count,
        bytes=byte_count,
        run_id=run.run_id if run is not None else None,
    )


def _storage_file(
    root_name: str,
    path: Path,
    byte_count: int,
    runs_by_output_path: dict[Path, RunMetadata],
) -> StorageFile:
    """Classify one regular file directly beneath a configured root."""
    classification, run = _classification_for_path(root_name, path, runs_by_output_path)
    return StorageFile(
        path=path,
        root=root_name,
        classification=classification,
        bytes=byte_count,
        run_id=run.run_id if run is not None else None,
    )


def _inspect_root(
    root_name: str, root: Path, runs_by_output_path: dict[Path, RunMetadata]
) -> tuple[list[StorageDirectory], list[StorageFile]]:
    directories, direct_files = _configured_root_entries(root)
    runs_by_directory = _directory_run_by_path(runs_by_output_path)
    entries = [
        _storage_directory(root_name, root, directory, runs_by_directory)
        for directory in directories
    ]
    root_files = [
        _storage_file(root_name, path, byte_count, runs_by_output_path)
        for path, byte_count in direct_files
    ]
    return entries, root_files


def _summarize(
    entries: list[StorageDirectory], root_files: list[StorageFile]
) -> dict[StorageClass, StorageSummary]:
    return {
        classification: StorageSummary(
            directory_count=sum(entry.classification is classification for entry in entries),
            file_count=sum(
                entry.file_count for entry in entries if entry.classification is classification
            )
            + sum(item.classification is classification for item in root_files),
            bytes=sum(entry.bytes for entry in entries if entry.classification is classification)
            + sum(item.bytes for item in root_files if item.classification is classification),
        )
        for classification in StorageClass
    }


def build_storage_report(config: Config, db: RunReader) -> StorageReport:
    """Inspect only configured roots and exact output-path joins without mutation."""
    output_root = config.output.output_path.expanduser()
    cache_root = config.cache.cache_path.expanduser()
    runs_by_output_path = _run_by_exact_output_path(db)
    output_entries, output_files = _inspect_root("output", output_root, runs_by_output_path)
    cache_entries, cache_files = _inspect_root("cache", cache_root, runs_by_output_path)
    entries = output_entries + cache_entries
    root_files = output_files + cache_files
    ordered = tuple(sorted(entries, key=lambda entry: str(entry.path)))
    ordered_files = tuple(sorted(root_files, key=lambda entry: str(entry.path)))
    largest = tuple(sorted(entries, key=lambda entry: (-entry.bytes, str(entry.path)))[:10])
    return StorageReport(ordered, ordered_files, _summarize(entries, root_files), largest)
