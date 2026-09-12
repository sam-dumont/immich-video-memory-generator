"""The generated, refreshable measurements behind relationship review."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from immich_memories.security import write_secret_file

if TYPE_CHECKING:
    from immich_memories.people.graph import PeopleGraph, PersonNode

SCHEMA_VERSION = 1


def default_evidence_graph_path(people_path: Path | None = None) -> Path:
    """The machine-owned graph beside the hand-editable people file."""
    if people_path is None:
        from immich_memories.people.companion import default_people_path

        people_path = default_people_path()
    return people_path.with_name("people-graph.json")


def save_evidence_graph(
    path: Path,
    graph: PeopleGraph,
    people_document: dict[str, Any] | None = None,
) -> None:
    """Write measurements and labelled confirmed-path derivations."""
    names = {node.evidence.person_id: node.evidence.name for node in graph.people}
    document = {
        "version": SCHEMA_VERSION,
        "generated": (graph.built_at or datetime.now()).isoformat(timespec="seconds"),
        "owner": _owner_block(graph),
        "nodes": [_node_block(node) for node in graph.people],
        "edges": [
            {
                "one_id": edge.one_id,
                "one_name": names[edge.one_id],
                "other_id": edge.other_id,
                "other_name": names[edge.other_id],
                "shared_assets": edge.shared_assets,
                "one_share": round(edge.one_share, 4),
                "other_share": round(edge.other_share, 4),
            }
            for edge in graph.cooccurrences
        ],
        "derived_relationships": _derived_relationships(people_document),
    }
    write_secret_file(path, json.dumps(document, ensure_ascii=False, indent=2) + "\n")


def _derived_relationships(document: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Confirmed-path closure, labelled and kept outside the confirmed file."""
    if document is None:
        return []

    from immich_memories.people.assumptions import family_assumptions
    from immich_memories.people.companion import people_entries

    names = {
        str(person_id): str(entry.get("name") or "?")
        for entry in people_entries(document)
        for person_id in entry["ids"]
    }
    return [
        {
            "source_id": assumption.source_id,
            "source_name": names.get(assumption.source_id, "?"),
            "kind": assumption.kind,
            "target_id": assumption.target_id,
            "target_name": names.get(assumption.target_id, "?"),
            "reverse_kind": assumption.reverse_kind,
            "provenance": "confirmed-graph-closure",
            "evidence": [
                {
                    "source_id": step.source_id,
                    "source_name": names.get(step.source_id, "?"),
                    "kind": step.kind,
                    "target_id": step.target_id,
                    "target_name": names.get(step.target_id, "?"),
                }
                for step in assumption.evidence
            ],
        }
        for assumption in family_assumptions(document)
    ]


def _node_block(node: PersonNode) -> dict[str, Any]:
    person = node.evidence
    given_name, family_name = _name_parts(person.name)
    return {
        "id": person.person_id,
        "name": person.name,
        "given_name": given_name,
        "family_name": family_name,
        "name_parts_source": "display-name-heuristic",
        "birth_date": _iso(person.birth_date),
        "count": person.count,
        "active_months": person.month_count,
        "first_month": _month(person.first_month),
        "last_month": _month(person.last_month),
        "onset": _month(person.onset),
        "tier": node.tier.value,
    }


def _owner_block(graph: PeopleGraph) -> dict[str, Any] | None:
    if graph.owner is None:
        return None
    return {
        "person_id": graph.owner.person_id,
        "name": graph.owner.name,
        "identified": graph.owner.identified,
    }


def _name_parts(name: str) -> tuple[str, str | None]:
    """Split a display name without pretending Immich supplied structured names."""
    parts = name.split()
    if len(parts) < 2:
        return name, None
    return " ".join(parts[:-1]), parts[-1]


def _month(value: date | None) -> str | None:
    return f"{value:%Y-%m}" if value else None


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None
