"""Recover Step 4 after a reload while a generation was, or still is, running.

The generation coroutine outlives the browser page: NiceGUI deletes the client on
disconnect, UI writes to it are dropped, and a fresh Step 4 knows nothing about the
run. The session state keeps the run id; the run table keeps the truth (#322).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from immich_memories.tracking import RunDatabase
from immich_memories.ui.pages._step4_generate import _restore_completed_ui_state

if TYPE_CHECKING:
    from immich_memories.tracking.models import RunMetadata

RecoveredStatus = Literal["running", "completed", "failed", "cancelled", "stale"]

# WHY: a "running" row this old means the process died mid-run (there is no UI timeout);
# without a cutoff every Step 4 visit would poll it forever.
STALE_AFTER = timedelta(hours=3)


@dataclass(frozen=True)
class RecoveredRun:
    status: RecoveredStatus
    run: RunMetadata | None


def recover_active_run(state, db: RunDatabase | None = None) -> RecoveredRun | None:
    """Reconcile the session's active run with the run table; restore a finished artifact.

    Returns None when the session has no run to recover. A run id without a row yet is
    still "running" (the tracker writes its row a moment after the click). Terminal
    failures are reported once and then forgotten so they do not haunt every visit.
    """
    run_id = state.active_run_id
    if not run_id:
        return None
    database = db or RunDatabase(state.config.cache.database_path)
    run = database.get_run(run_id)
    if run is None:
        return RecoveredRun("running", None)
    if run.status == "running":
        if _age(run.created_at) > STALE_AFTER:
            state.active_run_id = None
            return RecoveredRun("stale", run)
        return RecoveredRun("running", run)
    if run.status == "completed":
        _restore_completed_ui_state(state, run)
        state.active_run_id = None
        return RecoveredRun("completed", run)
    state.active_run_id = None
    return RecoveredRun(run.status, run)


def _age(created_at: datetime) -> timedelta:
    started = created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
    return datetime.now(tz=UTC) - started
