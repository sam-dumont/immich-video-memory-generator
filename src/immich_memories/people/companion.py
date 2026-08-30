"""The people file — the graph's findings, written where a person can argue.

One YAML file per library, auto-populated and hand-editable, holding what the
inference read off the numbers next to an empty space the user fills in. The
contract that makes it safe to regenerate: **confirmed beats inferred**. A
refresh recomputes every `inferred:` block and copies every `confirmed:` block
through untouched, and a person somebody has annotated is never dropped, even
when they fall off the roster.
"""

from __future__ import annotations

import copy
import logging
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from immich_memories.people.relationships import owner_role, reciprocal_kind
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
    """The person entries in a document, skipping anything malformed.

    An entry has to carry a list of ids to be an entry at all. The file is
    meant to be hand-edited, and `ids: 5f2c…` written without the brackets is
    a string that reads as a list of characters everywhere downstream.
    """
    people = document.get("people")
    if not isinstance(people, list):
        return []
    return [
        entry for entry in people if isinstance(entry, dict) and isinstance(entry.get("ids"), list)
    ]


def retained_immich_ids(document: dict[str, Any]) -> set[str]:
    """Face ids a user deliberately kept, even if they sit below the scan floor."""
    retained: set[str] = set()
    for entry in people_entries(document):
        if not (_has_content(entry.get("confirmed")) or entry.get("origin") == "immich"):
            continue
        retained.update(
            str(person_id) for person_id in entry["ids"] if not str(person_id).startswith("manual:")
        )
    return retained


def save_graph(path: Path, graph: PeopleGraph) -> None:
    """Write the graph, preserving every confirmed field already on disk."""
    standing = load_document(path)
    kept = _confirmed_by_id(standing)
    entries = [_entry_for(node, kept) for node in graph.people]
    entries.extend(_annotated_strangers(standing, graph))
    _write(
        path,
        {
            "version": SCHEMA_VERSION,
            "generated": (graph.built_at or datetime.now()).isoformat(timespec="seconds"),
            "owner": _owner_block(graph),
            "people": entries,
        },
    )


def save_confirmed(path: Path, person_id: str, confirmed: dict[str, Any]) -> None:
    """Replace one person's confirmed block, leaving the rest of the file alone.

    The settings page's write path, and the only other one there is. It reads
    the file, swaps one block and writes the whole document back through the
    same writer, so a confirmation cannot arrive with different permissions or
    a different header than a scan's.
    """
    document = load_document(path)
    entries = [entry for entry in people_entries(document) if person_id in entry["ids"]]
    if not entries:
        logger.warning("Nothing in %s to confirm for that person; the file moved underneath", path)
        return
    for entry in entries:
        entry["confirmed"] = copy.deepcopy(confirmed)
    _write(path, document)


def add_confirmed_person(
    path: Path,
    name: str,
    *,
    person_id: str | None = None,
    role: str | None = None,
) -> str:
    """Add somebody the quantitative roster did not retain.

    A real Immich id may be supplied for a below-threshold face. Somebody who
    has no Immich face record gets a local id and remains a first-class graph
    node; being off camera is not evidence that a relative does not exist.
    """
    document = load_document(path)
    matches = [entry for entry in people_entries(document) if entry.get("name") == name]
    if len(matches) > 1:
        msg = f"More than one person is named {name!r}; use a person id"
        raise ValueError(msg)
    if matches:
        return str(matches[0]["ids"][0])

    local_id = person_id or f"manual:{uuid.uuid4()}"
    document.setdefault("people", []).append(
        {
            "ids": [local_id],
            "name": name,
            "birth_date": None,
            "inferred": {
                "tier": None,
                "counts_reliable": False,
                "evidence": {},
                "links": [],
            },
            "confirmed": {"role": role, "links": [], "notes": None},
            "origin": "immich" if person_id else "manual",
        }
    )
    _write(path, document)
    return local_id


def save_confirmed_relationship(path: Path, source_id: str, kind: str, target_id: str) -> None:
    """Write one user relationship and its reciprocal as one file operation."""
    if source_id == target_id:
        raise ValueError("A person cannot have a relationship with themselves")
    document = load_document(path)
    source = _entry_with_id(document, source_id)
    target = _entry_with_id(document, target_id)
    reverse = reciprocal_kind(kind)
    _upsert_confirmed_link(source, kind, target_id, reverse)
    _upsert_confirmed_link(target, reverse, source_id, kind)
    _fill_owner_role(document, source, kind, target_id)
    _fill_owner_role(document, target, reverse, source_id)
    _write(path, document)


