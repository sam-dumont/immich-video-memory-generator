"""v21: record what a run spent on the LLM, on the run row itself.

Per-phase metrics already have a home in `phase_stats.extra_metrics`, and the
first design for this used only that. It does not work, for a reason that is
worth writing down: `RunTracker` records three phases -- clip_extraction,
assembly and music -- all inside `generate_memory`, while analysis and
selection run *before* the run row exists and spend nearly all of the model
budget. A run-level total is therefore not a sum over phases; it has to be
recorded in its own right.

One JSON column rather than six typed ones: the shape is still settling, and
`runs stats` does not yet aggregate across runs. When it does, the hot fields
hoist out of here without a redesign.
"""

from __future__ import annotations

import sqlite3


def migrate_run_llm_metrics(conn: sqlite3.Connection) -> None:
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pipeline_runs'"
    ).fetchone()
    if table_exists is None:
        return

    existing = {row[1] for row in conn.execute("PRAGMA table_info(pipeline_runs)")}
    if "llm_metrics" in existing:
        return

    conn.execute("ALTER TABLE pipeline_runs ADD COLUMN llm_metrics TEXT")
