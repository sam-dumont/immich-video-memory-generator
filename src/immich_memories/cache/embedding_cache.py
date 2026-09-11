"""Head facts per asset, in their own SQLite file beside the analysis cache.

Kept out of the migration chain on purpose: a fact is (asset, head, version) →
label, cheap to recompute, and the store is the seam the knowledge-store move
to Postgres replaces wholesale.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from immich_memories.cache.sqlite_conn import ThreadOwnedConnections
from immich_memories.triage.heads import HeadFact

_SCHEMA = """
CREATE TABLE IF NOT EXISTS head_facts (
    asset_id TEXT NOT NULL,
    head TEXT NOT NULL,
    version TEXT NOT NULL,
    label TEXT NOT NULL,
    confidence REAL NOT NULL,
    encoder_key TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    PRIMARY KEY (asset_id, head, version)
)
"""


class HeadFactStore:
    def __init__(self, db_path: Path) -> None:
        self._connections = ThreadOwnedConnections(Path(db_path), _SCHEMA)

    def remember_facts(self, asset_id: str, facts: Sequence[HeadFact], *, encoder_key: str) -> None:
        decided_at = datetime.now(UTC).isoformat(timespec="seconds")
        with self._connections.connection() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO head_facts"
                " (asset_id, head, version, label, confidence, encoder_key, decided_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (asset_id, f.head, f.version, f.label, f.confidence, encoder_key, decided_at)
                    for f in facts
                ],
            )
            conn.commit()

    def facts_for(
        self, asset_ids: Sequence[str], *, head: str, version: str
    ) -> dict[str, HeadFact]:
        if not asset_ids:
            return {}
        placeholders = ",".join("?" * len(asset_ids))
        with self._connections.connection() as conn:
            rows = conn.execute(
                "SELECT asset_id, label, confidence FROM head_facts"  # noqa: S608
                f" WHERE head = ? AND version = ? AND asset_id IN ({placeholders})",
                [head, version, *asset_ids],
            ).fetchall()
        return {
            row[0]: HeadFact(head=head, label=row[1], confidence=float(row[2]), version=version)
            for row in rows
        }

    def close(self) -> None:
        self._connections.close()
