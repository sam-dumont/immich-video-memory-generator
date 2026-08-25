"""The people file: what the graph writes down and what it must never touch.

Every person here is invented. The file this mirrors holds real names, which
is why it is gitignored and why no fixture in this repo resembles one.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import yaml

from immich_memories.people.companion import (
    default_people_path,
    load_document,
    people_entries,
    save_graph,
)
from immich_memories.people.graph import Owner, PeopleGraph, PersonNode
from immich_memories.people.signatures import Link, LinkKind, PersonEvidence, Tier


def _node(name: str, tier: Tier, count: int = 400, person_id: str | None = None) -> PersonNode:
    return PersonNode(
        evidence=PersonEvidence(
            person_id=person_id or f"id-{name.lower().replace(' ', '-')}",
            name=name,
            count=count,
            active_months=(date(2019, 1, 1), date(2019, 2, 1), date(2019, 3, 1)),
            birth_date=date(1990, 5, 6),
        ),
        tier=tier,
    )


def _graph(*nodes: PersonNode) -> PeopleGraph:
    return PeopleGraph(
        people=nodes,
        owner=Owner("id-alex-example", "Alex Example", "account"),
        built_at=datetime(2026, 8, 25, 9, 0, 0),
    )


class TestTheFile:
    def test_it_lives_beside_the_special_days_catalogue(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))

        assert default_people_path() == tmp_path / ".immich-memories" / "people.yaml"

    def test_a_fresh_scan_reserves_the_confirmed_fields_it_will_not_fill(self, tmp_path):
        path = tmp_path / "people.yaml"

        save_graph(path, _graph(_node("Alex Example", Tier.INNER)))

        entry = people_entries(load_document(path))[0]
        assert entry["confirmed"] == {"role": None, "links": []}
        assert entry["inferred"]["tier"] == "inner"

    def test_the_file_records_the_evidence_behind_each_reading(self, tmp_path):
        path = tmp_path / "people.yaml"

        save_graph(path, _graph(_node("Alex Example", Tier.RECURRING, count=402)))

        evidence = people_entries(load_document(path))[0]["inferred"]["evidence"]
        assert evidence["count"] == 402
        assert evidence["active_months"] == 3

    def test_an_unreadable_file_reads_as_no_file_at_all(self, tmp_path):
        path = tmp_path / "people.yaml"
        path.write_text("{{ not: yaml: at all")

        assert load_document(path) == {}

    def test_only_its_owner_can_read_it(self, tmp_path):
        path = tmp_path / "people.yaml"

        save_graph(path, _graph(_node("Alex Example", Tier.INNER)))

        assert path.stat().st_mode & 0o077 == 0


class TestConfirmedBeatsInferred:
    def test_a_confirmed_role_survives_a_refresh_that_disagrees(self, tmp_path):
        path = tmp_path / "people.yaml"
        save_graph(path, _graph(_node("Alex Example", Tier.EPISODIC)))
        _confirm(path, "id-alex-example", role="partner")

        save_graph(path, _graph(_node("Alex Example", Tier.EVENT)))

        entry = people_entries(load_document(path))[0]
        assert entry["confirmed"]["role"] == "partner"
        assert entry["inferred"]["tier"] == "event"

    def test_confirmed_links_survive_a_refresh_that_inferred_others(self, tmp_path):
        path = tmp_path / "people.yaml"
        save_graph(path, _graph(_node("Alex Example", Tier.INNER)))
        _confirm(path, "id-alex-example", links=[{"kind": "couple", "with": "id-sam-sample"}])

        refreshed = _node("Alex Example", Tier.INNER)
        refreshed = PersonNode(
            evidence=refreshed.evidence,
            tier=refreshed.tier,
            links=(Link(LinkKind.TIGHT_DYAD, refreshed.evidence.person_id, "id-kai", 0.4, "co"),),
        )
        save_graph(path, _graph(refreshed))

        entry = people_entries(load_document(path))[0]
        assert entry["confirmed"]["links"] == [{"kind": "couple", "with": "id-sam-sample"}]
        assert entry["inferred"]["links"][0]["with"] == "id-kai"

    def test_a_person_the_user_annotated_is_never_dropped_by_a_refresh(self, tmp_path):
        path = tmp_path / "people.yaml"
        save_graph(path, _graph(_node("Alex Example", Tier.INNER), _node("Sam Sample", Tier.EVENT)))
        _confirm(path, "id-sam-sample", role="friend")

        save_graph(path, _graph(_node("Alex Example", Tier.INNER)))

        by_name = {entry["name"]: entry for entry in people_entries(load_document(path))}
        assert by_name["Sam Sample"]["confirmed"]["role"] == "friend"

    def test_a_person_nobody_annotated_leaves_with_the_roster(self, tmp_path):
        path = tmp_path / "people.yaml"
        save_graph(path, _graph(_node("Alex Example", Tier.INNER), _node("Sam Sample", Tier.EVENT)))

        save_graph(path, _graph(_node("Alex Example", Tier.INNER)))

        assert [entry["name"] for entry in people_entries(load_document(path))] == ["Alex Example"]


class TestAHandEditedFile:
    def test_an_id_written_without_brackets_is_not_read_as_a_list_of_letters(self, tmp_path):
        # The file exists to be edited by hand, and `ids: 5f2c` without the
        # brackets is a string that iterates as characters everywhere after.
        path = tmp_path / "people.yaml"
        path.write_text("version: 1\npeople:\n  - ids: abc\n    name: Alex Example\n")

        assert people_entries(load_document(path)) == []

    def test_a_document_that_is_not_a_mapping_reads_as_nothing(self, tmp_path):
        path = tmp_path / "people.yaml"
        path.write_text("- just\n- a list\n")

        assert load_document(path) == {}


def _confirm(path: Path, person_id: str, **fields: object) -> None:
    """Stand in for the hand edit or the settings page that fills these in."""
    document = yaml.safe_load(path.read_text())
    for entry in document["people"]:
        if person_id in entry["ids"]:
            entry["confirmed"].update(fields)
    path.write_text(yaml.dump(document, sort_keys=False, allow_unicode=True))


class TestOneEntryPerPerson:
    def test_two_people_sharing_a_confirmed_block_do_not_become_yaml_anchors(self, tmp_path):
        # An entry that lists several ids — a person merged by hand, or the
        # cross-account identities to come — hands the same confirmed block to
        # two people. Written as one object, yaml emits `&id001`/`*id001`, and
        # a file meant to be hand-edited must not contain aliases: editing one
        # person silently edits the other, and deleting the anchor breaks both.
        path = tmp_path / "people.yaml"
        path.write_text(
            "version: 1\npeople:\n"
            "  - ids: [id-alex-example, id-sam-sample]\n"
            "    name: Alex Example\n"
            "    confirmed: {role: partner, links: []}\n"
        )

        save_graph(path, _graph(_node("Alex Example", Tier.INNER), _node("Sam Sample", Tier.INNER)))

        assert "&id" not in path.read_text()
        assert [entry["confirmed"]["role"] for entry in people_entries(load_document(path))] == [
            "partner",
            "partner",
        ]
