"""Typed values returned and persisted by smart automation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from immich_memories.automation.candidates import MemoryCandidate
from immich_memories.operations.phases import OperationalPhase


class AutoOutcome(StrEnum):
    """Lifecycle outcomes for one automation attempt."""

    RUNNING = "running"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"
    COMPLETED = "completed"
    FAILED = "failed"


class AutoAction(StrEnum):
    """Work selected for one automation invocation."""

    DELIVERY_RETRY = "delivery_retry"
    GENERATION = "generation"


@dataclass(frozen=True)
class AutoRejection:
    """One bounded, machine-readable hard-variety rejection."""

    category: str
    memory_key: str
    rule: str


@dataclass(frozen=True)
class AutoRunResult:
    """Terminal result reported by one smart automation invocation."""

    outcome: AutoOutcome
    reason: str
    action: AutoAction | None = None
    candidate: MemoryCandidate | None = None
    run_id: str | None = None
    output_path: Path | None = None
    error: str | None = None
    recent_categories: tuple[str, ...] = ()
    rejections: tuple[AutoRejection, ...] = ()


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
    last_phase: OperationalPhase | None = None
