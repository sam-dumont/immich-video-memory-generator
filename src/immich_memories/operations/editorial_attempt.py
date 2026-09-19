"""Durable, isolated selection attempts with a process-owned liveness lease."""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Mapping
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from immich_memories.analysis.llm_metrics import LLMCounters, collecting
from immich_memories.analysis.llm_usage_record import write_llm_usage
from immich_memories.operations.cancellation import PipelineCancelled
from immich_memories.operations.cut_progress import ANALYSIS_PHASE, StageClock, StageUpdate
from immich_memories.security import write_secret_file

_FIRST_STAGE = StageUpdate("Preparing editorial evidence", ANALYSIS_PHASE)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class EditorialAttempt:
    """Keep each run's artifacts separate while semantic caches remain reusable.

    The lease is released by the OS even after a crash. A reader can distinguish
    an interrupted run from a slow live run without guessing from its age or PID.
    """

    def __init__(self, root: Path, *, request: dict[str, Any]) -> None:
        self.root = Path(root)
        self.attempt_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:12]
        self.directory = self.root / "attempts" / self.attempt_id
        self.record: dict[str, Any] = {
            "schema": "editorial-attempt-v1",
            "attempt_id": self.attempt_id,
            "status": "running",
            "started_at": _now(),
            "stage": _FIRST_STAGE.stage_label,
            "progress": _FIRST_STAGE.as_record(),
            "request": request,
            "restart": "Run the same request; completed exact judgments remain reusable.",
        }
        self._lease: int | None = None
        self._stage_clock = StageClock()
        self._usage_scope = ExitStack()
        self._usage: LLMCounters | None = None

    def __enter__(self) -> EditorialAttempt:
        self.directory.mkdir(parents=True, mode=0o700)
        self._lease = os.open(self.directory / ".lease", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self._lease, fcntl.LOCK_EX)
            self._usage = self._usage_scope.enter_context(collecting())
            self._save()
            write_secret_file(
                self.root / "latest-attempt.private.json",
                json.dumps({"attempt_id": self.attempt_id, "directory": str(self.directory)}),
            )
        except BaseException:
            self._usage_scope.close()
            os.close(self._lease)
            self._lease = None
            raise
        return self

    def stage(self, update: StageUpdate | str) -> StageUpdate:
        """Record where the run is: the sentence for a row, the numbers for a bar."""
        if isinstance(update, str):
            update = StageUpdate(update)
        previous = StageUpdate.from_record(self.record["progress"])
        if previous and previous.identity == update.identity and previous.done == update.done:
            return previous  # The lease proves liveness; no disk heartbeat needed.
        update = self._stage_clock.measure(update)
        self.record["stage"] = update.stage_label
        self.record["progress"] = update.as_record()
        self._save()
        return update

    def complete(
        self,
        *,
        selected: int,
        outcome: str = "complete",
        duration_realization: dict | None = None,
        calls_by_stage: Mapping[str, Any] | None = None,
    ) -> None:
        self.record.update(status="complete", outcome=outcome, selected_carriers=selected)
        if duration_realization is not None:
            self.record["duration_realization"] = duration_realization
        if calls_by_stage is not None:
            self.record["calls_by_stage"] = dict(calls_by_stage)

    def _save(self) -> None:
        write_llm_usage(self.directory, self._usage)
        self.record["updated_at"] = _now()
        write_secret_file(
            self.directory / "status.private.json",
            json.dumps(self.record, ensure_ascii=False, indent=2),
        )

    def __exit__(self, _exc_type, exc, traceback) -> None:
        try:
            if exc is not None:
                self.record["status"] = (
                    "cancelled"
                    if isinstance(exc, (PipelineCancelled, KeyboardInterrupt))
                    else "failed"
                )
                self.record["error_type"] = type(exc).__name__
            elif self.record["status"] == "running":
                self.record["status"] = "incomplete"
            self.record["finished_at"] = _now()
            self._save()
        finally:
            self._usage_scope.close()
            if self._lease is not None:
                os.close(self._lease)
                self._lease = None


def read_editorial_attempt(directory: Path) -> dict[str, Any]:
    """Read truthful liveness without changing an attempt's historical record."""
    record = json.loads((directory / "status.private.json").read_text())
    if record.get("status") != "running":
        return record
    lease = os.open(directory / ".lease", os.O_RDONLY)
    try:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return record
        return {
            **record,
            "status": "interrupted",
            "reason": "Planning process no longer owns its lease",
        }
    finally:
        os.close(lease)


def window_origin_note(directory: Path) -> str:
    """How a run came by a window nobody typed, or nothing when the dates were asked for."""
    try:
        request = json.loads((directory / "status.private.json").read_text()).get("request")
    except (OSError, ValueError):
        return ""
    origin = request.get("window_origin") if isinstance(request, dict) else None
    return f"Window: nobody typed one — {origin}" if origin else ""
