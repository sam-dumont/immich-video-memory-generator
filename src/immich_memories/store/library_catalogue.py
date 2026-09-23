"""Write the library's own account of a period into the annotation database.

The read side lives next door in `library_overviews.py` and never writes. This is the only
writer: cataloguing owns the table. An account is content-addressed by everything that could
change it, so the same evidence read by the same producer is never paid for twice.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from immich_memories.cache.sqlite_conn import ThreadOwnedConnections

_KINDS = frozenset({"month", "year", "span", "month-part", "year-part", "span-part"})
_SCHEMA = """
CREATE TABLE IF NOT EXISTS library_overviews (
    node_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    period TEXT NOT NULL,
    account TEXT NOT NULL,
    children TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class LibraryAccount:
    """A navigational account whose complete child index remains available."""

    key: str
    kind: str
    period: str
    account: str
    children: tuple[str, ...]


class CatalogueStore:
    """Bank neutral accounts independently of a film's brief or duration."""

    def __init__(self, db_path: Path) -> None:
        self._connections = ThreadOwnedConnections(db_path, _SCHEMA)

    def accounts_for(self, keys: Sequence[str]) -> dict[str, LibraryAccount]:
        """Read exact revisions in bounded SQL batches, including on older SQLite builds."""
        found: dict[str, LibraryAccount] = {}
        with self._connections.connection() as connection:
            for start in range(0, len(keys), 300):
                batch = keys[start : start + 300]
                placeholders = ",".join("?" for _ in batch)
                rows = connection.execute(
                    "SELECT node_key,kind,period,account,children FROM library_overviews "  # noqa: S608 -- bound keys only.
                    f"WHERE node_key IN ({placeholders})",
                    tuple(batch),
                )
                for key, kind, period, account, children in rows:
                    found[key] = LibraryAccount(
                        key, kind, period, account, tuple(json.loads(children))
                    )
        return found

    def fullest(self, kind: str, period: str) -> LibraryAccount | None:
        """The banked account of this period built over the most children, or None.

        The same choice the film's own read makes, so a window reuses the account a month or
        a year film would have read.
        """
        with self._connections.connection() as connection:
            rows = connection.execute(
                "SELECT node_key,kind,period,account,children FROM library_overviews "
                "WHERE kind = ? AND period = ?",
                (kind, period),
            ).fetchall()
        accounts = [
            LibraryAccount(key, found, when, account, tuple(json.loads(children)))
            for key, found, when, account, children in rows
            if str(account or "").strip()
        ]
        return max(
            accounts, key=lambda row: (len(row.children), len(row.account), row.key), default=None
        )

    def remember(self, accounts: Sequence[LibraryAccount]) -> None:
        """Keep the first validated account of each exact evidence revision."""
        if any(account.kind not in _KINDS for account in accounts):
            raise ValueError("only period overviews belong here; episodes use EpisodeReadingStore")
        with self._connections.connection() as connection:
            connection.executemany(
                "INSERT INTO library_overviews VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(node_key) DO NOTHING",
                [
                    (row.key, row.kind, row.period, row.account, json.dumps(row.children))
                    for row in accounts
                ],
            )
            connection.commit()

    def close(self) -> None:
        """Release the annotation database connections owned by this bank."""
        self._connections.close()
