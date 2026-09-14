"""Inventory depicted moments before a film's duration can narrow the source pool.

Capture moments and event families are reading boundaries, not editorial identity.
Every eligible unit is accounted for, including alternatives to a representative.
The inventory has no film length, funded count, or early stopping condition.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from operator import itemgetter
from typing import Any

from immich_memories.analysis.editorial_page_recovery import read_page_answer
from immich_memories.analysis.editorial_story_replies import _lenient_object

INVENTORY_VERSION = "depicted-moments-v1"
PAGE_UNITS = 16
PAGE_CHARS = 14000


@dataclass
class DepictedMoment:
    key: str
    event: str
    content: str
    primary: str
    alternatives: list[str] = field(default_factory=list)

    @property
    def sources(self) -> list[str]:
        return [self.primary, *self.alternatives]


def pages(rows, *, max_items=PAGE_UNITS, max_chars=PAGE_CHARS):
    """Page every row in source order. Limits change request size, never coverage."""
    page: list[Any] = []
    size = 0
    for row in rows:
        width = len(json.dumps(row, ensure_ascii=False))
        if page and (len(page) >= max_items or size + width > max_chars):
            yield page
            page, size = [], 0
        page.append(row)
        size += width
    if page:
        yield page


def _moments_envelope(raw: str) -> dict:
    """The reply's moment rows, whatever envelope the model wrapped them in.

    Some complete replies carry the rows as an outer array instead of wrapping them in
    {"moments": ...}. Normalize only that representation; never manufacture rows or close
    a truncated array. The caller's source-coverage check still sees every offered source.
    """
    text = raw.strip()
    if text.startswith("```") and text.endswith("```"):
        header, separator, body = text.partition("\n")
        if separator and header.strip().lower() in {"```", "```json"}:
            text = body[:-3].strip()
    obj = {"moments": json.loads(text)} if text.startswith("[") else _lenient_object(raw)
    if not isinstance(obj, dict) or not isinstance(obj.get("moments"), list):
        raise ValueError("moment inventory requires a moments list")
    return obj


def _claimable_sources(row, new_ids, seen) -> list[str]:
    sources = row.get("sources")
    if isinstance(sources, str):
        sources = [sources]
    if not isinstance(sources, list):
        return []
    return [
        a for a in dict.fromkeys(sources) if isinstance(a, str) and a in new_ids and a not in seen
    ]


def _representative(row, members, units) -> tuple[str, list[str], str]:
    primary, content = row.get("primary"), row.get("content")
    if not isinstance(primary, str) or primary not in members:
        primary = members[0]
    if not isinstance(content, str) or not content.strip():
        content = "Depicted content not described"
    content = " ".join(content.split()[:60])
    favourites = [a for a in members if units[a].get("favourite")]
    # The established favourite law applies inside equivalent representations.
    if favourites and primary not in favourites:
        primary = favourites[0]
    alternatives = [a for a in members if a != primary]
    alternatives.sort(key=lambda a: not bool(units[a].get("favourite")))
    return primary, alternatives, content.strip()


def _read_group(row, *, new_ids, existing, units, seen, updated):
    """One answered group, or None when nothing in it can be believed."""
    if not isinstance(row, dict):
        return None
    same = row.get("same_as")
    if not isinstance(same, str) or same not in existing or same in updated:
        same = None
    sources = _claimable_sources(row, new_ids, seen)
    if not sources:
        return None
    seen.update(sources)
    if same is not None:
        updated.add(same)
    members = list(dict.fromkeys([*(existing[same].sources if same else ()), *sources]))
    primary, alternatives, content = _representative(row, members, units)
    return same, primary, alternatives, content


def read_inventory_page(raw, *, new_ids, existing, units):
    """Read the page's groups; coverage of the new sources is still required.

    A reference the answer invented (an unknown or repeated existing moment, a source that
    was never offered, a source already claimed) is dropped rather than voiding the page:
    dropping it cannot hide material, because the coverage check below still sees every
    offered source. Only a reply with no `moments` list, or one that leaves a source
    unaccounted for, fails the page.
    """
    obj = _moments_envelope(raw)
    seen: set[str] = set()
    updated: set[str] = set()
    result = []
    for row in obj["moments"]:
        group = _read_group(
            row, new_ids=new_ids, existing=existing, units=units, seen=seen, updated=updated
        )
        if group is not None:
            result.append(group)
    if seen != set(new_ids):
        raise ValueError("moment inventory omitted source units; coverage is incomplete")
    return result


def inventory_event(
    judge,
    *,
    event: str,
    units: list[dict],
    context: str,
    line: Callable[[dict], str],
    record: Callable[[dict], None] = lambda _: None,
) -> tuple[list[DepictedMoment], dict]:
    """Read all pictures, using earlier depicted moments to join later equivalent views."""
    ordered = sorted(units, key=itemgetter("taken", "asset_id"))
    by_alias = {f"U{i + 1:04d}": u for i, u in enumerate(ordered)}
    rows = [
        {
            "source": alias,
            "capture_group": u.get("moment"),
            "taken": u["taken"],
            "favourite": bool(u.get("favourite")),
            "facts": line(u),
        }
        for alias, u in by_alias.items()
    ]
    moments: dict[str, DepictedMoment] = {}
    audit: dict[str, Any] = {
        "version": INVENTORY_VERSION,
        "event": event,
        "source_units": len(ordered),
        "pages": [],
        "status": "reading",
    }
    for number, page in enumerate(pages(rows), 1):
        existing = [
            {
                "moment": m.key,
                "content": m.content,
                "primary": m.primary,
                "representative_facts": line(by_alias[m.primary]),
            }
            for m in moments.values()
        ]
        prompt = f"""Inventory distinct depicted moments in a personal library. {INVENTORY_VERSION}.
