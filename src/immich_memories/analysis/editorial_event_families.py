"""Deterministic event-family grouping over normalized production wall facts.

The caller supplies source membership and coordinates. No file path, probe
module or model call is part of the grouping rule.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import chain
from typing import Any

MERGE_GAP_SECONDS = 3 * 3600
NEAR_KM = 10.0

Point = tuple[float, float]


def _km(a: Point, b: Point) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (
        math.sin((la2 - la1) / 2) ** 2
        + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(h))


@dataclass(frozen=True)
class _EpisodeFacts:
    """Everything the merge rule reads about one canonical episode."""

    day: str
    start: float
    end: float
    loc: str
    places: frozenset[Any]
    gps: Point | None


def merge_event_families(
    tables: Mapping[str, Any],
    source_order: Sequence[str],
    mapping: Mapping[str, str],
    *,
    assets_of: Mapping[str, Sequence[str]],
    gps: Mapping[str, Point],
) -> tuple[dict[str, str], dict[str, Any]]:
    """Return (moment -> family id, log). Family id = first member episode id."""
    facts = _episode_facts(tables, source_order, mapping, assets_of=assets_of, gps=gps)
    families = _merged_families(facts)
    family_of = {episode: members[0] for members in families for episode in members}
    log = {
        "rule": {
            "same_day": True,
            "same_location_head": True,
            "max_gap_seconds": MERGE_GAP_SECONDS,
            "near_km_by_gps": NEAR_KM,
            "fallback_without_gps": "shared place id or none",
            "interleaved_episodes_unioned": False,
        },
        "episodes_before": len(facts),
        "families_after": len(families),
        "interleave_merges": 0,
        "merged_families": {members[0]: members for members in families if len(members) > 1},
    }
    return {m: family_of[mapping[m]] for m in source_order}, log


def _episode_facts(
    tables: Mapping[str, Any],
    source_order: Sequence[str],
    mapping: Mapping[str, str],
    *,
    assets_of: Mapping[str, Sequence[str]],
    gps: Mapping[str, Point],
) -> dict[str, _EpisodeFacts]:
    """Episode facts in first-appearance order, each read from its own moments."""
    fields, rows = tables["moments"]
    moments = {row[0]: dict(zip(fields, row, strict=True)) for row in rows}
    places: dict[str, set[Any]] = {}
    for moment, place in tables["moment_places"][1]:
        places.setdefault(moment, set()).add(place)
    members: dict[str, list[str]] = {}
    for moment in source_order:
        members.setdefault(mapping[moment], []).append(moment)
    return {
        episode: _facts_of(moment_ids, moments, places, assets_of=assets_of, gps=gps)
        for episode, moment_ids in members.items()
    }


def _facts_of(
    moment_ids: Sequence[str],
    moments: Mapping[str, Mapping[str, Any]],
    places: Mapping[str, set[Any]],
    *,
    assets_of: Mapping[str, Sequence[str]],
    gps: Mapping[str, Point],
) -> _EpisodeFacts:
    taken = [datetime.fromisoformat(moments[m]["taken"]) for m in moment_ids]
    ends = [
        t.timestamp() + float(moments[m]["span_s"] or 0)
        for t, m in zip(taken, moment_ids, strict=True)
    ]
    heads = [
        moments[m]["location_head"]
        for m in moment_ids
        if moments[m]["location_head"] not in ("", "null")
    ]
    points = [gps[a] for m in moment_ids for a in assets_of.get(m, []) if a in gps]
    return _EpisodeFacts(
        day=taken[0].date().isoformat(),
        start=min(t.timestamp() for t in taken),
        end=max(ends),
        loc=max(sorted(set(heads)), key=lambda h: (heads.count(h), h)) if heads else "",
        places=frozenset(chain.from_iterable(places.get(m, set()) for m in moment_ids)),
        gps=_centroid(points),
    )


def _centroid(points: Sequence[Point]) -> Point | None:
    if not points:
        return None
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _merged_families(facts: Mapping[str, _EpisodeFacts]) -> list[list[str]]:
    """Chain each episode onto the previous family, or open a new one.

    Canonical episodes may interleave when different people are in different
    places. A/B/A remains two happenings; chronology cannot override the place
    checks in ``_continues``.
    """
    families: list[list[str]] = []
    for episode, fact in facts.items():
        if families and _continues(fact, families[-1], facts):
            families[-1].append(episode)
        else:
            families.append([episode])
    return families


def _continues(
    fact: _EpisodeFacts, head: Sequence[str], facts: Mapping[str, _EpisodeFacts]
) -> bool:
    first = facts[head[0]]
    last_end = max(facts[member].end for member in head)
    return (
        fact.day == first.day
        and fact.loc == first.loc
        and fact.start - last_end <= MERGE_GAP_SECONDS
        and _near(fact, head, facts)
    )


def _near(fact: _EpisodeFacts, head: Sequence[str], facts: Mapping[str, _EpisodeFacts]) -> bool:
    head_gps = [facts[member].gps for member in head if facts[member].gps]
    if fact.gps and head_gps:
        return min(_km(fact.gps, point) for point in head_gps if point is not None) <= NEAR_KM
    head_places = frozenset(chain.from_iterable(facts[member].places for member in head))
    return not fact.places or not head_places or bool(fact.places & head_places)
