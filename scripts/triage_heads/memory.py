"""Hard resident-memory limits for offline triage-head stages."""

from __future__ import annotations

import fcntl
import os
import resource
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from math import isfinite
from pathlib import Path
from typing import IO

DEFAULT_OFFLINE_WORKING_SET_GIB = 8.0
HARD_MAX_OFFLINE_WORKING_SET_GIB = 8.0
DEFAULT_PIPELINE_ROOT = Path.home() / ".immich-memories-matrix" / "triage-heads"


def ensure_private_directory(path: Path) -> Path:
    """Create or tighten one local artifact directory to owner-only access."""
    directory = Path(path)
    if directory.is_symlink():
        raise ValueError(f"private directory cannot be a symlink: {directory}")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not directory.is_dir():
        raise ValueError(f"private artifact path is not a directory: {directory}")
    directory.chmod(0o700)
    return directory


def _acquire_lock(path: Path, *, label: str) -> IO[str]:
    ensure_private_directory(path.parent)
    if path.is_symlink():
        raise ValueError(f"{label} lock cannot be a symlink")
    handle = path.open("a+")
    os.chmod(path, 0o600)
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError(f"another triage run holds the {label} lock") from None
    return handle


class PipelineLocks:
    """One closeable ownership token for every identity in a pipeline lane."""

    def __init__(self, handles: list[IO[str]]) -> None:
        self._handles = handles

    def close(self) -> None:
        while self._handles:
            self._handles.pop().close()


def acquire_pipeline_lock(
    directory: Path,
    *,
    cache_db: Path | None = None,
    shared_root: Path | None = None,
) -> PipelineLocks:
    """Own artifact, cache, and optional process-wide pipeline identities."""
    identities: dict[Path, str] = {
        Path(directory).resolve() / ".pipeline.lock": "pipeline",
    }
    if cache_db is not None:
        cache_path = Path(cache_db).resolve()
        identities[cache_path.with_name(cache_path.name + ".pipeline.lock")] = "cache pipeline"
    if shared_root is not None:
        identities[Path(shared_root).resolve() / ".pipeline.lock"] = "shared pipeline"
    handles: list[IO[str]] = []
    try:
        for path, label in sorted(identities.items(), key=lambda item: str(item[0])):
            handles.append(_acquire_lock(path, label=label))
    except BaseException:
        while handles:
            handles.pop().close()
        raise
    return PipelineLocks(handles)


def acquire_recovery_pipeline_lock(*, cache_db: Path | None = None) -> PipelineLocks:
    """Serialize every memory-bounded recovery stage in the production lane."""
    return acquire_pipeline_lock(
        DEFAULT_PIPELINE_ROOT,
        cache_db=cache_db,
        shared_root=DEFAULT_PIPELINE_ROOT,
    )


def acquire_wal_lock(wal_path: Path) -> IO[str]:
    """Own repair and append access for one label WAL, including custom paths."""
    path = Path(wal_path)
    return _acquire_lock(path.with_name(path.name + ".lock"), label="WAL")


@contextmanager
def hold_label_run_locks(
    artifact_dir: Path,
    wal_path: Path,
    *,
    cache_db: Path | None = None,
    shared_root: Path | None = None,
) -> Iterator[None]:
    """Serialize one complete label run against the pipeline and its WAL."""
    pipeline = acquire_pipeline_lock(
        artifact_dir,
        cache_db=cache_db,
        shared_root=shared_root,
    )
    try:
        wal = acquire_wal_lock(wal_path)
        try:
            yield
        finally:
            wal.close()
    finally:
        pipeline.close()


def validate_offline_working_set_gib(value: float) -> float:
    """Accept only a finite, positive budget no larger than the hard 8 GiB cap."""
    resolved = float(value)
    if not isfinite(resolved) or resolved <= 0 or resolved > HARD_MAX_OFFLINE_WORKING_SET_GIB:
        raise ValueError("offline working set must be finite, positive, and at most 8 GiB")
    return resolved


def peak_process_rss_bytes() -> int:
    """Return this process's high-water RSS in bytes on macOS and Unix-like hosts."""
    rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # Darwin reports bytes; Linux and the BSDs exposed by our CI report KiB.
    return rss if sys.platform == "darwin" else rss * 1024


def ensure_offline_process_memory(
    *,
    max_working_set_gib: float = DEFAULT_OFFLINE_WORKING_SET_GIB,
    observed_rss_bytes: int | None = None,
) -> int:
    """Fail closed once the offline process reaches its configured RSS ceiling."""
    limit_gib = validate_offline_working_set_gib(max_working_set_gib)
    observed = peak_process_rss_bytes() if observed_rss_bytes is None else int(observed_rss_bytes)
    if observed < 0:
        raise ValueError("observed RSS cannot be negative")
    if observed >= int(limit_gib * 1024**3):
        raise MemoryError(
            "STOP: offline triage process reached the memory ceiling "
            f"({observed / 1024**3:.2f} GiB >= {limit_gib:.2f} GiB)"
        )
    return observed