def remove_confirmed_relationship(path: Path, source_id: str, kind: str, target_id: str) -> None:
    """Remove one confirmed relationship and the reciprocal written with it."""
    document = load_document(path)
    source = _entry_with_id(document, source_id)
    target = _entry_with_id(document, target_id)
    source_link = _confirmed_link(source, kind, target_id)
    reverse = (
        str(source_link.get("reverse"))
        if source_link and source_link.get("reverse")
        else reciprocal_kind(kind)
    )
    _remove_confirmed_link(source, kind, target_id)
    _remove_confirmed_link(target, reverse, source_id)
    _write(path, document)


def _write(path: Path, document: dict[str, Any]) -> None:
    body = yaml.dump(document, sort_keys=False, allow_unicode=True, default_flow_style=False)
    write_secret_file(path, _FILE_HEADER + body)


def _entry_with_id(document: dict[str, Any], person_id: str) -> dict[str, Any]:
    matches = [entry for entry in people_entries(document) if person_id in entry["ids"]]
    if len(matches) != 1:
        msg = f"Expected one people entry for {person_id!r}, found {len(matches)}"
        raise ValueError(msg)
    return matches[0]


def _confirmed_block(entry: dict[str, Any]) -> dict[str, Any]:
    confirmed = entry.get("confirmed")
    if not isinstance(confirmed, dict):
        confirmed = _blank_confirmed()
        entry["confirmed"] = confirmed
    confirmed.setdefault("role", None)
    confirmed.setdefault("links", [])
    confirmed.setdefault("notes", None)
    return confirmed


def _confirmed_link(entry: dict[str, Any], kind: str, target_id: str) -> dict[str, Any] | None:
    links = _confirmed_block(entry).get("links")
    if not isinstance(links, list):
        return None
    return next(
        (
            link
            for link in links
            if isinstance(link, dict) and link.get("kind") == kind and link.get("with") == target_id
        ),
        None,
    )


def _upsert_confirmed_link(
    entry: dict[str, Any], kind: str, target_id: str, reverse_kind: str
) -> None:
    confirmed = _confirmed_block(entry)
    links = confirmed["links"]
    if not isinstance(links, list):
        links = []
        confirmed["links"] = links
    existing = _confirmed_link(entry, kind, target_id)
    if existing is not None:
        existing["decision"] = "confirmed"
        existing["reverse"] = reverse_kind
        return
    links.append(
        {
            "kind": kind,
            "with": target_id,
            "reverse": reverse_kind,
            "decision": "confirmed",
        }
    )


def _remove_confirmed_link(entry: dict[str, Any], kind: str, target_id: str) -> None:
    confirmed = _confirmed_block(entry)
    links = confirmed.get("links")
    if not isinstance(links, list):
        return
    confirmed["links"] = [
        link
        for link in links
        if not (
            isinstance(link, dict) and link.get("kind") == kind and link.get("with") == target_id
        )
    ]


def _fill_owner_role(
    document: dict[str, Any], entry: dict[str, Any], kind: str, target_id: str
) -> None:
    owner = document.get("owner")
    owner_id = owner.get("person_id") if isinstance(owner, dict) else None
    confirmed = _confirmed_block(entry)
    if owner_id == target_id and not confirmed.get("role"):
        confirmed["role"] = owner_role(kind)


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
        # Copied, not referenced: one entry can name several ids, and handing
        # the same object to two people makes yaml emit an anchor and an alias.
        # In a file people edit by hand that is a trap — changing one person
        # changes the other, and deleting the anchor breaks both.
        "confirmed": copy.deepcopy(kept.get(person.person_id)) or _blank_confirmed(),
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
    """The space reserved for the user, and never filled by the scan.

    Written out empty rather than omitted: the file is meant to be edited by
    hand, and a field nobody can see is a field nobody fills in.
    """
    return {"role": None, "links": [], "notes": None}


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
        if not present.intersection(entry["ids"])
        and (_has_content(entry.get("confirmed")) or entry.get("origin") == "manual")
    ]


def _has_content(confirmed: object) -> bool:
    return isinstance(confirmed, dict) and any(value for value in confirmed.values())


def _month(value: date | None) -> str | None:
    return f"{value:%Y-%m}" if value else None


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None
