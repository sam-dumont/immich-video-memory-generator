"""The companion editor's model — the people file as a page can show it.

The settings page renders these and writes them back; none of it knows about
NiceGUI, which is what lets the confirm flow be tested on a real file rather
than through a browser. The rule the whole module exists to serve is the file's
own: the user's answer is the answer, so nothing here ever discards a
`confirmed:` field it did not understand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from immich_memories.people.companion import (
    add_confirmed_person,
    load_document,
    people_entries,
    remove_confirmed_relationship,
    save_confirmed,
    save_confirmed_relationship,
)
from immich_memories.people.relationships import RELATIONSHIP_CHOICES
from immich_memories.people.signatures import Tier, pair_key

# What inference can suggest, and nothing more. A role the graph cannot propose
# is a role only the user can name, and the free-text field is where they do
# it — inventing a relationship vocabulary here would put words in their mouth.
ROLE_SUGGESTIONS = (
    "partner",
    "child",
    "parent",
    "sibling",
    "family",
    "friend",
    "acquaintance",
)

# Declaration order in the enum is closeness order, and the roster is drawn in
# it — inner circle first. Derived rather than retyped so the two cannot drift.
TIER_ORDER = tuple(tier.value for tier in Tier)

CONFIRMED = "confirmed"
REJECTED = "rejected"

_GONE = "someone no longer in the file"

_LINK_PROMPTS = {
    "tight-dyad": "appears in a large share of their pictures, both ways",
    "twin": "same family name and birth date",
    "duplicate": "the same name on a second person record",
}

_FLAG_MESSAGES = {
    "twin": (
        "Face recognition merges identical faces, so one of these records holds "
        "nearly all the pictures and the other almost none. Neither count means "
        "anything on its own. Merge them in Immich or keep them apart — your call."
    ),
    "duplicate": (
        "One name on two person records — a split face cluster. Merge these "
        "records in Immich; it is the only place it can be fixed."
    ),
}


@dataclass
class LinkView:
    """One edge, and what the user has said about it."""

    kind: str
    target_id: str
    target_name: str
    confidence: float
    via: str
    inferred: bool
    decision: str | None = None
    reverse_kind: str | None = None

    @property
    def prompt(self) -> str:
        return _LINK_PROMPTS.get(self.kind, self.via)


@dataclass
class PersonView:
    """One person as the editor shows them: the reading, and the user's answer."""

    person_id: str
    name: str
    birth_date: str | None
    tier: str
    count: int
    counts_reliable: bool
    evidence: str
    links: list[LinkView] = field(default_factory=list)
    role: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class CurationFlag:
    """Something only Immich can fix, surfaced where the user is already looking.

    `names` and `person_ids` are in the same order, because the page turns them
    into one "open this person in Immich" link each and sending somebody to the
    wrong record is worse than not offering the link.
    """

    kind: str
    names: tuple[str, ...]
    person_ids: tuple[str, ...]
    message: str


def load_people(path: Path) -> list[PersonView]:
    """The people file as a roster, inner circle first and busiest first within.

    A missing or damaged file reads as an empty roster: somebody opening the
    page before their first scan should be told to run one, not shown a stack
    trace.
    """
    entries = people_entries(load_document(path))
    names = _names_by_id(entries)
    people = [_view(entry, names) for entry in entries]
    return sorted(people, key=lambda person: (_tier_rank(person.tier), -person.count))


def save_person(path: Path, person: PersonView) -> None:
    """Write one person's answers back, through the file's own writer.

    Only decided links are written down. An edge nobody has answered yet is
    the graph's opinion, and the graph's opinions live under `inferred:`.
    """
    save_confirmed(
        path,
        person.person_id,
        {
            "role": _cleaned(person.role),
            "links": [_confirmed_link_block(link) for link in person.links if link.decision],
            "notes": _cleaned(person.notes),
        },
    )


def _confirmed_link_block(link: LinkView) -> dict[str, str]:
    block = {
        "kind": link.kind,
        "with": link.target_id,
        "decision": str(link.decision),
    }
    if link.reverse_kind:
        block["reverse"] = link.reverse_kind
    return block


def add_person(path: Path, name: str) -> str:
    """Add an off-camera or not-yet-tagged person from the settings page."""
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("A person needs a name")
    return add_confirmed_person(path, cleaned)


def add_relationship(path: Path, source_id: str, kind: str, target_id: str) -> None:
    """Save one human answer; the file writer maintains its reciprocal."""
    valid = {choice.kind for choice in RELATIONSHIP_CHOICES}
    if kind not in valid:
        raise ValueError(f"Unknown relationship kind: {kind}")
    save_confirmed_relationship(path, source_id, kind, target_id)


def remove_relationship(path: Path, source_id: str, kind: str, target_id: str) -> None:
    """Remove one relationship created by the user and its reciprocal."""
    remove_confirmed_relationship(path, source_id, kind, target_id)


