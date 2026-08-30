"""Editorial people context follows the graph without rewriting it."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from probe_people_context import load_person_facts


def _write_family(path, *, casey_role=None, casey_birth_date=None, casey_first_month=None) -> bytes:
    document = {
        "owner": {"person_id": "alex", "name": "Alex Example", "identified": "told"},
        "people": [
            {
                "ids": ["alex"],
                "name": "Alex Example",
                "birth_date": None,
                "inferred": {"tier": "inner", "evidence": {}},
                "confirmed": {
                    "role": None,
                    "links": [{"kind": "sibling-of", "with": "robin", "decision": "confirmed"}],
                    "notes": None,
                },
            },
            {
                "ids": ["robin"],
                "name": "Robin Example",
                "birth_date": None,
                "inferred": {"tier": "inner", "evidence": {}},
                "confirmed": {
                    "role": "sibling",
                    "links": [
                        {"kind": "sibling-of", "with": "alex", "decision": "confirmed"},
                        {"kind": "parent-of", "with": "casey", "decision": "confirmed"},
                    ],
                    "notes": None,
                },
            },
            {
                "ids": ["casey"],
                "name": "Casey Example",
                "birth_date": casey_birth_date,
                "inferred": {
                    "tier": "inner",
                    "evidence": {"first_month": casey_first_month},
                },
                "confirmed": {
                    "role": casey_role,
                    "links": [{"kind": "child-of", "with": "robin", "decision": "confirmed"}],
                    "notes": None,
                },
            },
        ],
    }
    raw = yaml.safe_dump(document, sort_keys=False).encode()
    path.write_bytes(raw)
    return raw


def test_owner_relative_kinship_is_derived_at_read_time_without_touching_the_file(tmp_path):
    path = tmp_path / "people.yaml"
    before = _write_family(path)

    facts = load_person_facts(path, include_derived=True)

    casey = facts["Casey Example"]
    assert casey.relationship == "niece or nephew of library owner"
    assert casey.relationship_source == "derived"
    assert casey.relationship_current is True
    assert casey.owner_relationship_kinds == ("nibling-of",)
    assert path.read_bytes() == before


def test_an_explicit_role_beats_the_derived_owner_relative_role(tmp_path):
    path = tmp_path / "people.yaml"
    _write_family(path, casey_role="godchild")

    casey = load_person_facts(path, include_derived=True)["Casey Example"]

    assert (casey.relationship, casey.relationship_source) == ("godchild", "confirmed")
    assert casey.relationship_current is True
    assert any(
        link.target_id == "alex" and link.kind == "nibling-of" and link.source == "derived"
        for link in casey.links
    )


def test_the_derived_layer_is_optional_and_off_by_default(tmp_path):
    path = tmp_path / "people.yaml"
    _write_family(path)

    casey = load_person_facts(path)["Casey Example"]

    assert (casey.relationship, casey.relationship_source) == ("unconfirmed", "unconfirmed")
    assert casey.relationship_current is False
    assert all(link.source != "derived" for link in casey.links)


def test_a_face_match_from_before_the_recorded_birth_is_not_prompt_context(tmp_path):
    path = tmp_path / "people.yaml"
    _write_family(
        path,
        casey_birth_date="2024-02-07",
        casey_first_month="2015-07",
    )

    casey = load_person_facts(path)["Casey Example"]

    assert casey.first_month is None
