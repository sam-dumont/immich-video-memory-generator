"""The companion editor's model: what the page shows and what it writes back.

Every person here is invented. The file this mirrors holds the names of a real
household, which is why it is gitignored and why no fixture in this repo — or
in any issue or PR — resembles one.
"""

from __future__ import annotations

from pathlib import Path

from immich_memories.people.companion import load_document, people_entries
from immich_memories.people.editor import curation_flags, load_people, save_person


def _file(tmp_path: Path, *entries: str) -> Path:
    path = tmp_path / "people.yaml"
    path.write_text("version: 1\npeople:\n" + "".join(entries))
    return path


def _entry(
    name: str,
    person_id: str,
    *,
    tier: str = "recurring",
    count: int = 100,
    links: str = "[]",
    confirmed: str = "{role: null, links: [], notes: null}",
    birth_date: str = "null",
    counts_reliable: str = "true",
) -> str:
    return (
        f"  - ids: [{person_id}]\n"
        f"    name: {name}\n"
        f"    birth_date: {birth_date}\n"
        f"    inferred:\n"
        f"      tier: {tier}\n"
        f"      counts_reliable: {counts_reliable}\n"
        f"      evidence: {{count: {count}, active_months: 12, first_month: '2019-01',"
        f" last_month: '2021-06', span_years: 2.4, onset: '2019-03',"
        f" concentration: 8.3, continuity: 0.4}}\n"
        f"      links: {links}\n"
        f"    confirmed: {confirmed}\n"
    )


class TestTheRoster:
    def test_it_reads_the_inner_circle_first_and_the_busiest_of_each_tier_above(self, tmp_path):
        path = _file(
            tmp_path,
            _entry("Quiet Neighbour", "id-quiet", tier="event", count=300),
            _entry("Sam Sample", "id-sam", tier="recurring", count=90),
            _entry("Robin Placeholder", "id-robin", tier="recurring", count=410),
            _entry("Alex Example", "id-alex", tier="inner", count=120),
        )

        assert [person.name for person in load_people(path)] == [
            "Alex Example",
            "Robin Placeholder",
            "Sam Sample",
            "Quiet Neighbour",
        ]

    def test_a_person_carries_the_evidence_the_scan_read_them_from(self, tmp_path):
        path = _file(tmp_path, _entry("Alex Example", "id-alex", count=412))

        person = load_people(path)[0]

        assert person.count == 412
        assert "412 pictures" in person.evidence
        assert "12 months" in person.evidence
        assert "2019-03" in person.evidence

    def test_an_empty_file_is_an_empty_roster_rather_than_a_crash(self, tmp_path):
        assert load_people(tmp_path / "nothing.yaml") == []


class TestConfirmingARole:
    def test_a_confirmed_role_persists_through_a_rescan_that_disagrees(self, tmp_path):
        from datetime import date, datetime

        from immich_memories.people.companion import save_graph
        from immich_memories.people.graph import Owner, PeopleGraph, PersonNode
        from immich_memories.people.signatures import PersonEvidence, Tier

        path = tmp_path / "people.yaml"
        node = PersonNode(
            evidence=PersonEvidence("id-alex", "Alex Example", 120, (date(2019, 1, 1),)),
            tier=Tier.EPISODIC,
        )
        save_graph(path, PeopleGraph((node,), Owner(None, "Someone", "told"), datetime.now()))

        person = load_people(path)[0]
        person.role = "partner"
        save_person(path, person)

        # WHY not a mock: the rescan is the real writer, on a real file. That
        # the two write paths agree is the whole contract under test.
        save_graph(
            path,
            PeopleGraph(
                (PersonNode(evidence=node.evidence, tier=Tier.EVENT),),
                Owner(None, "Someone", "told"),
                datetime.now(),
            ),
        )

        assert load_people(path)[0].role == "partner"
        assert load_people(path)[0].tier == "event"

    def test_a_role_typed_by_hand_is_kept_as_typed(self, tmp_path):
        path = _file(tmp_path, _entry("Alex Example", "id-alex"))

        person = load_people(path)[0]
        person.role = "  godmother  "
        save_person(path, person)

        assert load_people(path)[0].role == "godmother"

    def test_clearing_a_role_puts_the_field_back_to_empty(self, tmp_path):
        path = _file(tmp_path, _entry("Alex Example", "id-alex", confirmed="{role: friend}"))

        person = load_people(path)[0]
        person.role = ""
        save_person(path, person)

        assert load_people(path)[0].role is None


