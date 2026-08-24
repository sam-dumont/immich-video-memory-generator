"""Refused pipeline-run lifecycle transitions and the state that explains them."""

from __future__ import annotations

import sqlite3
from typing import NoReturn


class InvalidRunLifecycleError(RuntimeError):
    """Raised when a run's lifecycle state forbids a requested transition."""


class DuplicateRunError(RuntimeError):
    """Raised when a new tracker tries to claim an existing run identity."""


def raise_invalid_delivery_transition(
    conn: sqlite3.Connection,
    run_id: str,
) -> NoReturn:
    """Re-read the run to name which precondition the delivery transition missed."""
    row = conn.execute(
        "SELECT status, delivery_status FROM pipeline_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"Unknown pipeline run: {run_id}")
    if row["status"] == "completed":
        raise InvalidRunLifecycleError(
            f"Delivery transition requires requested delivery; run '{run_id}' is "
            f"'{row['delivery_status']}'"
        )
    raise InvalidRunLifecycleError(
        f"Delivery transition requires a completed run; run '{run_id}' is '{row['status']}'"
    )


def raise_invalid_artifact_transition(
    conn: sqlite3.Connection,
    run_id: str,
) -> NoReturn:
    """Re-read the run to name the status that blocked artifact completion."""
    row = conn.execute("SELECT status FROM pipeline_runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(f"Unknown pipeline run: {run_id}")
    raise InvalidRunLifecycleError(
        f"Artifact completion requires a running run; run '{run_id}' is '{row['status']}'"
    )
