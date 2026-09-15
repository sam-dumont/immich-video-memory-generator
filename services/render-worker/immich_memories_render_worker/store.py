"""Atomic job transitions behind a replaceable repository; no credentials enter this layer."""

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from immich_memories_render_worker.models import JobStatus


class JobNotFound(LookupError):
    pass


class JobExpired(LookupError):
    pass


class JobConflict(ValueError):
    pass


class QueueFull(RuntimeError):
    pass


class OutputConsumed(RuntimeError):
    pass


_LIVE = {"queued", "running", "ready"}
_TERMINAL = {"ready", "failed", "consumed"}


class JobRepository(Protocol):
    def admit(self, status: JobStatus, fingerprint: str) -> tuple[JobStatus, bool]: ...
    def get(self, job_id: UUID) -> JobStatus: ...
    def update(self, job_id: UUID, **changes: object) -> JobStatus: ...
    def claim(self, job_id: UUID) -> JobStatus: ...
    def expire(self) -> tuple[UUID, ...]: ...
    def overdue(self, deadline_seconds: int) -> tuple[UUID, ...]: ...


class JobJournal:
    """One JSON document per job, shaped like the row a render_jobs table wants.

    It exists so a restart can answer "that render died with the process"
    instead of the 404 an expired job and a never-submitted id also return.
    """

    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, job_id: UUID) -> Path:
        return self.directory / f"{job_id}.json"

    def record(self, status: JobStatus) -> None:
        path = self._path(status.job_id)
        scratch = path.with_suffix(".writing")
        scratch.write_text(status.model_dump_json(), encoding="utf-8")
        os.chmod(scratch, 0o600)
        os.replace(scratch, path)

    def read(self, job_id: UUID) -> JobStatus | None:
        try:
            raw = self._path(job_id).read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            return JobStatus.model_validate(json.loads(raw))
        except ValueError:
            return None


@dataclass
class _Record:
    status: JobStatus
    fingerprint: str
    expires_at: float


class MemoryJobRepository:
    """Live jobs in memory; the journal answers for jobs this process never saw."""

    def __init__(self, max_jobs: int, retention_seconds: int, journal: JobJournal | None = None):
        self._max_jobs = max_jobs
        self._retention = retention_seconds
        self._journal = journal
        self._records: dict[UUID, _Record] = {}
        self._lock = threading.RLock()

    def _write(self, status: JobStatus) -> None:
        if self._journal is not None:
            self._journal.record(status)

    def admit(self, status: JobStatus, fingerprint: str) -> tuple[JobStatus, bool]:
        with self._lock:
            record = self._records.get(status.job_id)
            if record is not None and record.status.state != "failed":
                if record.fingerprint != fingerprint:
                    raise JobConflict(
                        f"cut {record.status.plan_digest[:12]} is already running "
                        "with different job content"
                    )
                return record.status, False
            if record is None:
                self._reserve_capacity()
            self._records[status.job_id] = _Record(
                status, fingerprint, time.time() + self._retention
            )
            self._write(status)
            return status, True

    def _reserve_capacity(self) -> None:
        active = sum(r.status.state in _LIVE for r in self._records.values())
        if active >= self._max_jobs or len(self._records) >= 128:
            raise QueueFull("Worker job capacity reached; retrieve outputs or retry later")

    def get(self, job_id: UUID) -> JobStatus:
        with self._lock:
            record = self._records.get(job_id)
            if record is not None:
                return record.status
        return self._recall(job_id)

    def _recall(self, job_id: UUID) -> JobStatus:
        journaled = self._journal.read(job_id) if self._journal is not None else None
        if journaled is None:
            raise JobNotFound("Job not found")
        if journaled.state == "expired":
            raise JobExpired("Job result expired")
        if journaled.state == "failed":
            return journaled
        return journaled.model_copy(
            update={
                "state": "failed",
                "phase": "failed",
                "error": "The worker restarted before this render finished; submit it again",
            }
        )

    def update(self, job_id: UUID, **changes: object) -> JobStatus:
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                raise JobNotFound("Job not found")
            record.status = record.status.model_copy(update=changes)
            record.expires_at = time.time() + self._retention
            # WHY: progress ticks many times a second; only a transition is worth a write.
            if "state" in changes:
                self._write(record.status)
            return record.status

    def claim(self, job_id: UUID) -> JobStatus:
        with self._lock:
            status = self.get(job_id)
            if status.state == "consumed":
                raise OutputConsumed("Output was already claimed")
            if status.state != "ready":
                raise JobConflict("Output is not ready")
            return self.update(job_id, state="consumed")

    def expire(self) -> tuple[UUID, ...]:
        with self._lock:
            now = time.time()
            expired = tuple(
                key
                for key, row in self._records.items()
                if row.status.state in _TERMINAL and row.expires_at < now
            )
            for key in expired:
                self._write(self._records.pop(key).status.model_copy(update={"state": "expired"}))
            return expired

    def overdue(self, deadline_seconds: int) -> tuple[UUID, ...]:
        """Name every unfinished job past its deadline, measured from submission."""
        with self._lock:
            cutoff = time.time() - deadline_seconds
            return tuple(
                key
                for key, row in self._records.items()
                if row.status.state in {"queued", "running"}
                and (row.status.started_at or row.status.submitted_at).timestamp() < cutoff
            )
