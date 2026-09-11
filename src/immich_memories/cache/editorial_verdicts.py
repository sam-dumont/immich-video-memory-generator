"""What a picture IS, remembered across every memory it could appear in.

Cull answers two durable questions and one contextual one. Whether a frame is a
photographed screen or a document, and whether it came out at all, are facts
about the picture: true in a month, a year, a person's spotlight, a trip. That
a frame is "one of several alike" is not — it is a fact about what it happened
to sit beside, and it is deliberately not stored here.

So a year stops paying to re-decide the same fifteen thousand pictures twelve
times, and a Person or Trip memory inherits the judgement rather than needing
Cull to be gentler on a corpus that was already filtered.

The cost of being wrong rises with the reuse: a bad cull used to spoil one
video and now follows the picture everywhere. That is why the trace says a
visual was culled on a remembered verdict rather than quietly omitting it, why
a star still outranks anything stored here, and why this file can be deleted at
any time — it costs only the calls it saved.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS editorial_verdicts (
    asset_id TEXT NOT NULL,
    pass_version TEXT NOT NULL,
    bucket TEXT NOT NULL,
    decided_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (asset_id, pass_version)
)
"""


class EditorialVerdicts:
    """Durable per-asset Cull verdicts, scoped to what the buckets meant."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._open() as connection:
            connection.execute(_SCHEMA)

    def remember(self, verdicts: Iterable[tuple[str, str]], *, pass_version: str) -> None:
        """Store one standing verdict per asset; the most recent look wins.

        Takes plain (asset, bucket) pairs so the cache layer stays ignorant of
        the editorial contracts, and so the caller decides which buckets are
        durable enough to remember.
        """
        rows = [(asset_id, pass_version, bucket) for asset_id, bucket in verdicts]
        if not rows:
            return
        with self._open() as connection:
            connection.executemany(
                "INSERT INTO editorial_verdicts (asset_id, pass_version, bucket) "
                "VALUES (?, ?, ?) ON CONFLICT(asset_id, pass_version) DO UPDATE SET "
                "bucket = excluded.bucket, decided_at = datetime('now')",
                rows,
            )

    def recall(self, asset_ids: Sequence[str], *, pass_version: str) -> dict[str, str]:
        """The standing verdicts for these assets under this definition."""
        if not asset_ids:
            return {}
        # Only the number of placeholders is interpolated; every value is bound.
        placeholders = ",".join("?" for _ in asset_ids)
        with self._open() as connection:
            rows = connection.execute(
                "SELECT asset_id, bucket FROM editorial_verdicts "  # noqa: S608
                f"WHERE pass_version = ? AND asset_id IN ({placeholders})",
                (pass_version, *asset_ids),
            ).fetchall()
        return dict(rows)

    def _open(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)
