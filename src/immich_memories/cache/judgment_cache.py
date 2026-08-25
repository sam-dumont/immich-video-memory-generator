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
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Bump to abandon every stored answer — for a change in how the answer is used
# that the prompt text itself does not capture.
_ANSWER_VERSION = "judge1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS judgments (
    key TEXT PRIMARY KEY,
    answer TEXT NOT NULL,
    answered_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_VISUAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS visual_judgments (
    key TEXT PRIMARY KEY,
    answer TEXT NOT NULL,
    original_provenance TEXT NOT NULL,
    answered_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


@dataclass(frozen=True)
class VisualJudgmentIdentity:
    """All evidence and request settings that could change a visual answer."""

    page_bytes: tuple[bytes, ...]
    ordered_input_ids: tuple[str, ...]
    ordered_group_ids: tuple[str, ...]
    annotations: tuple[str, ...]
    model: str | None
    thinking: bool
    image_detail: str
    pass_name: str
    prompt_version: str
    schema_version: str
    render_version: str
    layout_versions: tuple[str, ...]
    upstream_material: tuple[str, ...]
    request_limits: tuple[str, ...]
    continuation_identity: tuple[int, int]
    endpoint: str = ""

    def key(self) -> str:
        """Return the deterministic key without including credentials or paths."""
        material = {
            "version": "visual1",
            "page_hashes": [hashlib.sha256(page).hexdigest() for page in self.page_bytes],
            "ordered_input_ids": self.ordered_input_ids,
            "ordered_group_ids": self.ordered_group_ids,
            "annotations": self.annotations,
            "model": self.model or "",
            "thinking": self.thinking,
            "image_detail": self.image_detail,
            "pass_name": self.pass_name,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "render_version": self.render_version,
            "layout_versions": self.layout_versions,
            "upstream_material": self.upstream_material,
            "request_limits": self.request_limits,
            "continuation_identity": self.continuation_identity,
            "endpoint": self.endpoint,
        }
        encoded = json.dumps(material, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def judgment_key(*, model: str | None, prompt: str, thinking: bool) -> str:
    """Everything that could change the answer, and nothing that could not.

    The prompt text carries the clips, their descriptions and their order, so
    a changed selection keys differently without anyone maintaining a list of
    what to invalidate on. It also carries the prompt template, so editing the
    wording abandons the answers given to the old one — which is the behaviour
    you want and the one that is easiest to forget to implement.
    """
    material = "\x1f".join(
        [_ANSWER_VERSION, model or "", "thinking" if thinking else "fast", prompt]
    )
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

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute(_SCHEMA)
        return conn

    def answer_for(self, key: str) -> str | None:
        """What the model said last time, or None if it has not been asked."""
        try:
            conn = self._connect()
        except (OSError, sqlite3.Error) as exc:
            logger.debug("Judgment cache unreadable (%s): asking again", exc)
            return None
        try:
            row = conn.execute("SELECT answer FROM judgments WHERE key = ?", (key,)).fetchone()
        except sqlite3.Error as exc:
            logger.debug("Judgment cache unreadable (%s): asking again", exc)
            return None
        finally:
            conn.close()
        return str(row[0]) if row else None

    def remember(self, key: str, answer: str) -> None:
        """Keep an answer. Silence and failures are never stored."""
        if not answer:
            return
        try:
            conn = self._connect()
        except (OSError, sqlite3.Error) as exc:
            logger.debug("Judgment cache unwritable (%s): the answer is not kept", exc)
            return
        try:
            conn.execute(
                "INSERT OR REPLACE INTO judgments (key, answer) VALUES (?, ?)", (key, answer)
            )
            conn.commit()
        except sqlite3.Error as exc:
            logger.debug("Judgment cache unwritable (%s): the answer is not kept", exc)
        finally:
            conn.close()


class VisualJudgmentCache:
    """A visual-answer cache independent of the legacy prompt-only table."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute(_VISUAL_SCHEMA)
        return conn

    def answer_for(self, key: str) -> tuple[str, str] | None:
        """Return a banked answer and its original provenance, if available."""
        try:
            conn = self._connect()
        except (OSError, sqlite3.Error) as exc:
            logger.debug("Visual judgment cache unreadable (%s): asking again", exc)
            return None
        try:
            row = conn.execute(
                "SELECT answer, original_provenance FROM visual_judgments WHERE key = ?", (key,)
            ).fetchone()
        except sqlite3.Error as exc:
            logger.debug("Visual judgment cache unreadable (%s): asking again", exc)
            return None
        finally:
            conn.close()
        return (str(row[0]), str(row[1])) if row else None

    def remember(self, key: str, answer: str, original_provenance: str) -> None:
        """Bank a complete visual answer with the decision that first produced it."""
        if not answer:
            return
        try:
            conn = self._connect()
        except (OSError, sqlite3.Error) as exc:
            logger.debug("Visual judgment cache unwritable (%s): answer not kept", exc)
            return
        try:
            conn.execute(
                "INSERT OR REPLACE INTO visual_judgments (key, answer, original_provenance) "
                "VALUES (?, ?, ?)",
                (key, answer, original_provenance),
            )
            conn.commit()
        except sqlite3.Error as exc:
            logger.debug("Visual judgment cache unwritable (%s): answer not kept", exc)
        finally:
            conn.close()
