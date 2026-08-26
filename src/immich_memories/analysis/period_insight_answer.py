"""Strict answers for hierarchical visual insight."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from immich_memories.analysis.editorial_contracts import InsightEvidence

EpisodeTileKey = tuple[str, str, int]
PeriodTileKey = tuple[str, int]
PeriodTileValue = tuple[str, str]
EPISODE_SCAN_SCHEMA_VERSION = "episode-scan-v1"
PERIOD_INSIGHT_SCHEMA_VERSION = "period-insight-v1"


@dataclass(frozen=True)
class EpisodePageReading:
    """One complete page observation resolved back to stable asset IDs."""

    episode_id: str
    page_id: str
    visual_summary: str
    representative_asset_ids: tuple[str, ...]
    representative_reason: str


@dataclass(frozen=True)
class PeriodInsightAnswer:
    """A complete provisional reading before request provenance is attached."""

    thesis: str | None
    evidence: tuple[InsightEvidence, ...]
    tensions: tuple[str, ...]
    recurring_threads: tuple[str, ...]
    unavailable_reason: str | None


@dataclass(frozen=True)
class EpisodeScanReadings:
    """Valid episode namespaces and the required observations that did not parse."""

    readings: tuple[EpisodePageReading, ...]
    invalid_observations: tuple[tuple[str, str], ...]


def read_episode_answer(
    raw: str,
    *,
    episode_id: str,
    page_id: str,
    tile_map: Mapping[EpisodeTileKey, str],
) -> EpisodePageReading | None:
    """Read one complete page namespace without repairing malformed JSON."""
    payload = _final_json_object(raw)
    if payload is None:
        return None
    if payload.get("schema_version") != EPISODE_SCAN_SCHEMA_VERSION:
        return None
    if payload.get("episode_id") != episode_id or payload.get("page_id") != page_id:
        return None
    reading = payload.get("episode_reading")
    if not isinstance(reading, dict):
        return None
    return _read_episode_namespace(
        reading,
        episode_id=episode_id,
        page_id=page_id,
        tile_map=tile_map,
    )


def read_episode_answers(
    raw: str,
    *,
    pack_id: str,
    expected_observations: tuple[tuple[str, str], ...],
    tile_map: Mapping[EpisodeTileKey, str],
) -> EpisodeScanReadings | None:
    """Parse each required episode namespace independently from one physical pack."""
    payload = _final_json_object(raw)
    if (
        payload is None
        or payload.get("schema_version") != EPISODE_SCAN_SCHEMA_VERSION
        or payload.get("pack_id") != pack_id
        or len(expected_observations) != len(set(expected_observations))
    ):
        return None
    values = payload.get("episode_readings")
    if not isinstance(values, list):
        return None
    by_identity: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        episode_id = value.get("episode_id")
        page_id = value.get("page_id")
        if not isinstance(episode_id, str) or not isinstance(page_id, str):
            continue
        by_identity.setdefault((episode_id, page_id), []).append(value)
    readings: list[EpisodePageReading] = []
    invalid: list[tuple[str, str]] = []
    for episode_id, page_id in expected_observations:
        candidates = by_identity.get((episode_id, page_id), [])
        reading = None
        if len(candidates) == 1:
            singular_raw = json.dumps(
                {
                    "schema_version": EPISODE_SCAN_SCHEMA_VERSION,
                    "episode_id": episode_id,
                    "page_id": page_id,
                    "episode_reading": candidates[0],
                },
                separators=(",", ":"),
            )
            reading = read_episode_answer(
                singular_raw,
                episode_id=episode_id,
                page_id=page_id,
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
    episode_id: str,
    page_id: str,
    tile_map: Mapping[EpisodeTileKey, str],
) -> EpisodePageReading | None:
    summary = reading.get("visual_summary")
    displayed = reading.get("representative_tiles")
    representative_reason = reading.get("representative_reason")
    numbers = _tile_numbers(displayed)
    if (
        not isinstance(summary, str)
        or not summary.strip()
        or not isinstance(representative_reason, str)
        or not representative_reason.strip()
        or not numbers
    ):
        return None
    if len(numbers) != len(set(numbers)):
        return None
    keys = tuple((episode_id, page_id, number) for number in numbers)
    if any(key not in tile_map for key in keys):
        return None
    return EpisodePageReading(
        episode_id=episode_id,
        page_id=page_id,
        visual_summary=summary.strip(),
        representative_asset_ids=tuple(tile_map[key] for key in keys),
        representative_reason=representative_reason.strip(),
    )


def read_period_answer(
    raw: str,
    *,
    page_ids: tuple[str, ...],
    tile_map: Mapping[PeriodTileKey, PeriodTileValue],
) -> PeriodInsightAnswer | None:
    """Read a complete period synthesis whose evidence resolves to visible tiles."""
    payload = _final_json_object(raw)
    if payload is None or payload.get("schema_version") != PERIOD_INSIGHT_SCHEMA_VERSION:
        return None
    insight = payload.get("period_insight")
    if not isinstance(insight, dict):
        return None
    thesis = insight.get("thesis")
    unavailable_reason = insight.get("unavailable_reason")
    if thesis is not None and (not isinstance(thesis, str) or not thesis.strip()):
        return None
    if unavailable_reason is not None and (
        not isinstance(unavailable_reason, str) or not unavailable_reason.strip()
    ):
        return None
    if thesis is unavailable_reason is None:
        return None
    if not page_ids or len(page_ids) != len(set(page_ids)):
        return None
    evidence = _evidence(insight.get("evidence"), page_ids=page_ids, tile_map=tile_map)
    tensions = _texts(insight.get("tensions"))
    recurring_threads = _texts(insight.get("recurring_threads"))
    if evidence is None or tensions is None or recurring_threads is None:
        return None
    return PeriodInsightAnswer(
        thesis=thesis.strip() if isinstance(thesis, str) else None,
        evidence=evidence,
        tensions=tensions,
        recurring_threads=recurring_threads,
        unavailable_reason=(
            unavailable_reason.strip() if isinstance(unavailable_reason, str) else None
        ),
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
        parsed.append(
            InsightEvidence(
                observation=observation.strip(),
                episode_ids=_unique(reference[0] for reference in references),
                asset_ids=_unique(reference[1] for reference in references),
            )
        )
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


def _final_json_object(raw: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, character in enumerate(raw):
        if character != "{":
            continue
        try:
            value, end = decoder.raw_decode(raw, index)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and raw[end:].strip() in ("", "```"):
            return value
    return None
