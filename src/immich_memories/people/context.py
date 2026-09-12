"""Immutable people metadata shaped for editorial prompt annotations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from immich_memories.people.assumptions import family_assumptions
from immich_memories.people.companion import default_people_path, load_document, people_entries
from immich_memories.people.relationships import relationship_label


@dataclass(frozen=True, slots=True)
class PersonPromptRelationship:
    """One graph edge and whether a person confirmed it or closure derived it."""

    kind: str
    target_id: str
    target_name: str
    source: Literal["confirmed", "derived"]


@dataclass(frozen=True, slots=True)
class PersonPromptContext:
    """Prompt-safe identity facts shared by every Immich ID for one person."""

    person_ids: tuple[str, ...]
    name: str
    role: str | None
    tier: str | None
    birth_date: str | None
    relationship: str
    relationship_source: Literal["owner", "confirmed", "derived", "unconfirmed"]
    first_month: str | None
    onset: str | None
    relationship_current: bool
    owner_relationship_kinds: tuple[str, ...] = ()
    relationships: tuple[PersonPromptRelationship, ...] = ()


def load_people_prompt_context(
    path: Path | None = None,
    *,
    include_derived: bool = False,
) -> Mapping[str, PersonPromptContext]:
    """Load one immutable lookup entry for every known Immich person ID."""
    document = load_document(path or default_people_path())
    entries = people_entries(document)
    ids_by_entry = [(_person_ids(entry), entry) for entry in entries]
    canonical_by_id = {
        person_id: person_ids[0]
        for person_ids, _entry in ids_by_entry
        for person_id in person_ids
        if person_ids
    }
    names = {
        person_ids[0]: str(entry.get("name") or "?")
        for person_ids, entry in ids_by_entry
        if person_ids
    }
    relationships = _relationships(
        ids_by_entry,
        canonical_by_id,
        names,
        document,
        include_derived=include_derived,
    )
    owner = _mapping(document.get("owner"))
    owner_id = canonical_by_id.get(str(owner.get("person_id") or ""), "")

    by_id: dict[str, PersonPromptContext] = {}
    for person_ids, entry in ids_by_entry:
        if not person_ids:
            continue
        inferred = _mapping(entry.get("inferred"))
        evidence = _mapping(inferred.get("evidence"))
        confirmed = _mapping(entry.get("confirmed"))
        role = _text(confirmed.get("role"))
        canonical_id = person_ids[0]
        mine = tuple(sorted(relationships.get(canonical_id, {}).values(), key=_relationship_key))
        relationship, relationship_source = _primary_relationship(
            canonical_id,
            owner_id,
            owner,
            role,
            mine,
        )
        owner_relationships = tuple(
            item
            for item in mine
            if item.target_id == owner_id and item.source in {"confirmed", "derived"}
        )
        birth_date = _text(entry.get("birth_date"))
        context = PersonPromptContext(
            person_ids=person_ids,
            name=str(entry.get("name") or "?"),
            role=role,
            tier=_text(inferred.get("tier")),
            birth_date=birth_date,
            relationship=relationship,
            relationship_source=relationship_source,
            first_month=_credible_first_month(_text(evidence.get("first_month")), birth_date),
            onset=_text(evidence.get("onset")),
            relationship_current=(
                relationship_source in {"confirmed", "derived"}
                and (bool(owner_relationships) or role is not None)
            ),
            owner_relationship_kinds=tuple(sorted({item.kind for item in owner_relationships})),
            relationships=mine,
        )
        for person_id in person_ids:
            by_id[person_id] = context
    return MappingProxyType(by_id)


def _relationships(
    ids_by_entry: list[tuple[tuple[str, ...], dict[str, Any]]],
    canonical_by_id: Mapping[str, str],
    names: Mapping[str, str],
    document: dict[str, Any],
    *,
    include_derived: bool,
) -> dict[str, dict[tuple[str, str], PersonPromptRelationship]]:
    found: dict[str, dict[tuple[str, str], PersonPromptRelationship]] = {}
    for person_ids, entry in ids_by_entry:
        if not person_ids:
            continue
        confirmed = _mapping(entry.get("confirmed"))
        raw_relationships = confirmed.get("links")
        if not isinstance(raw_relationships, list):
            continue
        for raw in raw_relationships:
            if (
                not isinstance(raw, dict)
                or not raw.get("with")
                or raw.get("decision", "confirmed") == "rejected"
            ):
                continue
            _add_relationship(
                found,
                person_ids[0],
                str(raw.get("kind") or "link"),
                str(raw["with"]),
                canonical_by_id,
                names,
                "confirmed",
            )

    if include_derived:
        for assumption in family_assumptions(document):
            _add_relationship(
                found,
                assumption.source_id,
                assumption.kind,
                assumption.target_id,
                canonical_by_id,
                names,
                "derived",
            )
            _add_relationship(
                found,
                assumption.target_id,
                assumption.reverse_kind,
                assumption.source_id,
                canonical_by_id,
                names,
                "derived",
            )
    return found


def _add_relationship(
    relationships: dict[str, dict[tuple[str, str], PersonPromptRelationship]],
    source_id: str,
    kind: str,
    target_id: str,
    canonical_by_id: Mapping[str, str],
    names: Mapping[str, str],
    source: Literal["confirmed", "derived"],
) -> None:
    canonical_source = canonical_by_id.get(source_id)
    canonical_target = canonical_by_id.get(target_id)
    if canonical_source is None or canonical_target is None:
        return
    mine = relationships.setdefault(canonical_source, {})
    key = kind, canonical_target
    standing = mine.get(key)
    if standing is None or standing.source == "derived" and source == "confirmed":
        mine[key] = PersonPromptRelationship(
            kind=kind,
            target_id=canonical_target,
            target_name=names[canonical_target],
            source=source,
        )


def _primary_relationship(
    person_id: str,
    owner_id: str,
    owner: Mapping[str, Any],
    role: str | None,
    relationships: tuple[PersonPromptRelationship, ...],
) -> tuple[str, Literal["owner", "confirmed", "derived", "unconfirmed"]]:
    if person_id == owner_id:
        return f"library owner ({owner.get('identified') or 'identified'})", "owner"
    if role:
        return role, "confirmed"
    to_owner = [item for item in relationships if item.target_id == owner_id]
    if to_owner:
        chosen = min(to_owner, key=lambda item: (item.source != "confirmed", item.kind))
        return f"{relationship_label(chosen.kind)} library owner", chosen.source
    return "unconfirmed", "unconfirmed"


def _relationship_key(item: PersonPromptRelationship) -> tuple[bool, str, str]:
    return item.source != "confirmed", item.target_name.casefold(), item.kind


def _credible_first_month(first_month: str | None, birth_date: str | None) -> str | None:
    """Exclude a face match that predates the person's recorded birth."""
    if not first_month or not birth_date:
        return first_month
    try:
        first = date.fromisoformat(f"{first_month}-01")
        born = date.fromisoformat(birth_date).replace(day=1)
    except ValueError:
        return first_month
    return first_month if first >= born else None


def _person_ids(entry: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for item in entry["ids"] if (value := _text(item))))


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
