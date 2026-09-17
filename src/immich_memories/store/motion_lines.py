"""The motion line each video carries into the pick, keyed like a caption.

A row belongs to one picture, one producer and the exact source metadata it was read from.
A changed source is a different digest, so its old row is simply not an answer any more.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass

from immich_memories.store.editorial_preparation import now

DESCRIBED = "described"
UNAVAILABLE = "unavailable"

SCHEMA = """
CREATE TABLE IF NOT EXISTS motion_lines (
 asset_id TEXT NOT NULL, producer TEXT NOT NULL, source_digest TEXT NOT NULL,
 status TEXT NOT NULL, text TEXT NOT NULL, frames INTEGER NOT NULL, bytes_read INTEGER NOT NULL,
 written_at TEXT NOT NULL, PRIMARY KEY(asset_id, producer))
"""


@dataclass(frozen=True, slots=True)
class MotionLine:
    status: str
    text: str
    frames: int


def initialize_motion_lines(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)


def remember_motion_line(
    connection: sqlite3.Connection,
    *,
    asset_id: str,
    producer: str,
    source_digest: str,
    line: MotionLine,
    bytes_read: int,
) -> None:
    """Replace whatever this producer said about an older version of the same picture."""
    with connection:
        connection.execute(
            "INSERT OR REPLACE INTO motion_lines VALUES (?,?,?,?,?,?,?,?)",
            (
                asset_id,
                producer,
                source_digest,
                line.status,
                line.text,
                line.frames,
                bytes_read,
                now(),
            ),
        )


def settled_motion_lines(
    connection: sqlite3.Connection, digests: Mapping[str, str], producer: str
) -> dict[str, MotionLine]:
    """The rows that still answer for these pictures as they are now."""
    settled: dict[str, MotionLine] = {}
    ids = list(digests)
    for start in range(0, len(ids), 500):
        batch = ids[start : start + 500]
        rows = connection.execute(
            "SELECT asset_id, source_digest, status, text, frames FROM motion_lines "  # noqa: S608
            f"WHERE producer=? AND asset_id IN ({','.join('?' * len(batch))})",
            (producer, *batch),
        )
        for asset_id, digest, status, text, frames in rows:
            if digest == digests[asset_id] and status in {DESCRIBED, UNAVAILABLE}:
                settled[asset_id] = MotionLine(status, text, frames)
    return settled
