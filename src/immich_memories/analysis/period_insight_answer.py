"""Strict answers for hierarchical visual insight."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, TypeGuard

from immich_memories.analysis.editorial_contracts import InsightEvidence
from immich_memories.analysis.strict_json import (
    bounded_model_text,
    final_json_object,
    is_safe_model_text,
)

EpisodeObservationKey = tuple[int, int]
EpisodeObservationValue = tuple[str, str]
EpisodeTileKey = tuple[int, int, int]
PeriodTileKey = tuple[str, int]
PeriodTileValue = tuple[str, str]
EPISODE_SCAN_SCHEMA_VERSION = "episode-scan-v4"
PERIOD_INSIGHT_SCHEMA_VERSION = "period-insight-v1"
# Declining is the decision; explaining the decline is prose. A model that
# sets thesis to null and says nothing beside it has still answered.
THESIS_DECLINED_WITHOUT_REASON = "no thesis offered, and no reason stated"
EPISODE_VISUAL_SUMMARY_MAX_CHARS = 64
EPISODE_REPRESENTATIVE_REASON_MAX_CHARS = 96


@dataclass(frozen=True)
class EpisodePageReading:
    """One complete page observation resolved back to stable asset IDs."""

    episode_id: str
    page_id: str
    visual_summary: str
    representative_asset_ids: tuple[str, ...]
    representative_reason: str

    def __post_init__(self) -> None:
        if not is_safe_model_text(
            self.visual_summary,
            max_chars=EPISODE_VISUAL_SUMMARY_MAX_CHARS,
        ) or not is_safe_model_text(
            self.representative_reason,
            max_chars=EPISODE_REPRESENTATIVE_REASON_MAX_CHARS,
        ):
            raise ValueError("episode page reading needs bounded safe single-line text")


@dataclass(frozen=True)
class PeriodInsightAnswer:
    """A complete provisional reading before request provenance is attached."""

    thesis: str | None
    evidence: tuple[InsightEvidence, ...]
    tensions: tuple[str, ...]
    recurring_threads: tuple[str, ...]
    unavailable_reason: str | None

    def __post_init__(self) -> None:
        if (self.thesis is None) == (self.unavailable_reason is None):
            raise ValueError("period insight answer needs exactly one outcome")
        if self.thesis is not None and (not self.thesis.strip() or not self.evidence):
            raise ValueError("period insight answer thesis needs visual evidence")
        if self.unavailable_reason is not None and not self.unavailable_reason.strip():
            raise ValueError("period insight answer unavailable reason cannot be blank")
        if any(not item.episode_ids or not item.asset_ids for item in self.evidence):
            raise ValueError("period insight answer evidence must identify episodes and assets")


@dataclass(frozen=True)
class EpisodeScanReadings:
    """Valid episode namespaces and the required observations that did not parse."""

    readings: tuple[EpisodePageReading, ...]
    invalid_observations: tuple[tuple[str, str], ...]


def read_episode_answer(
    raw: str,
    *,
    episode_alias: int,
    page_alias: int,
    observation_map: Mapping[EpisodeObservationKey, EpisodeObservationValue],
    tile_map: Mapping[EpisodeTileKey, str],
) -> EpisodePageReading | None:
    """Read one complete page namespace without repairing malformed JSON."""
    payload = final_json_object(raw)
    if payload is None:
        return None
    if payload.get("schema_version") != EPISODE_SCAN_SCHEMA_VERSION:
        return None
    if (
        not _is_integer_alias(payload.get("episode"))
        or not _is_integer_alias(payload.get("page"))
        or payload.get("episode") != episode_alias
        or payload.get("page") != page_alias
    ):
        return None
    stable_identity = observation_map.get((episode_alias, page_alias))
    if stable_identity is None:
        return None
    reading = payload.get("episode_reading")
    if not isinstance(reading, dict):
        return None
    return _read_episode_namespace(
        reading,
        episode_alias=episode_alias,
        page_alias=page_alias,
        episode_id=stable_identity[0],
        page_id=stable_identity[1],
        tile_map=tile_map,
    )


def read_episode_answers(
    raw: str,
    *,
    pack_alias: int,
    expected_observations: tuple[tuple[str, str], ...],
    observation_map: Mapping[EpisodeObservationKey, EpisodeObservationValue],
    tile_map: Mapping[EpisodeTileKey, str],
) -> EpisodeScanReadings | None:
    """Parse each required episode namespace independently from one physical pack."""
    payload = final_json_object(raw)
    if (
        payload is None
        or payload.get("schema_version") != EPISODE_SCAN_SCHEMA_VERSION
        or not _is_integer_alias(payload.get("pack"))
        or payload.get("pack") != pack_alias
        or len(expected_observations) != len(set(expected_observations))
        or len(observation_map) != len(set(observation_map.values()))
        or set(observation_map.values()) != set(expected_observations)
    ):
        return None
    values = payload.get("episode_readings")
    if not isinstance(values, list):
        return None
    by_alias: dict[EpisodeObservationKey, list[dict[str, Any]]] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        episode_alias = value.get("episode")
        page_alias = value.get("page")
        if not _is_integer_alias(episode_alias) or not _is_integer_alias(page_alias):
            continue
        by_alias.setdefault((episode_alias, page_alias), []).append(value)
    alias_by_observation = {stable: alias for alias, stable in observation_map.items()}
    readings: list[EpisodePageReading] = []
    invalid: list[tuple[str, str]] = []
    for episode_id, page_id in expected_observations:
        episode_alias, page_alias = alias_by_observation[(episode_id, page_id)]
        candidates = by_alias.get((episode_alias, page_alias), [])
        reading = None
        if len(candidates) == 1:
            singular_raw = json.dumps(
                {
                    "schema_version": EPISODE_SCAN_SCHEMA_VERSION,
                    "episode": episode_alias,
                    "page": page_alias,
                    "episode_reading": candidates[0],
                },
                separators=(",", ":"),
            )
            reading = read_episode_answer(
                singular_raw,
                episode_alias=episode_alias,
                page_alias=page_alias,
                observation_map=observation_map,
                tile_map=tile_map,
            )
        if reading is None:
            invalid.append((episode_id, page_id))
        else:
            readings.append(reading)
    return EpisodeScanReadings(tuple(readings), tuple(invalid))


def _read_episode_namespace(
    reading: Mapping[str, object],
    *,
    episode_alias: int,
    page_alias: int,
    episode_id: str,
    page_id: str,
    tile_map: Mapping[EpisodeTileKey, str],
) -> EpisodePageReading | None:
    summary = bounded_model_text(
        reading.get("visual_summary"), max_chars=EPISODE_VISUAL_SUMMARY_MAX_CHARS
    )
    displayed = reading.get("representative_tiles")
    representative_reason = bounded_model_text(
        reading.get("representative_reason"),
        max_chars=EPISODE_REPRESENTATIVE_REASON_MAX_CHARS,
    )
    numbers = _tile_numbers(displayed)
    if summary is None or representative_reason is None or not numbers:
        return None
    if len(numbers) != len(set(numbers)):
        return None
    keys = tuple((episode_alias, page_alias, number) for number in numbers)
    if any(key not in tile_map for key in keys):
        return None
    return EpisodePageReading(
        episode_id=episode_id,
        page_id=page_id,
        visual_summary=summary,
        representative_asset_ids=tuple(tile_map[key] for key in keys),
        representative_reason=representative_reason,
    )


def _stated_outcome(insight: dict[str, object]) -> tuple[str | None, str | None] | None:
    """The thesis and the reason there is none, or None when they contradict.

    Exactly one of the two is the answer. Both set is a contradiction; neither
    set is an omission, resolved once the rest of the answer is known.
    """
    thesis = insight.get("thesis")
    unavailable_reason = insight.get("unavailable_reason")
    for value in (thesis, unavailable_reason):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            return None
    if thesis is not None and unavailable_reason is not None:
        return None
    return (
        thesis.strip() if isinstance(thesis, str) else None,
        unavailable_reason.strip() if isinstance(unavailable_reason, str) else None,
    )


def read_period_answer(
    raw: str,
    *,
    page_ids: tuple[str, ...],
    tile_map: Mapping[PeriodTileKey, PeriodTileValue],
) -> PeriodInsightAnswer | None:
    """Read a complete period synthesis whose evidence resolves to visible tiles."""
    payload = final_json_object(raw)
    if payload is None or payload.get("schema_version") != PERIOD_INSIGHT_SCHEMA_VERSION:
        return None
    insight = payload.get("period_insight")
    if not isinstance(insight, dict) or not page_ids or len(page_ids) != len(set(page_ids)):
        return None
    outcome = _stated_outcome(insight)
    if outcome is None:
        return None
    thesis, unavailable_reason = outcome
    evidence = _evidence(insight.get("evidence"), page_ids=page_ids, tile_map=tile_map)
    tensions = _texts(insight.get("tensions"))
    recurring_threads = _texts(insight.get("recurring_threads"))
    if (
        evidence is None
        or tensions is None
        or recurring_threads is None
        or (thesis is not None and not evidence)
    ):
        return None
    if thesis is unavailable_reason is None:
        # An answer that declined a thesis but still read the wall has answered.
        # One that says nothing at all has not, and stays unreadable.
        if not (evidence or tensions or recurring_threads):
            return None
        unavailable_reason = THESIS_DECLINED_WITHOUT_REASON
    return PeriodInsightAnswer(
        thesis=thesis,
        evidence=evidence,
        tensions=tensions,
        recurring_threads=recurring_threads,
        unavailable_reason=unavailable_reason,
    )


def _evidence(
    value: object,
    *,
    page_ids: tuple[str, ...],
    tile_map: Mapping[PeriodTileKey, PeriodTileValue],
) -> tuple[InsightEvidence, ...] | None:
    if not isinstance(value, list):
        return None
    parsed: list[InsightEvidence] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        observation = item.get("observation")
        keys = _period_tile_keys(item.get("representative_tiles"), page_ids)
        if (
            not isinstance(observation, str)
            or not observation.strip()
            or not keys
            or len(keys) != len(set(keys))
            or any(key not in tile_map for key in keys)
        ):
            return None
        references = tuple(tile_map[key] for key in keys)
        try:
            evidence = InsightEvidence(
                observation=observation.strip(),
                episode_ids=_unique(reference[0] for reference in references),
                asset_ids=_unique(reference[1] for reference in references),
            )
        except ValueError:
            return None
        parsed.append(evidence)
    return tuple(parsed)


def _period_tile_keys(value: object, page_ids: tuple[str, ...]) -> tuple[PeriodTileKey, ...]:
    numbers = _tile_numbers(value)
    if numbers:
        return tuple((page_ids[0], number) for number in numbers) if len(page_ids) == 1 else ()
    if not isinstance(value, list):
        return ()
    keys: list[PeriodTileKey] = []
    for item in value:
        if not isinstance(item, dict):
            return ()
        page_id = item.get("page_id")
        number = item.get("tile")
        if (
            not isinstance(page_id, str)
            or page_id not in page_ids
            or not isinstance(number, int)
            or isinstance(number, bool)
        ):
            return ()
        keys.append((page_id, number))
    return tuple(keys)


def _texts(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list):
        return None
    if any(not isinstance(item, str) or not item.strip() for item in value):
        return None
    return tuple(item.strip() for item in value)


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _tile_numbers(value: object) -> tuple[int, ...]:
    if not isinstance(value, list):
        return ()
    if any(not isinstance(item, int) or isinstance(item, bool) for item in value):
        return ()
    return tuple(value)


def _is_integer_alias(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1
