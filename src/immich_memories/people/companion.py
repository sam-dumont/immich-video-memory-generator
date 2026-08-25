"""The people file — the graph's findings, written where a person can argue.

One YAML file per library, auto-populated and hand-editable, holding what the
inference read off the numbers next to an empty space the user fills in. The
contract that makes it safe to regenerate: **confirmed beats inferred**. A
refresh recomputes every `inferred:` block and copies every `confirmed:` block
through untouched, and a person somebody has annotated is never dropped, even
when they fall off the roster.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from immich_memories.security import write_secret_file

if TYPE_CHECKING:
    from immich_memories.people.graph import PeopleGraph, PersonNode
    from immich_memories.people.signatures import Link

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_FILE_HEADER = """\
# Who is in this library, as the people graph reads it.
#
# Everything under `inferred:` is recomputed by `immich-memories people scan`
# and your edits there will be overwritten. Everything under `confirmed:` is
# yours: the scan copies it through untouched, forever, and prefers it to its
# own reading. Confirmed beats inferred.
"""


def default_people_path() -> Path:
    """Where the people file lives when nobody said otherwise.

    Resolved per call rather than at import, so a test, a container or a
    service account can move the home directory underneath us.
    """
    return Path.home() / ".immich-memories" / "people.yaml"


def load_document(path: Path) -> dict[str, Any]:
    """The people file as it stands, or an empty document.

    Nothing raises. A damaged file must not cost somebody the roster they
    curated by hand, so it reads as absent and the scan writes a new one.
    """
    if not path.exists():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        logger.warning("%s is not readable as a people file; starting fresh", path)
        return {}
    return loaded if isinstance(loaded, dict) else {}


def people_entries(document: dict[str, Any]) -> list[dict[str, Any]]:
    """The person entries in a document, skipping anything malformed."""
    people = document.get("people")
    if not isinstance(people, list):
        return []
    return [entry for entry in people if isinstance(entry, dict) and entry.get("ids")]


def save_graph(path: Path, graph: PeopleGraph) -> None:
    """Write the graph, preserving every confirmed field already on disk."""
    standing = load_document(path)
    kept = _confirmed_by_id(standing)
    entries = [_entry_for(node, kept) for node in graph.people]
    entries.extend(_annotated_strangers(standing, graph))
    document = {
        "version": SCHEMA_VERSION,
        "generated": (graph.built_at or datetime.now()).isoformat(timespec="seconds"),
        "owner": _owner_block(graph),
        "people": entries,
    }
    body = yaml.dump(document, sort_keys=False, allow_unicode=True, default_flow_style=False)
    write_secret_file(path, _FILE_HEADER + body)


def _owner_block(graph: PeopleGraph) -> dict[str, Any] | None:
    if graph.owner is None:
        return None
    return {
        "person_id": graph.owner.person_id,
        "name": graph.owner.name,
        "identified": graph.owner.identified,
    }


def _entry_for(node: PersonNode, kept: dict[str, dict[str, Any]]) -> dict[str, Any]:
    person = node.evidence
    return {
        "ids": [person.person_id],
        "name": person.name,
        "birth_date": _iso(person.birth_date),
        "inferred": {
            "tier": node.tier.value,
            "counts_reliable": node.counts_reliable,
            "evidence": _evidence_block(node),
            "links": [_link_block(link) for link in node.links],
        },
        "confirmed": kept.get(person.person_id, _blank_confirmed()),
    }


def _evidence_block(node: PersonNode) -> dict[str, Any]:
    person = node.evidence
    return {
        "count": person.count,
        "active_months": person.month_count,
        "first_month": _month(person.first_month),
        "last_month": _month(person.last_month),
        "span_years": round(person.span_years, 1),
        "onset": _month(person.onset),
        "concentration": round(person.concentration, 1),
        "continuity": round(person.continuity, 2),
    }


def _link_block(link: Link) -> dict[str, Any]:
    return {
        "kind": link.kind.value,
        "with": link.target_id,
        "confidence": round(link.confidence, 2),
        "via": link.via,
    }


def _blank_confirmed() -> dict[str, Any]:
    """The space reserved for the user, and never filled by the scan."""
    return {"role": None, "links": []}


def _confirmed_by_id(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    kept: dict[str, dict[str, Any]] = {}
    for entry in people_entries(document):
        confirmed = entry.get("confirmed")
        if not isinstance(confirmed, dict):
            continue
        for person_id in entry["ids"]:
            kept[person_id] = confirmed
    return kept


def _annotated_strangers(document: dict[str, Any], graph: PeopleGraph) -> list[dict[str, Any]]:
    """Entries the refresh no longer sees but somebody took the trouble to fill.

    Falling off the roster is a thing the library does — a person merged, a
    threshold moved, Immich unreachable for one call. None of those is a reason
    to delete an answer a person gave, so an entry carrying anything confirmed
    is carried forward exactly as it was.
    """
    present = {node.evidence.person_id for node in graph.people}
    return [
        entry
        for entry in people_entries(document)
        if not present.intersection(entry["ids"]) and _has_content(entry.get("confirmed"))
    ]


def _has_content(confirmed: object) -> bool:
    return isinstance(confirmed, dict) and any(value for value in confirmed.values())


def _month(value: date | None) -> str | None:
    return f"{value:%Y-%m}" if value else None


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None