This is source reading, before any film duration or picture allocation.

EPISODE CONTEXT (inferred; the source facts can correct it)
{context}

MOMENTS ALREADY READ
{json.dumps(existing, ensure_ascii=False)}

NEW SOURCES
{json.dumps(page, ensure_ascii=False)}

Account for EVERY new source exactly once. Group interchangeable pictures of the same
depicted content together. Choose the strongest representative; the others remain alternatives.
A pose, expression, framing, camera angle, clothing detail, or still versus Live Photo format
does not itself create another contribution. A still and its own motion tell one moment.
Different actions, stages, settings, or genuinely different participants can deserve separate
moments even within one capture_group. Conversely, two capture_group IDs do not prove distinct
content. Do not merge different participants or actions just because they share an episode.
Keep all content here, including quiet or imperfect records; relevance to the film is judged later.
Never invent actions, emotions, relationships or progress from the passage of time.

For each group return same_as (an existing moment ID for equivalent content, otherwise null),
sources (NEW source IDs belonging to it), primary (the best source in the group, or the existing
primary), and content (at most 60 words describing the shared observable content).
An existing moment may be updated once per response. Prefer a favourite among equivalent views.
JSON only: {{"moments":[{{"same_as":null,"sources":["U0001"],"primary":"U0001","content":"Observed content"}}]}}
"""
        try:
            groups = read_page_answer(
                judge,
                stage=f"moment-inventory-{event}-{number}",
                prompt=prompt,
                max_tokens=400 + 140 * len(page),
                read=partial(
                    read_inventory_page,
                    new_ids={r["source"] for r in page},
                    existing=moments,
                    units=by_alias,
                ),
            )
        except ValueError as exc:
            audit.update(status="incomplete", failure=str(exc))
            record(audit)
            raise
        for same, primary, alternatives, content in groups:
            key = same or f"K{len(moments) + 1:04d}"
            moments[key] = DepictedMoment(key, event, content, primary, alternatives)
        audit["pages"].append(
            {
                "offered": [by_alias[r["source"]]["asset_id"] for r in page],
                "depicted_moments_after": len(moments),
            }
        )
        record(audit)
    # Alias IDs never escape their exact event request. Persist real source membership.
    result = [
        DepictedMoment(
            f"{event}:{m.key}",
            event,
            m.content,
            by_alias[m.primary]["asset_id"],
            [by_alias[a]["asset_id"] for a in m.alternatives],
        )
        for m in moments.values()
    ]
    audit.update(
        status="complete",
        moments=[vars(m) for m in result],
        examined_units=sum(len(m.sources) for m in result),
    )
    record(audit)
    return result, audit
