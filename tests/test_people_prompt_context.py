"""Production people context for editorial annotation prompts."""

from __future__ import annotations

import operator
from dataclasses import FrozenInstanceError

import pytest
import yaml

from immich_memories.people.context import load_people_prompt_context


def test_every_merged_immich_id_resolves_the_same_person_context(tmp_path):
    path = tmp_path / "people.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "people": [
                    {
                        "ids": ["person-current", "person-merged"],
                        "name": "Taylor Example",
                        "birth_date": "1992-06-14",
                        "inferred": {"tier": "inner", "evidence": {}},
                        "confirmed": {"role": "friend", "links": []},
                    }
                ]
            },
            sort_keys=False,
        )
    )

    context = load_people_prompt_context(path)

    assert tuple(context) == ("person-current", "person-merged")
    assert context["person-current"] is context["person-merged"]
    assert context["person-current"].person_ids == ("person-current", "person-merged")
    assert context["person-current"].role == "friend"
    assert context["person-current"].tier == "inner"
    assert context["person-current"].birth_date == "1992-06-14"


def test_confirmed_and_derived_relationships_keep_their_provenance(tmp_path):
    path = tmp_path / "people.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "owner": {
                    "person_id": "owner-merged",
                    "name": "Alex Example",
                    "identified": "account",
                },
                "people": [
                    {
                        "ids": ["owner-current", "owner-merged"],
                        "name": "Alex Example",
                        "inferred": {"tier": "inner", "evidence": {}},
                        "confirmed": {
                            "role": None,
                            "links": [
                                {
                                    "kind": "sibling-of",
                                    "with": "parent-current",
                                    "decision": "confirmed",
                                }
                            ],
                        },
                    },
                    {
                        "ids": ["parent-current"],
                        "name": "Robin Example",
                        "inferred": {"tier": "inner", "evidence": {}},
                        "confirmed": {
                            "role": "sibling",
                            "links": [
                                {
                                    "kind": "sibling-of",
                                    "with": "owner-current",
                                    "decision": "confirmed",
                                },
                                {
                                    "kind": "parent-of",
                                    "with": "child-current",
                                    "decision": "confirmed",
                                },
                            ],
                        },
                    },
                    {
                        "ids": ["child-current", "child-merged"],
                        "name": "Casey Example",
                        "birth_date": "2020-02-07",
                        "inferred": {
                            "tier": "recurring",
                            "evidence": {"first_month": "2020-03", "onset": "2020-04"},
                        },
                        "confirmed": {
                            "role": None,
                            "links": [
                                {
                                    "kind": "child-of",
                                    "with": "parent-current",
                                    "decision": "confirmed",
                                }
                            ],
                        },
                    },
                ],
            },
            sort_keys=False,
        )
    )

    casey = load_people_prompt_context(path, include_derived=True)["child-merged"]

    assert casey.relationship == "niece or nephew of library owner"
    assert casey.relationship_source == "derived"
    assert casey.relationship_current is True
    assert casey.owner_relationship_kinds == ("nibling-of",)
    assert casey.first_month == "2020-03"
    assert casey.onset == "2020-04"
    assert {(item.kind, item.target_id, item.source) for item in casey.relationships} == {
        ("child-of", "parent-current", "confirmed"),
        ("nibling-of", "owner-current", "derived"),
    }


def test_the_lookup_and_each_person_context_are_immutable(tmp_path):
    path = tmp_path / "people.yaml"
    path.write_text(
        "people:\n"
        "  - ids: [person-one]\n"
        "    name: Taylor Example\n"
        "    inferred: {tier: inner, evidence: {}}\n"
        "    confirmed: {role: friend, links: []}\n"
    )
    context = load_people_prompt_context(path)
    person = context["person-one"]

    with pytest.raises(TypeError):
        operator.setitem(context, "person-two", person)
    attribute = "role"
    with pytest.raises(FrozenInstanceError):
        setattr(person, attribute, "sibling")


def test_a_face_match_before_the_recorded_birth_is_not_prompt_context(tmp_path):
    path = tmp_path / "people.yaml"
    path.write_text(
        "people:\n"
        "  - ids: [person-one]\n"
        "    name: Taylor Example\n"
        "    birth_date: 2024-02-07\n"
        "    inferred:\n"
        "      tier: inner\n"
        "      evidence: {first_month: 2015-07, onset: 2024-03}\n"
        "    confirmed: {role: child, links: []}\n"
    )

    person = load_people_prompt_context(path)["person-one"]

    assert person.birth_date == "2024-02-07"
    assert person.first_month is None
    assert person.onset == "2024-03"


def test_a_confirmed_role_beats_an_owner_relative_relationship(tmp_path):
    path = tmp_path / "people.yaml"
    path.write_text(
        "owner: {person_id: owner-one, identified: account}\n"
        "people:\n"
        "  - ids: [owner-one]\n"
        "    name: Alex Example\n"
        "    inferred: {tier: inner, evidence: {}}\n"
        "    confirmed: {role: null, links: []}\n"
        "  - ids: [person-one]\n"
        "    name: Taylor Example\n"
        "    inferred: {tier: inner, evidence: {}}\n"
        "    confirmed:\n"
        "      role: godchild\n"
        "      links:\n"
        "        - {kind: friend-of, with: owner-one, decision: confirmed}\n"
    )

    person = load_people_prompt_context(path)["person-one"]

    assert (person.relationship, person.relationship_source) == ("godchild", "confirmed")
    assert person.relationships[0].source == "confirmed"
