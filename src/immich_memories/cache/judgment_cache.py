"""Answers to judgement-call prompts, keyed by exactly what was asked.

A judgement call is the expensive kind: reasoning mode costs 5-10x the latency
and 10-20x the completion tokens of a fast answer. The refinement loop asks the
same question repeatedly — a stabilize round that changes nothing re-presents
an identical selection, and a second run of the same memory presents it again.

Reusing the answer is the point rather than a compromise. A good judgement
about an identical set should not be re-rolled, and a sampled verdict that
changes between two identical rounds is noise, not a second opinion.

Its own database rather than a table in the analysis cache: that one carries a
SCHEMA_VERSION real users' stored analysis keys off, and bumping it for a
derived cache would invalidate everybody's work for an unrelated feature. This
file can be deleted at any time and costs only the calls it saved.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from pathlib import Path

from immich_memories.cache.sqlite_conn import ThreadOwnedConnections

logger = logging.getLogger(__name__)

# Bump to abandon every stored answer — for a change in how the answer is used
# that the prompt text itself does not capture.
_ANSWER_VERSION = "judge1"
# Bump when the shared visual gateway changes what identity material the
# provider actually sees. visual1 keyed annotations without sending them.

_SCHEMA = """
CREATE TABLE IF NOT EXISTS judgments (
    key TEXT PRIMARY KEY,
    answer TEXT NOT NULL,
    answered_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_TEXT_FAILURE_SCHEMA = """
CREATE TABLE IF NOT EXISTS text_completion_failures (
    key TEXT PRIMARY KEY,
    record TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def judgment_key(
    *, model: str | None, prompt: str, thinking: bool, thinking_identity: str = ""
) -> str:
    """Everything that could change the answer, and nothing that could not.

    The prompt text carries the clips, their descriptions and their order, so
    a changed selection keys differently without anyone maintaining a list of
    what to invalidate on. It also carries the prompt template, so editing the
    wording abandons the answers given to the old one — which is the behaviour
    you want and the one that is easiest to forget to implement.
    """
    parts = [_ANSWER_VERSION, model or "", "thinking" if thinking else "fast", prompt]
    if thinking_identity:
        parts.append(thinking_identity)
    material = "\x1f".join(parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def verdicts_beside(cache_dir: Path) -> Path:
    """Where reused answers live, given the configured cache directory."""
    return Path(cache_dir) / "judgments.db"


class JudgmentCache:
    """Remembers what the model said about an identical question.

    Every failure here is quiet and costs only calls: an unwritable directory,
    a locked database or a corrupt file must never take a generation down with
    it.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._connections = ThreadOwnedConnections(self.db_path, _SCHEMA)
        self._failure_connections = ThreadOwnedConnections(self.db_path, _TEXT_FAILURE_SCHEMA)

    def answer_for(self, key: str) -> str | None:
        """What the model said last time, or None if it has not been asked."""
        try:
            with self._connections.connection() as conn:
                row = conn.execute("SELECT answer FROM judgments WHERE key = ?", (key,)).fetchone()
        except (OSError, sqlite3.Error) as exc:
            logger.debug("Judgment cache unreadable (%s): asking again", exc)
            return None
        return str(row[0]) if row else None

    def remember(self, key: str, answer: str) -> None:
        """Keep an answer. Silence and failures are never stored."""
        if not answer:
            return
        try:
            with self._connections.connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO judgments (key, answer) VALUES (?, ?)", (key, answer)
                )
                conn.commit()
        except (OSError, sqlite3.Error) as exc:
            logger.debug("Judgment cache unwritable (%s): the answer is not kept", exc)

    def forget(self, key: str) -> None:
        """Drop an answer the asking contract refused, so the question is asked again."""
        try:
            with self._connections.connection() as conn:
                conn.execute("DELETE FROM judgments WHERE key = ?", (key,))
                conn.commit()
        except (OSError, sqlite3.Error) as exc:
            logger.debug("Judgment cache unwritable (%s): the answer is still kept", exc)

    def close(self) -> None:
        """Release the connections; a later question quietly reopens."""
        self._connections.close()
        self._failure_connections.close()

    def completion_failure_for(self, key: str) -> dict | None:
        """Replay a bounded output failure separately; it is never a usable answer."""
        try:
            with self._failure_connections.connection() as conn:
                row = conn.execute(
                    "SELECT record FROM text_completion_failures WHERE key = ?", (key,)
                ).fetchone()
            record = json.loads(row[0]) if row else None
            return record if isinstance(record, dict) else None
        except (OSError, sqlite3.Error, ValueError) as exc:
            logger.debug("Completion failure cache unreadable (%s)", exc)
            return None

    def remember_completion_failure(self, key: str, record: dict) -> None:
        """Only the text gateway supplies verified exhausted-completion records."""
        try:
            with self._failure_connections.connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO text_completion_failures (key, record) VALUES (?, ?)",
                    (key, json.dumps(record)),
                )
                conn.commit()
        except (OSError, sqlite3.Error) as exc:
            logger.debug("Completion failure was not kept (%s)", exc)
