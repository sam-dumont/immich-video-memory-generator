"""Per-episode provenance for the evidence an episode reading was keyed from.

An episode's evidence key hashes the rendered annotation lines of its members as one
blob, so two banked readings of the same group under the same producer differ only by
that one opaque digest. When a warm replay lands on a different reading there is
nothing left to compare. These artifacts keep the missing granularity: one hash per
asset line, public-safe, beside the exact lines in an owner-only sibling.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from immich_memories.security import write_secret_file

logger = logging.getLogger(__name__)

PROVENANCE_VERSION = "episode-evidence-provenance-v1"
EVIDENCE_HASHES_NAME = "evidence-hashes.json"
EVIDENCE_LINES_NAME = "evidence-lines.private.json"


def line_sha256(line: str) -> str:
    """Hash one rendered annotation line exactly as it reached the reading."""
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EpisodeEvidenceLines:
    """One episode's complete rendered evidence, in canonical membership order."""

    group_id: str
    evidence_key: str
    lines: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not self.group_id.strip() or not self.evidence_key.strip() or not self.lines:
            raise ValueError("episode evidence needs a group, an evidence key and lines")

    def hashes(self) -> dict[str, Any]:
        """The public-safe projection: ids and digests, never the line text."""
        return {
            "group_id": self.group_id,
            "evidence_key": self.evidence_key,
            "assets": [
                {"asset_id": asset_id, "line_sha256": line_sha256(line)}
                for asset_id, line in self.lines
            ],
        }

    def as_record(self) -> dict[str, Any]:
        """The owner-only projection: the same episode with its lines intact."""
        return {
            "group_id": self.group_id,
            "evidence_key": self.evidence_key,
            "lines": [[asset_id, line] for asset_id, line in self.lines],
        }


def _document(rows: list[dict[str, Any]]) -> str:
    return json.dumps(
        {"schema": PROVENANCE_VERSION, "episodes": rows}, ensure_ascii=False, indent=2
    )


class AttemptEvidenceProvenance:
    """Accumulate every episode read during one attempt and rewrite both artifacts.

    A plan may read episodes more than once; the later reading of a group replaces the
    earlier record so the artifact always states the evidence the attempt actually used.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._by_directory: dict[Path, dict[str, EpisodeEvidenceLines]] = {}

    def capture(self, episodes: Sequence[EpisodeEvidenceLines], *, directory: Path) -> None:
        """Record the evidence behind these episodes; never change the run's answer."""
        if not episodes:
            return
        try:
            path = Path(directory).resolve()
            with self._lock:
                kept = self._by_directory.setdefault(path, {})
                kept.update({episode.group_id: episode for episode in episodes})
                ordered = tuple(kept.values())
                path.mkdir(parents=True, exist_ok=True, mode=0o700)
                hashes = _document([episode.hashes() for episode in ordered])
                (path / EVIDENCE_HASHES_NAME).write_text(hashes)
                write_secret_file(
                    path / EVIDENCE_LINES_NAME,
                    _document([episode.as_record() for episode in ordered]),
                )
        except Exception as exc:  # Provenance is diagnostics; it cannot fail a cut.
            logger.warning("Could not record episode evidence provenance (%s)", type(exc).__name__)