class TestDecidingOnALink:
    def test_rejecting_an_inferred_link_is_recorded_as_a_rejection(self, tmp_path):
        path = _file(
            tmp_path,
            _entry(
                "Alex Example",
                "id-alex",
                links="[{kind: tight-dyad, with: id-robin, confidence: 0.51, via: co-occurrence}]",
            ),
            _entry("Robin Placeholder", "id-robin"),
        )

        person = load_people(path)[0]
        person.links[0].decision = "rejected"
        save_person(path, person)

        written = people_entries(load_document(path))[0]["confirmed"]["links"]
        assert written == [{"kind": "tight-dyad", "with": "id-robin", "decision": "rejected"}]
        assert load_people(path)[0].links[0].decision == "rejected"

    def test_an_undecided_link_writes_nothing_into_the_confirmed_block(self, tmp_path):
        path = _file(
            tmp_path,
            _entry(
                "Alex Example",
                "id-alex",
                links="[{kind: twin, with: id-robin, confidence: 0.9, via: birth-date}]",
            ),
        )

        person = load_people(path)[0]
        save_person(path, person)

        assert people_entries(load_document(path))[0]["confirmed"]["links"] == []

    def test_a_link_names_the_person_on_its_other_end(self, tmp_path):
        path = _file(
            tmp_path,
            _entry(
                "Alex Example",
                "id-alex",
                links="[{kind: twin, with: id-robin, confidence: 0.9, via: birth-date}]",
            ),
            _entry("Robin Placeholder", "id-robin"),
        )

        assert load_people(path)[0].links[0].target_name == "Robin Placeholder"

    def test_a_link_confirmed_by_hand_is_not_dropped_by_the_editor(self, tmp_path):
        # The file is hand-editable and documents this shape. An editor that
        # only knew about links the scan inferred would delete it on save.
        path = _file(
            tmp_path,
            _entry(
                "Alex Example",
                "id-alex",
                confirmed="{role: null, links: [{kind: couple, with: id-robin}], notes: null}",
            ),
        )

        person = load_people(path)[0]
        person.role = "partner"
        save_person(path, person)

        written = people_entries(load_document(path))[0]["confirmed"]["links"]
        assert written == [{"kind": "couple", "with": "id-robin", "decision": "confirmed"}]

    def test_two_relationships_to_the_same_person_remain_distinct(self, tmp_path):
        path = _file(
            tmp_path,
            _entry(
                "Alex Example",
                "id-alex",
                confirmed=(
                    "{role: null, links: [{kind: sibling-of, with: id-robin}, "
                    "{kind: twin-of, with: id-robin}], notes: null}"
                ),
            ),
            _entry("Robin Placeholder", "id-robin"),
        )

        links = load_people(path)[0].links

        assert [(link.kind, link.target_name) for link in links] == [
            ("sibling-of", "Robin Placeholder"),
            ("twin-of", "Robin Placeholder"),
        ]


class TestNotes:
    def test_a_note_survives_the_round_trip(self, tmp_path):
        path = _file(tmp_path, _entry("Alex Example", "id-alex"))

        person = load_people(path)[0]
        person.notes = "met at the running club"
        save_person(path, person)

        assert load_people(path)[0].notes == "met at the running club"


class TestCurationFlags:
    def test_twins_are_flagged_once_for_the_pair_with_their_counts_disowned(self, tmp_path):
        path = _file(
            tmp_path,
            _entry(
                "Robin Placeholder",
                "id-robin",
                counts_reliable="false",
                links="[{kind: twin, with: id-remy, confidence: 0.9, via: birth-date}]",
            ),
            _entry(
                "Remy Placeholder",
                "id-remy",
                counts_reliable="false",
                links="[{kind: twin, with: id-robin, confidence: 0.9, via: birth-date}]",
            ),
        )

        flags = curation_flags(load_people(path))

        assert len(flags) == 1
        assert flags[0].kind == "twin"
        assert set(flags[0].names) == {"Robin Placeholder", "Remy Placeholder"}

    def test_one_name_on_two_records_is_flagged_as_a_merge_for_immich(self, tmp_path):
        path = _file(
            tmp_path,
            _entry(
                "Alex Example",
                "id-alex",
                links="[{kind: duplicate, with: id-alex-2, confidence: 0.6, via: name}]",
            ),
            _entry(
                "Alex Example",
                "id-alex-2",
                links="[{kind: duplicate, with: id-alex, confidence: 0.6, via: name}]",
            ),
        )

        flags = curation_flags(load_people(path))

        assert [flag.kind for flag in flags] == ["duplicate"]
        assert "Immich" in flags[0].message

    def test_each_flagged_name_is_paired_with_that_persons_own_record(self, tmp_path):
        # The page turns these into one "open in Immich" link each. Sorting the
        # ids for the dedup key and reading them back as if they matched the
        # names would send somebody to the wrong person's record.
        path = _file(
            tmp_path,
            _entry(
                "Zoe Placeholder",
                "id-aaa",
                links="[{kind: twin, with: id-zzz, confidence: 0.9, via: birth-date}]",
            ),
            _entry(
                "Alex Placeholder",
                "id-zzz",
                links="[{kind: twin, with: id-aaa, confidence: 0.9, via: birth-date}]",
            ),
        )

        flag = curation_flags(load_people(path))[0]

        assert dict(zip(flag.names, flag.person_ids, strict=True)) == {
            "Zoe Placeholder": "id-aaa",
            "Alex Placeholder": "id-zzz",
        }

    def test_saying_they_are_not_twins_stops_the_page_asking(self, tmp_path):
        # Two people can share a surname and a birthday without being twins.
        # A flag you have already answered is nagging, not curation.
        path = _file(
            tmp_path,
            _entry(
                "Robin Placeholder",
                "id-robin",
                links="[{kind: twin, with: id-remy, confidence: 0.9, via: birth-date}]",
            ),
            _entry(
                "Remy Placeholder",
                "id-remy",
                links="[{kind: twin, with: id-robin, confidence: 0.9, via: birth-date}]",
            ),
        )
        robin = load_people(path)[0]
        robin.links[0].decision = "rejected"
        save_person(path, robin)

        assert curation_flags(load_people(path)) == []

    def test_a_tidy_roster_raises_no_flags(self, tmp_path):
        path = _file(tmp_path, _entry("Alex Example", "id-alex"))

        assert curation_flags(load_people(path)) == []


class TestTheWritePathIsSharedWithTheScan:
    def test_the_editor_writes_a_file_only_its_owner_can_read(self, tmp_path):
        path = _file(tmp_path, _entry("Alex Example", "id-alex"))
        path.chmod(0o644)

        person = load_people(path)[0]
        person.role = "friend"
        save_person(path, person)

        assert path.stat().st_mode & 0o077 == 0
