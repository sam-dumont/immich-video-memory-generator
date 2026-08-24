"""Read-only storage inventory for configured output and cache roots."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from immich_memories.tracking.models import DeliveryStatus, RunMetadata

if TYPE_CHECKING:
    from immich_memories.config_loader import Config


class RunReader(Protocol):
    """Small read seam required by the storage inventory."""

    def list_runs(self, limit: int = 50, offset: int = 0) -> list[RunMetadata]: ...


class ReadOnlyRunStore:
    """SQLite run reader that never creates or migrates the configured database."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def list_runs(self, limit: int = 50, offset: int = 0) -> list[RunMetadata]:
        if not self.db_path.is_file():
            return []
        uri = f"{self.db_path.resolve(strict=True).as_uri()}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only = ON")
                table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pipeline_runs'"
                ).fetchone()
                if table is None:
                    return []
                rows = conn.execute(
                    "SELECT * FROM pipeline_runs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
        except sqlite3.Error:
            return []

        from immich_memories.tracking.run_database_rows import row_to_run

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
    """Aggregate size and count for one storage class."""

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
    """One direct child of a configured storage root."""

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
class StorageReport:
    """Complete read-only inventory of configured application storage."""

    directories: tuple[StorageDirectory, ...]
    summary: dict[StorageClass, StorageSummary]
    largest_directories: tuple[StorageDirectory, ...]

    @property
    def total_bytes(self) -> int:
        return sum(entry.bytes for entry in self.directories)

    @property
    def total_files(self) -> int:
        return sum(entry.file_count for entry in self.directories)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_bytes": self.total_bytes,
            "total_files": self.total_files,
            "summary": {
                classification.value: self.summary[classification].to_dict()
                for classification in StorageClass
            },
            "directories": [entry.to_dict() for entry in self.directories],
            "largest_directories": [entry.to_dict() for entry in self.largest_directories],
        }


def _configured_child_directories(root: Path) -> list[Path]:
    if not root.is_dir() or root.is_symlink():
        return []
    try:
        return sorted(
            (child for child in root.iterdir() if child.is_dir() and not child.is_symlink()),
            key=str,
        )
    except OSError:
        return []


def _directory_usage(directory: Path) -> tuple[int, int]:
    file_count = 0
    byte_count = 0
    for current_root, dir_names, file_names in os.walk(directory, followlinks=False):
        current = Path(current_root)
        dir_names[:] = [name for name in dir_names if not (current / name).is_symlink()]
        for name in file_names:
            path = current / name
            if path.is_symlink():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if path.is_file():
                file_count += 1
                byte_count += stat.st_size
    return file_count, byte_count


def _all_runs(db: RunReader) -> list[RunMetadata]:
    runs: list[RunMetadata] = []
    offset = 0
    page_size = 500
    while True:
        page = db.list_runs(limit=page_size, offset=offset)
        runs.extend(page)
        if len(page) < page_size:
            return runs
        offset += page_size


def _run_by_exact_output_directory(db: RunReader) -> dict[Path, RunMetadata]:
    matches: dict[Path, RunMetadata] = {}
    for run in reversed(_all_runs(db)):
        if run.output_path:
            matches[Path(run.output_path).expanduser().resolve(strict=False).parent] = run
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


def _inspect_root(
    root_name: str,
    root: Path,
    runs_by_directory: dict[Path, RunMetadata],
) -> list[StorageDirectory]:
    entries: list[StorageDirectory] = []
    for directory in _configured_child_directories(root):
        exact_directory = directory.resolve(strict=False)
        run = runs_by_directory.get(exact_directory) if root_name == "output" else None
        if run is not None:
            classification = _classification_for_run(run)
        elif root_name == "output":
            classification = StorageClass.ORPHANED
        else:
            classification = StorageClass.UNKNOWN
        file_count, byte_count = _directory_usage(directory)
        entries.append(
            StorageDirectory(
                path=directory,
                root=root_name,
                classification=classification,
                file_count=file_count,
                bytes=byte_count,
                run_id=run.run_id if run is not None else None,
            )
        )
    return entries


def _summarize(entries: list[StorageDirectory]) -> dict[StorageClass, StorageSummary]:
    summaries: dict[StorageClass, StorageSummary] = {}
    for classification in StorageClass:
        classified = [entry for entry in entries if entry.classification is classification]
        summaries[classification] = StorageSummary(
            directory_count=len(classified),
            file_count=sum(entry.file_count for entry in classified),
            bytes=sum(entry.bytes for entry in classified),
        )
    return summaries


def build_storage_report(config: Config, db: RunReader) -> StorageReport:
    """Inspect configured roots and exact output-path joins without mutating state."""
    # Keep configured roots lexical so _configured_child_directories can reject
    # a root symlink before resolving it to an arbitrary external directory.
    output_root = config.output.output_path
    cache_root = config.cache.cache_path
    runs_by_directory = _run_by_exact_output_directory(db)
    entries = _inspect_root("output", output_root, runs_by_directory)
    entries.extend(_inspect_root("cache", cache_root, runs_by_directory))

    ordered = tuple(sorted(entries, key=lambda entry: str(entry.path)))
    largest = tuple(sorted(entries, key=lambda entry: (-entry.bytes, str(entry.path)))[:10])
    return StorageReport(ordered, _summarize(entries), largest)