def curation_flags(people: list[PersonView]) -> list[CurationFlag]:
    """The pairs the user has to go and fix in Immich, one flag per pair.

    Links are stored from both ends, so the same twin pair arrives twice; the
    pair key is what collapses them into the one prompt a person should see.
    A pair somebody has rejected raises nothing — two people can share a
    surname and a birthday without being twins, or a name without being one
    person, and a flag that has been answered is nagging rather than curation.
    """
    names = {person.person_id: person.name for person in people}
    seen = _rejected_pairs(people)
    flags: list[CurationFlag] = []
    for person in people:
        for link in person.links:
            if link.kind not in _FLAG_MESSAGES or link.target_id not in names:
                continue
            pair = pair_key(person.person_id, link.target_id)
            if pair in seen:
                continue
            seen.add(pair)
            flags.append(
                CurationFlag(
                    kind=link.kind,
                    names=(person.name, names[link.target_id]),
                    person_ids=(person.person_id, link.target_id),
                    message=_FLAG_MESSAGES[link.kind],
                )
            )
    return flags


def _rejected_pairs(people: list[PersonView]) -> set[tuple[str, str]]:
    """Pairs somebody has already said no to — seeded into the seen set."""
    return {
        pair_key(person.person_id, link.target_id)
        for person in people
        for link in person.links
        if link.decision == REJECTED
    }


def _view(entry: dict[str, Any], names: dict[str, str]) -> PersonView:
    inferred = _mapping(entry.get("inferred"))
    confirmed = _mapping(entry.get(CONFIRMED))
    evidence = _mapping(inferred.get("evidence"))
    return PersonView(
        person_id=str(entry["ids"][0]),
        name=str(entry.get("name") or "?"),
        birth_date=_text(entry.get("birth_date")),
        tier=str(inferred.get("tier") or ""),
        count=int(evidence.get("count") or 0),
        counts_reliable=inferred.get("counts_reliable", True) is not False,
        evidence=_evidence_line(evidence),
        links=_links(inferred, confirmed, names),
        role=_text(confirmed.get("role")),
        notes=_text(confirmed.get("notes")),
    )


def _links(
    inferred: dict[str, Any], confirmed: dict[str, Any], names: dict[str, str]
) -> list[LinkView]:
    """Every edge this person has, whether the scan found it or the user wrote it.

    A link somebody typed into the file by hand has no inferred counterpart, and
    an editor that only rendered what the scan found would delete it the next
    time the user pressed a button on this person.
    """
    decisions = _decisions(confirmed)
    views = [
        _link_view(link, names, decisions)
        for link in _mappings(inferred.get("links"))
        if link.get("with")
    ]
    known = {(view.kind, view.target_id) for view in views}
    views.extend(
        _hand_written_link(link, names)
        for link in _mappings(confirmed.get("links"))
        if link.get("with") and (str(link.get("kind") or "link"), str(link["with"])) not in known
    )
    return views


def _link_view(
    raw: dict[str, Any], names: dict[str, str], decisions: dict[tuple[str, str], str]
) -> LinkView:
    target_id = str(raw["with"])
    kind = str(raw.get("kind") or "link")
    return LinkView(
        kind=kind,
        target_id=target_id,
        target_name=names.get(target_id, _GONE),
        confidence=float(raw.get("confidence") or 0.0),
        via=str(raw.get("via") or ""),
        inferred=True,
        decision=decisions.get((kind, target_id)),
        reverse_kind=None,
    )


def _hand_written_link(raw: dict[str, Any], names: dict[str, str]) -> LinkView:
    """An edge nobody inferred, because somebody wrote it into the file."""
    target_id = str(raw["with"])
    return LinkView(
        kind=str(raw.get("kind") or "link"),
        target_id=target_id,
        target_name=names.get(target_id, _GONE),
        confidence=0.0,
        via="you",
        inferred=False,
        decision=str(raw.get("decision") or CONFIRMED),
        reverse_kind=_text(raw.get("reverse")),
    )


def _decisions(confirmed: dict[str, Any]) -> dict[tuple[str, str], str]:
    """What the user said about each relationship kind and person pair.

    A link written by hand carries no `decision:` — the documented shape is
    just kind and target — and writing it down at all is the confirmation.
    """
    return {
        (str(link.get("kind") or "link"), str(link["with"])): str(link.get("decision") or CONFIRMED)
        for link in _mappings(confirmed.get("links"))
        if link.get("with")
    }


def _evidence_line(evidence: dict[str, Any]) -> str:
    count = evidence.get("count") or 0
    months = evidence.get("active_months") or 0
    since = evidence.get("onset") or evidence.get("first_month")
    line = f"{count} pictures across {months} months"
    return f"{line}, here since {since}" if since else line


def _names_by_id(entries: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(person_id): str(entry.get("name") or "?")
        for entry in entries
        for person_id in entry["ids"]
    }


def _tier_rank(tier: str) -> int:
    return TIER_ORDER.index(tier) if tier in TIER_ORDER else len(TIER_ORDER)


def _cleaned(value: str | None) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _text(value: object) -> str | None:
    return str(value) if value not in (None, "") else None


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _mappings(value: object) -> list[dict[str, Any]]:
    """A hand-edited list read as mappings, skipping whatever else is in there."""
    return [_mapping(item) for item in value] if isinstance(value, list) else []
