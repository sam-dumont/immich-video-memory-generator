"""Prototype people metadata shaped for editorial prompts, with provenance intact."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from immich_memories.people.assumptions import family_assumptions
from immich_memories.people.companion import default_people_path, load_document, people_entries
from immich_memories.people.relationships import relationship_label


@dataclass(frozen=True)
class PersonLink:
    """One direct relationship available as prompt context."""

    kind: str
    target_id: str
    target_name: str
    source: str


@dataclass(frozen=True)
class PersonFact:
    """Stable and refreshable metadata for one named person."""

    name: str
    relationship: str
    relationship_source: str
    birth_date: str | None
    first_month: str | None
    onset: str | None
    tier: str | None
    links: tuple[PersonLink, ...] = ()


def load_person_facts(
    path: Path | None = None,
    *,
    include_derived: bool = False,
) -> dict[str, PersonFact]:
    """Read people context, optionally following confirmed paths one step further."""
    document = load_document(path or default_people_path())
    entries = people_entries(document)
    names = {
        str(person_id): str(entry.get("name") or "?")
        for entry in entries
        for person_id in entry["ids"]
    }
    owner = document.get("owner") if isinstance(document.get("owner"), dict) else {}
    owner_id = str(owner.get("person_id") or "")
    links = _context_links(entries, names, document, include_derived=include_derived)

    facts: dict[str, PersonFact] = {}
    for entry in entries:
        person_id = str(entry["ids"][0])
        inferred = entry.get("inferred") if isinstance(entry.get("inferred"), dict) else {}
        evidence = inferred.get("evidence") if isinstance(inferred.get("evidence"), dict) else {}
        confirmed = entry.get("confirmed") if isinstance(entry.get("confirmed"), dict) else {}
        mine = tuple(sorted(links.get(person_id, {}).values(), key=_link_key))
        relationship, relationship_source = _primary_relationship(
            person_id,
            owner_id,
            owner,
            str(confirmed.get("role") or "").strip(),
            mine,
        )
        name = str(entry.get("name") or "?")
        birth_date = str(entry.get("birth_date")) if entry.get("birth_date") else None
        first_month = str(evidence.get("first_month")) if evidence.get("first_month") else None
        facts[name] = PersonFact(
            name=name,
            relationship=relationship,
            relationship_source=relationship_source,
            birth_date=birth_date,
            first_month=_credible_first_month(first_month, birth_date),
            onset=str(evidence.get("onset")) if evidence.get("onset") else None,
            tier=str(inferred.get("tier")) if inferred.get("tier") else None,
            links=mine,
        )
    return facts


def _context_links(
    entries: list[dict[str, Any]],
    names: dict[str, str],
    document: dict[str, Any],
    *,
    include_derived: bool,
) -> dict[str, dict[tuple[str, str], PersonLink]]:
    links: dict[str, dict[tuple[str, str], PersonLink]] = {}
    for entry in entries:
        source_id = str(entry["ids"][0])
        confirmed = entry.get("confirmed")
        raw_links = confirmed.get("links") if isinstance(confirmed, dict) else None
        if not isinstance(raw_links, list):
            continue
        for raw in raw_links:
            if (
                not isinstance(raw, dict)
                or not raw.get("with")
                or raw.get("decision", "confirmed") == "rejected"
            ):
                continue
            _add_link(
                links,
                source_id,
                str(raw.get("kind") or "link"),
                str(raw["with"]),
                names,
                "confirmed",
            )

    if include_derived:
        for assumption in family_assumptions(document):
            _add_link(
                links,
                assumption.source_id,
                assumption.kind,
                assumption.target_id,
                names,
                "derived",
            )
            _add_link(
                links,
                assumption.target_id,
                assumption.reverse_kind,
                assumption.source_id,
                names,
                "derived",
            )
    return links


def _add_link(
    links: dict[str, dict[tuple[str, str], PersonLink]],
    source_id: str,
    kind: str,
    target_id: str,
    names: dict[str, str],
    source: str,
) -> None:
    if target_id not in names:
        return
    mine = links.setdefault(source_id, {})
    key = kind, target_id
    standing = mine.get(key)
    if standing is None or standing.source == "derived" and source == "confirmed":
        mine[key] = PersonLink(kind, target_id, names[target_id], source)


def _primary_relationship(
    person_id: str,
    owner_id: str,
    owner: dict[str, Any],
    role: str,
    links: tuple[PersonLink, ...],
) -> tuple[str, str]:
    if person_id == owner_id:
        return f"library owner ({owner.get('identified') or 'identified'})", "owner"
    if role:
        return role, "confirmed"
    to_owner = [link for link in links if link.target_id == owner_id]
    if to_owner:
        chosen = min(to_owner, key=lambda link: (link.source != "confirmed", link.kind))
        return f"{relationship_label(chosen.kind)} library owner", chosen.source
    return "unconfirmed", "unconfirmed"


def _link_key(link: PersonLink) -> tuple[bool, str, str]:
    return link.source != "confirmed", link.target_name.casefold(), link.kind


def _credible_first_month(first_month: str | None, birth_date: str | None) -> str | None:
    """Do not hand a model a face match from before the person was born."""
    if not first_month or not birth_date:
        return first_month
    try:
        first = date.fromisoformat(f"{first_month}-01")
        born = date.fromisoformat(birth_date).replace(day=1)
    except ValueError:
        return first_month
    return first_month if first >= born else None
