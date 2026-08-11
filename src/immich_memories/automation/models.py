"""Typed values returned and persisted by smart automation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from immich_memories.automation.candidates import MemoryCandidate


class AutoOutcome(StrEnum):
    """Lifecycle outcomes for one automation attempt."""

    RUNNING = "running"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class AutoRunResult:
    """Terminal result reported by one smart automation invocation."""

    outcome: AutoOutcome
    reason: str
    candidate: MemoryCandidate | None = None
    run_id: str | None = None
    output_path: Path | None = None
    error: str | None = None


@dataclass(frozen=True)
class ProcessResult:
    """Bounded output captured from the generation process."""

    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class AutomationAttempt:
    """Durable state for one daily automation decision."""

    id: str
    started_at: datetime
    finished_at: datetime | None
    outcome: AutoOutcome
    reason: str
    candidate_category: str | None = None
    memory_type: str | None = None
    memory_key: str | None = None
    run_id: str | None = None
    error: str | None = None
