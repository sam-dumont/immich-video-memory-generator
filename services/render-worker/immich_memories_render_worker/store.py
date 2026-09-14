"""Atomic job transitions behind a replaceable repository; no credentials enter this layer."""

import threading
import time
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from immich_memories_render_worker.models import JobStatus


class JobNotFound(LookupError):
    pass


class JobConflict(ValueError):
    pass


class QueueFull(RuntimeError):
    pass


class OutputConsumed(RuntimeError):
    pass


class JobRepository(Protocol):
    def admit(self, job_id: UUID, memory_key: str, fingerprint: str) -> tuple[JobStatus, bool]: ...
    def get(self, job_id: UUID) -> JobStatus: ...
    def update(self, job_id: UUID, **changes: object) -> JobStatus: ...
    def claim(self, job_id: UUID) -> JobStatus: ...
    def expire(self) -> tuple[UUID, ...]: ...


@dataclass
class _Record:
    status: JobStatus
    fingerprint: str
    expires_at: float


class MemoryJobRepository:
    """S1 is ephemeral: a restart loses statuses; PostgreSQL can implement the same transitions."""

    def __init__(self, max_jobs: int, retention_seconds: int):
        self._max_jobs = max_jobs
        self._retention = retention_seconds
        self._records: dict[UUID, _Record] = {}
        self._lock = threading.RLock()

    def admit(self, job_id: UUID, memory_key: str, fingerprint: str) -> tuple[JobStatus, bool]:
        with self._lock:
            if job_id in self._records:
                record = self._records[job_id]
                if record.fingerprint != fingerprint:
                    raise JobConflict("request_id already belongs to a different request")
                return record.status, False
            active = sum(
                r.status.state in {"queued", "running", "ready"} for r in self._records.values()
            )
            if active >= self._max_jobs or len(self._records) >= 128:
                raise QueueFull("Worker job capacity reached; retrieve outputs or retry later")
            status = JobStatus(job_id=job_id, memory_key=memory_key)
            self._records[job_id] = _Record(status, fingerprint, time.time() + self._retention)
            return status, True

    def get(self, job_id: UUID) -> JobStatus:
        with self._lock:
            if job_id not in self._records:
                raise JobNotFound("Job not found")
            return self._records[job_id].status

    def update(self, job_id: UUID, **changes: object) -> JobStatus:
        with self._lock:
            record = self._records[job_id]
            record.status = record.status.model_copy(update=changes)
            record.expires_at = time.time() + self._retention
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
            expired = tuple(
                key
                for key, row in self._records.items()
                if row.status.state in {"ready", "failed", "consumed"}
                and row.expires_at < time.time()
            )
            for key in expired:
                del self._records[key]
            return expired
