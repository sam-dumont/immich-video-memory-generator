"""v23: a look that failed is remembered by kind, keyed like any look (#699).

Only a successful look was ever written, so an asset that failed was
indistinguishable from one never seen and was re-asked on every run, forever —
observed as `24 hits, 20 misses` repeating byte-identically across a cold run
and a warm re-run of identical input.

Failures live beside the scores rather than in them: a verdict is not a score,
and a read for "what did the model say about these photos" must not have to
filter out the assets it said nothing about.

The key is `(asset_id, model_version)`, the same key a look uses — what a
prompt could not answer is a fact about that prompt, and a bump re-asks.
`attempts` is what makes the ledger a policy rather than a tombstone: the
caller decides how many tries a version is worth before it stops paying.
"""

from __future__ import annotations

import sqlite3


def migrate_look_failure_ledger(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS asset_look_failures (
            asset_id TEXT NOT NULL,
            model_version TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 1,
            last_attempt_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (asset_id, model_version)
        )
    """)
