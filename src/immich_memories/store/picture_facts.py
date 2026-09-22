"""The typed facts one ingest read banks about a picture, keyed like a motion line.

A row belongs to one picture, one producer and the exact source metadata it was read from, so
an edited picture is a different digest and its old row simply stops being an answer. The
producer string carries the question set's hash, so a reworded question is a different producer
and invalidates its rows the same way. Rows hold the reader's raw numbers, never a verdict.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from immich_memories.store.editorial_preparation import now

DESCRIBED = "described"
REFUSED = "refused"
UNAVAILABLE = "unavailable"
_SETTLED = frozenset({DESCRIBED, REFUSED, UNAVAILABLE})

SCHEMA = """
CREATE TABLE IF NOT EXISTS picture_facts (
 asset_id TEXT NOT NULL, producer TEXT NOT NULL, source_digest TEXT NOT NULL,
 status TEXT NOT NULL, probabilities TEXT NOT NULL, answered_at TEXT NOT NULL,
 PRIMARY KEY(asset_id, producer))
"""


@dataclass(frozen=True, slots=True)
class PictureFacts:
    """One picture's raw answers: a probability per noul, a label and its spread per choice."""

    status: str
    answers: Mapping[str, Any]

    def noul(self, name: str) -> float | None:
        value = self.answers.get(name)
        return float(value) if isinstance(value, int | float) else None

    def choice(self, name: str) -> str:
        value = self.answers.get(name)
        label = value.get("choice") if isinstance(value, Mapping) else None
        return label if isinstance(label, str) else ""


def initialize_picture_facts(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)


def remember_picture_facts(
    connection: sqlite3.Connection,
    *,
    asset_id: str,
    producer: str,
    source_digest: str,
    facts: PictureFacts,
) -> None:
    """Replace whatever this producer said about an older version of the same picture."""
    with connection:
        connection.execute(
            "INSERT OR REPLACE INTO picture_facts VALUES (?,?,?,?,?,?)",
            (
                asset_id,
                producer,
                source_digest,
                facts.status,
                json.dumps(facts.answers, sort_keys=True, separators=(",", ":")),
                now(),
            ),
        )


def banked_picture_facts(
    connection: sqlite3.Connection, asset_ids: Sequence[str], producer: str
) -> dict[str, tuple[str, PictureFacts]]:
    """Every settled row this producer holds, with the source digest each one answers for."""
    banked: dict[str, tuple[str, PictureFacts]] = {}
    for start in range(0, len(asset_ids), 500):
        batch = asset_ids[start : start + 500]
        rows = connection.execute(
            "SELECT asset_id, source_digest, status, probabilities FROM picture_facts "  # noqa: S608
            f"WHERE producer=? AND asset_id IN ({','.join('?' * len(batch))})",
            (producer, *batch),
        )
        for asset_id, digest, status, probabilities in rows:
            if status in _SETTLED:
                banked[asset_id] = (str(digest), PictureFacts(status, _answers(probabilities)))
    return banked


def settled_picture_facts(
    connection: sqlite3.Connection, digests: Mapping[str, str], producer: str
) -> dict[str, PictureFacts]:
    """The rows that still answer for these pictures as they are now."""
    banked = banked_picture_facts(connection, list(digests), producer)
    return {
        asset_id: facts
        for asset_id, (digest, facts) in banked.items()
        if digest == digests[asset_id]
    }


def _answers(raw: object) -> dict[str, Any]:
    try:
        answers = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return answers if isinstance(answers, dict) else {}
