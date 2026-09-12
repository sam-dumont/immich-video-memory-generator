"""Bank text-only period meaning against exact ordered episode grounding."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from immich_memories.cache.sqlite_conn import ThreadOwnedConnections

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS editorial_period_insights (
    producer_key TEXT NOT NULL,
    evidence_key TEXT NOT NULL,
    episode_grounding TEXT NOT NULL,
    thesis TEXT NOT NULL,
    evidence TEXT NOT NULL,
    tensions TEXT NOT NULL,
    recurring_threads TEXT NOT NULL,
    answered_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (producer_key, evidence_key)
)
"""


@dataclass(frozen=True)
class PeriodInsightProducer:
    """Everything that can change a period-level semantic reading."""

    model_id: str
    prompt_version: str
    schema_version: str

    def __post_init__(self) -> None:
        if any(
            not value.strip() for value in (self.model_id, self.prompt_version, self.schema_version)
        ):
            raise ValueError("period insight producer fields cannot be blank")

    def key(self) -> str:
        """Return a credential- and path-free identity for this exact producer."""
        material = {
            "identity_version": "period-insight-producer-v1",
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
        }
        encoded = json.dumps(material, separators=(",", ":"), sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PeriodEpisodeGrounding:
    """One readable canonical episode supplied to the period editor."""

    episode_id: str
    evidence_key: str
    rendered_line: str
    representative_asset_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if any(
            not value.strip() for value in (self.episode_id, self.evidence_key, self.rendered_line)
        ):
            raise ValueError("period episode grounding fields cannot be blank")
        if (
            not self.representative_asset_ids
            or len(self.representative_asset_ids) != len(set(self.representative_asset_ids))
            or any(not asset_id.strip() for asset_id in self.representative_asset_ids)
        ):
            raise ValueError("period episode grounding needs unique representatives")


@dataclass(frozen=True)
class PeriodInsightIdentity:
    """One ordered set of episode facts under one exact period producer."""

    producer_key: str
    evidence_key: str

    def __post_init__(self) -> None:
        if not self.producer_key.strip() or not self.evidence_key.strip():
            raise ValueError("period insight identity cannot be blank")

    @classmethod
    def from_grounding(
        cls,
        *,
        producer_key: str,
        episodes: Sequence[PeriodEpisodeGrounding],
    ) -> PeriodInsightIdentity:
        """Hash the exact ordered episode evidence; request scope is deliberately absent."""
        ordered = tuple(episodes)
        episode_ids = tuple(episode.episode_id for episode in ordered)
        if not ordered or len(episode_ids) != len(set(episode_ids)):
            raise ValueError("period insight identity needs unique ordered episodes")
        encoded = json.dumps(
            [_grounding_dict(episode) for episode in ordered],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return cls(
            producer_key=producer_key,
            evidence_key=hashlib.sha256(encoded).hexdigest(),
        )


@dataclass(frozen=True)
class BankedInsightEvidence:
    """One period observation with canonical episode and asset support."""

    observation: str
    episode_ids: tuple[str, ...]
    asset_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.observation.strip():
            raise ValueError("period evidence observation cannot be blank")
        if not _unique_nonblank(self.episode_ids):
            raise ValueError("period evidence needs unique episode IDs")
        if not _unique_nonblank(self.asset_ids):
            raise ValueError("period evidence needs unique asset IDs")


@dataclass(frozen=True)
class BankedPeriodInsight:
    """Reusable period meaning whose evidence can be checked without a model."""

    identity: PeriodInsightIdentity
    episode_grounding: tuple[PeriodEpisodeGrounding, ...]
    thesis: str
    evidence: tuple[BankedInsightEvidence, ...]
    tensions: tuple[str, ...]
    recurring_threads: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.thesis.strip() or not self.evidence:
            raise ValueError("banked period insight needs a thesis and evidence")
        if any(not item.strip() for item in (*self.tensions, *self.recurring_threads)):
            raise ValueError("period insight lists cannot contain blank values")
        expected_identity = PeriodInsightIdentity.from_grounding(
            producer_key=self.identity.producer_key,
            episodes=self.episode_grounding,
        )
        if self.identity != expected_identity:
            raise ValueError("period insight identity does not match its grounding")
        episode_ids = {episode.episode_id for episode in self.episode_grounding}
        representatives_by_episode = {
            episode.episode_id: frozenset(episode.representative_asset_ids)
            for episode in self.episode_grounding
        }
        if any(not set(item.episode_ids).issubset(episode_ids) for item in self.evidence):
            raise ValueError("period evidence must reference its episode grounding")
        for item in self.evidence:
            cited_asset_ids = {
                asset_id
                for episode_id in item.episode_ids
                for asset_id in representatives_by_episode[episode_id]
            }
            if not set(item.asset_ids).issubset(cited_asset_ids):
                raise ValueError("period evidence asset must belong to a cited episode")


class PeriodInsightStore:
    """Persist immutable text-only period readings in the library database."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._connections = ThreadOwnedConnections(self.db_path, _SCHEMA)

    def remember(self, insight: BankedPeriodInsight) -> None:
        """Keep a complete reading; an existing exact identity is never rerolled."""
        try:
            with self._connections.connection() as connection:
                connection.execute(
                    "INSERT INTO editorial_period_insights ("
                    "producer_key, evidence_key, episode_grounding, thesis, evidence, "
                    "tensions, recurring_threads"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(producer_key, evidence_key) DO NOTHING",
                    _row_for(insight),
                )
                connection.commit()
        except (OSError, sqlite3.Error) as exc:
            logger.debug("Period insight store unwritable (%s): insight not kept", exc)

    def insight_for(self, identity: PeriodInsightIdentity) -> BankedPeriodInsight | None:
        """Return only a reading with the current producer and exact grounding."""
        try:
            with self._connections.connection() as connection:
                row = connection.execute(
                    "SELECT producer_key, evidence_key, episode_grounding, thesis, evidence, "
                    "tensions, recurring_threads FROM editorial_period_insights "
                    "WHERE producer_key = ? AND evidence_key = ?",
                    (identity.producer_key, identity.evidence_key),
                ).fetchone()
        except (OSError, sqlite3.Error) as exc:
            logger.debug("Period insight store unreadable (%s): treating as cold", exc)
            return None
        if row is None:
            return None
        try:
            insight = _insight_from(row)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.debug("Skipping invalid banked period insight")
            return None
        return insight if insight.identity == identity else None

    def close(self) -> None:
        """Release every thread-owned connection."""
        self._connections.close()


def _unique_nonblank(values: tuple[str, ...]) -> bool:
    return (
        bool(values) and len(values) == len(set(values)) and all(value.strip() for value in values)
    )


def _grounding_dict(episode: PeriodEpisodeGrounding) -> dict[str, object]:
    return {
        "episode_id": episode.episode_id,
        "evidence_key": episode.evidence_key,
        "rendered_line": episode.rendered_line,
        "representative_asset_ids": list(episode.representative_asset_ids),
    }


def _row_for(insight: BankedPeriodInsight) -> tuple[str, ...]:
    return (
        insight.identity.producer_key,
        insight.identity.evidence_key,
        json.dumps(
            [_grounding_dict(episode) for episode in insight.episode_grounding],
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        insight.thesis,
        json.dumps(
            [
                {
                    "observation": item.observation,
                    "episode_ids": item.episode_ids,
                    "asset_ids": item.asset_ids,
                }
                for item in insight.evidence
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        json.dumps(insight.tensions, ensure_ascii=False, separators=(",", ":")),
        json.dumps(insight.recurring_threads, ensure_ascii=False, separators=(",", ":")),
    )


def _insight_from(row: Sequence[object]) -> BankedPeriodInsight:
    grounding_json = json.loads(str(row[2]))
    evidence_json = json.loads(str(row[4]))
    return BankedPeriodInsight(
        identity=PeriodInsightIdentity(producer_key=str(row[0]), evidence_key=str(row[1])),
        episode_grounding=tuple(
            PeriodEpisodeGrounding(
                episode_id=str(item["episode_id"]),
                evidence_key=str(item["evidence_key"]),
                rendered_line=str(item["rendered_line"]),
                representative_asset_ids=tuple(
                    str(asset_id) for asset_id in item["representative_asset_ids"]
                ),
            )
            for item in grounding_json
        ),
        thesis=str(row[3]),
        evidence=tuple(
            BankedInsightEvidence(
                observation=str(item["observation"]),
                episode_ids=tuple(str(episode_id) for episode_id in item["episode_ids"]),
                asset_ids=tuple(str(asset_id) for asset_id in item["asset_ids"]),
            )
            for item in evidence_json
        ),
        tensions=tuple(str(item) for item in json.loads(str(row[5]))),
        recurring_threads=tuple(str(item) for item in json.loads(str(row[6]))),
    )
