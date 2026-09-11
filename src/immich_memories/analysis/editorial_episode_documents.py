"""Compact factual episode documents for production retrieval."""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass

from immich_memories.analysis import editorial_wall_rows as direct

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EpisodeCandidate:
    episode_id: str
    source_index: int
    moment_ids: tuple[str, ...]
    taken_start: str
    taken_end: str
    document: str


def _present(value: str | None) -> bool:
    return value not in (None, "", "null")


def _person_summary(value: str | None) -> str | None:
    if not _present(value):
        return None
    people: list[str] = []
    for raw_person in str(value).split(";"):
        _alias, separator, facts_text = raw_person.partition(":")
        facts: dict[str, str] = {}
        for raw_fact in facts_text.split("|") if separator else ():
            key, equals, fact = raw_fact.partition("=")
            if equals:
                facts[key] = fact
        name = facts.get("name")
        if not name:
            continue
        detail = facts.get("relationship") or "relationship unknown"
        age = facts.get("age")
        if age and age != "?":
            detail = f"{detail}, age {age}"
        people.append(f"{name} ({detail})")
    return "; ".join(people) or None


def _place_summary(value: str | None) -> str | None:
    if not _present(value):
        return None
    names: list[str] = []
    for raw_place in str(value).split(";"):
        _alias, separator, name = raw_place.partition(":")
        if separator and name and name not in names:
            names.append(name)
    return "; ".join(names) or None


def compact_moment_fact(row: dict[str, str]) -> str:
    """Keep decision facts and discard repeated explanatory prose."""
    pieces = [f"at={row['taken'][:16]}"]
    descriptions = [
        row[name]
        for name in ("evidence_1_description", "evidence_2_description")
        if _present(row.get(name))
    ]
    if descriptions:
        pieces.append("seen=" + " / ".join(dict.fromkeys(descriptions)))
    people = _person_summary(row.get("people"))
    if people:
        pieces.append("people=" + people)
    elif _present(row.get("people_head")):
        pieces.append("people_shape=" + row["people_head"])
    place = _place_summary(row.get("places"))
    if place:
        pieces.append("place=" + place)
    for field in ("children", "activity", "location_head", "stitches"):
        if _present(row.get(field)):
            pieces.append(f"{field}={row[field]}")
    media = ",".join(
        f"{field}:{row[field]}"
        for field in ("visuals", "photo", "video", "live", "favorites", "span_s")
        if _present(row.get(field))
    )
    if media:
        pieces.append("media=" + media)
    return " | ".join(pieces)


def _read_direct_rows(sheet: bytes) -> list[dict[str, str]]:
    lines = sheet.decode("utf-8").splitlines()
    if len(lines) < 3 or not lines[0].startswith("@format\t"):
        raise ValueError("direct moment sheet envelope is invalid")
    header = next(csv.reader([lines[1]], delimiter="\t"))
    if len(header) < 4 or header[:2] != ["@table", "moments"]:
        raise ValueError("direct moment sheet header is invalid")
    try:
        declared = int(header[2])
    except ValueError as exc:
        raise ValueError("direct moment sheet count is invalid") from exc
    fields = header[3:]
    values = [next(csv.reader([line], delimiter="\t")) for line in lines[2:]]
    if declared != len(values) or any(len(row) != len(fields) for row in values):
        raise ValueError("direct moment sheet rows are not conserved")
    return [dict(zip(fields, row, strict=True)) for row in values]


def factual_moment_rows(tables, aliases):
    """Return each moment's literal evidence with the established association normalization."""
    # a person-scoped wall can list the same person twice on one moment; the sheet builder refuses
    # duplicates, so keep the first association per (moment, person). Recorded, not silent.
    fields, rows = tables["moment_people"]

    seen, deduped, dropped = set(), [], 0
    for r in rows:
        key = (r[0], r[1])
        if key in seen or not re.fullmatch(
            r"P[0-9]+", r[1]
        ):  # unnamed faces (U..) have no people record
            dropped += 1
            continue
        seen.add(key)
        deduped.append(r)
    if dropped:
        tables = {**tables, "moment_people": (fields, deduped)}
        logger.debug("Dropped %d duplicate or unnamed person associations", dropped)
    sheet, _counts = direct.direct_moment_sheet(tables, aliases)
    return _read_direct_rows(sheet)


def episode_candidates_any_order(tables, aliases):
    """Factual episode documents without an artificial contiguity requirement."""
    direct_rows = factual_moment_rows(tables, aliases)
    rows_by_id = {row["moment_id"]: row for row in direct_rows}
    moment_rows = direct._unique_index(direct._records(tables, "moments"), "id", "moments")
    episode_rows = direct._unique_index(direct._records(tables, "episodes"), "id", "episodes")
    order: list[str] = []
    members: dict[str, list[str]] = {}
    for alias in aliases:
        e = moment_rows[alias].get("episode", "")
        if e not in members:
            order.append(e)
            members[e] = []
        members[e].append(alias)
    out = []
    for i, e in enumerate(order):
        ms = tuple(members[e])
        rows = [rows_by_id[m] for m in ms]
        taken = [r["taken"] for r in rows]
        context = episode_rows[e].get("meaning", "").strip()
        facts = " || ".join(compact_moment_fact(r) for r in rows)
        out.append(
            EpisodeCandidate(
                episode_id=e,
                source_index=i,
                moment_ids=ms,
                taken_start=min(taken),
                taken_end=max(taken),
                document=f"occasion={min(taken)[:10]} | context={context} | member_count={len(ms)} | facts={facts}",
            )
        )
    return tuple(out)


def _sampled_moments(moment_ids, samples: int):
    """An even spread across the span, so a long family keeps its last scene."""
    if len(moment_ids) <= samples:
        return moment_ids
    if samples == 1:
        return [moment_ids[len(moment_ids) // 2]]
    return [
        moment_ids[round(index * (len(moment_ids) - 1) / (samples - 1))] for index in range(samples)
    ]


def _scene_descriptions(selected, rows_by_id) -> list[str]:
    """Visit the first description of every sampled moment before spending remaining
    space on second descriptions. A prefix of paired rows would lose the last scene."""
    descriptions: list[str] = []
    for field in ("evidence_1_description", "evidence_2_description"):
        for alias in selected:
            row = rows_by_id[alias]
            if _present(row.get(field)):
                words = row[field].split()
                description = " ".join(words[:24]) + (" …" if len(words) > 24 else "")
                if description not in descriptions:
                    descriptions.append(description)
    return descriptions


def _known_people(moment_ids, rows_by_id) -> list[str]:
    """Scene sampling must not hide a known person tagged elsewhere in this family."""
    people: list[str] = []
    for alias in moment_ids:
        known = _person_summary(rows_by_id[alias].get("people"))
        for person in known.split("; ") if known else ():
            if person not in people:
                people.append(person)
    return people


def anchor_observations(moment_ids, rows_by_id, *, samples=3):
    """Bound literal scenes, while preserving known ages and relationships as facts."""
    if samples < 1:
        raise ValueError("anchor observation sample limit must be positive")
    if not moment_ids:
        return ""
    descriptions = _scene_descriptions(_sampled_moments(moment_ids, samples), rows_by_id)
    people = _known_people(moment_ids, rows_by_id)
    # Keep whole person facts, not a prefix cut in the middle of an age or relationship.
    facts = ["seen: " + " / ".join(descriptions[:samples])]
    if people:
        facts.append(
            "known people: "
            + "; ".join(people[:8])
            + (f"; +{len(people) - 8} others" if len(people) > 8 else "")
        )
    return " | ".join(facts)
