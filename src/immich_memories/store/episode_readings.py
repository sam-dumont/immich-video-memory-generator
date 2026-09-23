"""Bank semantic episode readings by full membership and producer identity."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from immich_memories.cache.sqlite_conn import ThreadOwnedConnections

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS editorial_episode_readings (
    group_id TEXT NOT NULL,
    producer_key TEXT NOT NULL,
    evidence_key TEXT NOT NULL,
    full_asset_ids TEXT NOT NULL,
    what_happened TEXT NOT NULL,
    representatives TEXT NOT NULL,
    cull_decisions TEXT NOT NULL,
    notable_moments TEXT NOT NULL DEFAULT '[]',
    answered_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (group_id, producer_key, evidence_key)
)
"""


@dataclass(frozen=True)
class EpisodeReadingProducer:
    """Everything that can change the meaning produced for an episode."""

    model_id: str
    prompt_version: str
    schema_version: str
    annotation_renderer_version: str
    annotation_versions: tuple[str, ...]

    def __post_init__(self) -> None:
        scalar_parts = (
            self.model_id,
            self.prompt_version,
            self.schema_version,
            self.annotation_renderer_version,
        )
        if any(not part.strip() for part in scalar_parts):
            raise ValueError("episode reading producer fields cannot be blank")
        if any(not version.strip() for version in self.annotation_versions):
            raise ValueError("annotation producer versions cannot be blank")
        if len(self.annotation_versions) != len(set(self.annotation_versions)):
            raise ValueError("annotation producer versions must be unique")

    def key(self) -> str:
        """Return a path- and credential-free identity for this exact contract."""
        material = {
            "identity_version": "episode-reading-producer-v1",
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "annotation_renderer_version": self.annotation_renderer_version,
            "annotation_versions": sorted(self.annotation_versions),
        }
        encoded = json.dumps(material, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class EpisodeReadingIdentity:
    """One canonical episode under one exact reading contract."""

    group_id: str
    producer_key: str
    evidence_key: str

    def __post_init__(self) -> None:
        if (
            not self.group_id.strip()
            or not self.producer_key.strip()
            or not self.evidence_key.strip()
        ):
            raise ValueError("episode reading identity cannot be blank")

    @classmethod
    def from_annotations(
        cls,
        *,
        group_id: str,
        producer_key: str,
        annotation_lines: Mapping[str, str],
    ) -> EpisodeReadingIdentity:
        """Key the exact complete text evidence independently of mapping order."""
        if not annotation_lines or any(
            not asset_id.strip() or not line.strip() for asset_id, line in annotation_lines.items()
        ):
            raise ValueError("episode identity needs complete rendered annotation lines")
        encoded = json.dumps(
            sorted(annotation_lines.items()),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return cls(
            group_id=group_id,
            producer_key=producer_key,
            evidence_key=hashlib.sha256(encoded).hexdigest(),
        )


@dataclass(frozen=True)
class EpisodeRepresentative:
    """An episode member that carries one distinct part of its meaning.

    The same row shape carries a notable moment: which picture, and what it is a record of.
    """

    asset_id: str
    reason: str

    def __post_init__(self) -> None:
        if not self.asset_id.strip() or not self.reason.strip():
            raise ValueError("episode representative needs an asset and reason")


@dataclass(frozen=True)
class EpisodeCullDecision:
    """A typed Cull bucket attached to one member of the episode."""

    asset_id: str
    bucket: str

    def __post_init__(self) -> None:
        if not self.asset_id.strip() or not self.bucket.strip():
            raise ValueError("episode cull decision needs an asset and bucket")


@dataclass(frozen=True)
class BankedEpisodeReading:
    """Reusable meaning for one complete canonical episode."""

    identity: EpisodeReadingIdentity
    full_asset_ids: tuple[str, ...]
    what_happened: str
    representatives: tuple[EpisodeRepresentative, ...]
    cull_decisions: tuple[EpisodeCullDecision, ...]
    # What the reading says is worth a record of its own, and why. A reading that named
    # none is an episode nothing stood out in, not an unread one.
    notable_moments: tuple[EpisodeRepresentative, ...] = ()

    def __post_init__(self) -> None:
        members = set(self.full_asset_ids)
        if not members or len(members) != len(self.full_asset_ids):
            raise ValueError("episode reading needs unique full membership")
        if not self.what_happened.strip() or not self.representatives:
            raise ValueError("episode reading needs meaning and a representative")
        referenced = {
            *(representative.asset_id for representative in self.representatives),
            *(decision.asset_id for decision in self.cull_decisions),
            *(moment.asset_id for moment in self.notable_moments),
        }
        if not referenced.issubset(members):
            raise ValueError("episode reading may reference only full episode members")


class EpisodeReadingStore:
    """Persist immutable episode readings in the configured library database."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._connections = ThreadOwnedConnections(self.db_path, _SCHEMA)

    def remember(self, readings: Iterable[BankedEpisodeReading]) -> None:
        """Keep complete readings; an existing identity is never rerolled."""
        rows = tuple(_row_for(reading) for reading in readings)
        if not rows:
            return
        try:
            with self._connections.connection() as connection:
                _migrate_notable_moments(connection)
                connection.executemany(
                    "INSERT INTO editorial_episode_readings ("
                    "group_id, producer_key, evidence_key, full_asset_ids, what_happened, "
                    "representatives, cull_decisions, notable_moments"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(group_id, producer_key, evidence_key) DO NOTHING",
                    rows,
                )
                connection.commit()
        except (OSError, sqlite3.Error) as exc:
            logger.debug("Episode reading store unwritable (%s): reading not kept", exc)

    def readings_for(
        self,
        identities: Sequence[EpisodeReadingIdentity],
    ) -> dict[str, BankedEpisodeReading]:
        """Return readings matching current membership, producer, and evidence."""
        ordered = tuple(dict.fromkeys(identities))
        if not ordered:
            return {}
        placeholders = ",".join("(?, ?, ?)" for _ in ordered)
        parameters = tuple(
            part
            for identity in ordered
            for part in (identity.group_id, identity.producer_key, identity.evidence_key)
        )
        try:
            with self._connections.connection() as connection:
                _migrate_notable_moments(connection)
                query = (
                    "SELECT group_id, producer_key, evidence_key, full_asset_ids, what_happened, "  # noqa: S608 -- generated placeholders; bound values.
                    "representatives, cull_decisions, notable_moments "
                    "FROM editorial_episode_readings "
                    f"WHERE (group_id, producer_key, evidence_key) IN ({placeholders})"
                )
                rows = connection.execute(query, parameters).fetchall()
        except (OSError, sqlite3.Error) as exc:
            logger.debug("Episode reading store unreadable (%s): treating as cold", exc)
            return {}
        recalled: dict[str, BankedEpisodeReading] = {}
        for row in rows:
            try:
                reading = _reading_from(row)
            except (json.JSONDecodeError, TypeError, ValueError):
                logger.debug("Skipping invalid banked episode reading %s", row[0])
                continue
            recalled[reading.identity.group_id] = reading
        return recalled

    def close(self) -> None:
        """Release every thread-owned connection."""
        self._connections.close()


def _row_for(reading: BankedEpisodeReading) -> tuple[str, ...]:
    return (
        reading.identity.group_id,
        reading.identity.producer_key,
        reading.identity.evidence_key,
        json.dumps(reading.full_asset_ids, separators=(",", ":")),
        reading.what_happened,
        json.dumps(
            [
                {"asset_id": representative.asset_id, "reason": representative.reason}
                for representative in reading.representatives
            ],
            separators=(",", ":"),
        ),
        json.dumps(
            [
                {"asset_id": decision.asset_id, "bucket": decision.bucket}
                for decision in reading.cull_decisions
            ],
            separators=(",", ":"),
        ),
        json.dumps(
            [
                {"asset_id": moment.asset_id, "reason": moment.reason}
                for moment in reading.notable_moments
            ],
            separators=(",", ":"),
        ),
    )


def _reading_from(row: Sequence[object]) -> BankedEpisodeReading:
    representatives = json.loads(str(row[5]))
    cull_decisions = json.loads(str(row[6]))
    return BankedEpisodeReading(
        identity=EpisodeReadingIdentity(
            group_id=str(row[0]),
            producer_key=str(row[1]),
            evidence_key=str(row[2]),
        ),
        full_asset_ids=tuple(str(asset_id) for asset_id in json.loads(str(row[3]))),
        what_happened=str(row[4]),
        representatives=tuple(
            EpisodeRepresentative(asset_id=str(item["asset_id"]), reason=str(item["reason"]))
            for item in representatives
        ),
        cull_decisions=tuple(
            EpisodeCullDecision(asset_id=str(item["asset_id"]), bucket=str(item["bucket"]))
            for item in cull_decisions
        ),
        notable_moments=tuple(
            EpisodeRepresentative(asset_id=str(item["asset_id"]), reason=str(item["reason"]))
            for item in json.loads(str(row[7]))
        ),
    )


def _columns(connection: sqlite3.Connection) -> set[str]:
    return {row[1] for row in connection.execute("PRAGMA table_info(editorial_episode_readings)")}


def _migrate_notable_moments(connection: sqlite3.Connection) -> None:
    """A bank written before notable moments existed reads back with an explicitly empty lane."""
    if "notable_moments" in _columns(connection):
        return
    try:
        connection.execute(
            "ALTER TABLE editorial_episode_readings "
            "ADD COLUMN notable_moments TEXT NOT NULL DEFAULT '[]'"
        )
        connection.commit()
    except sqlite3.OperationalError:
        # Another thread's connection may have added it between the check and here.
        if "notable_moments" not in _columns(connection):
            raise
