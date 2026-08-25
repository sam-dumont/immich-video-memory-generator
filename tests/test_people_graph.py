"""Building the graph out of what Immich answers.

The library behind this is invented from scratch — three households of people
who do not exist, with round numbers chosen to sit either side of the measured
thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from immich_memories.people.graph import build_graph, identify_owner
from immich_memories.people.signatures import LinkKind, PersonEvidence, Tier


@dataclass
class _Person:
    id: str
    name: str
    birth_date: date | None = None


@dataclass
class _Bucket:
    time_bucket: str
    count: int


@dataclass
class _Account:
    name: str


class FakeImmich:
    """A library that answers the four questions the graph asks of Immich.

    # WHY: this replaces the Immich HTTP API, the only boundary the builder
    # touches. Everything else under test is arithmetic on its answers.
    """

    def __init__(self, people, months, shared=None, account="Alex Example"):
        self._people = people
        self._months = months
        self._shared = shared or {}
        self._account = account
        self.pair_queries = 0

    def get_all_people(self, with_hidden: bool = False):
        return self._people

    def get_time_buckets(self, **kwargs):
        person_id = kwargs["person_id"]
        return [
            _Bucket(f"{month:%Y-%m-%d}T00:00:00.000Z", count)
            for month, count in self._months.get(person_id, {}).items()
        ]

    def count_assets_with_people(self, person_ids):
        self.pair_queries += 1
        return self._shared.get(frozenset(person_ids), 0)

    def get_current_user(self):
        return _Account(self._account)


def _monthly(start: date, count: int, per_month: int) -> dict[date, int]:
    months = {}
    for index in range(count):
        month = date(
            start.year + (start.month - 1 + index) // 12, (start.month - 1 + index) % 12 + 1, 1
        )
        months[month] = per_month
    return months


class TestRoster:
    def test_a_named_person_becomes_their_count_and_their_months(self):
        immich = FakeImmich(
            people=[_Person("p1", "Alex Example")],
            months={"p1": _monthly(date(2019, 1, 1), 12, 10)},
        )

        graph = build_graph(immich, today=date(2026, 8, 25))

        alex = graph.people[0].evidence
        assert alex.count == 120
        assert alex.month_count == 12

    def test_faces_nobody_named_are_left_out_of_the_graph(self):
        immich = FakeImmich(
            people=[_Person("p1", ""), _Person("p2", "Alex Example")],
            months={
                "p1": _monthly(date(2019, 1, 1), 40, 50),
                "p2": _monthly(date(2019, 1, 1), 12, 10),
            },
        )

        graph = build_graph(immich, today=date(2026, 8, 25))

        assert [node.evidence.name for node in graph.people] == ["Alex Example"]

    def test_a_person_the_library_barely_holds_is_below_the_roster_bound(self):
        immich = FakeImmich(
            people=[_Person("p1", "Alex Example"), _Person("p2", "Sasha Example")],
            months={"p1": _monthly(date(2019, 1, 1), 12, 10), "p2": {date(2020, 5, 1): 8}},
        )

        graph = build_graph(immich, min_assets=25, today=date(2026, 8, 25))

        assert [node.evidence.name for node in graph.people] == ["Alex Example"]

    def test_epoch_buckets_from_broken_exif_never_reach_the_evidence(self):
        immich = FakeImmich(
            people=[_Person("p1", "Alex Example")],
            months={"p1": {date(1970, 1, 1): 4, **_monthly(date(2019, 1, 1), 12, 10)}},
        )

        graph = build_graph(immich, today=date(2026, 8, 25))

        assert graph.people[0].evidence.first_month == date(2019, 1, 1)


class TestOwner:
    def test_the_immich_account_name_picks_the_owner_out_of_the_roster(self):
        immich = FakeImmich(people=[], months={}, account="Alex Example")
        roster = [PersonEvidence("p1", "Alex Example", 400, (date(2019, 1, 1),))]

        owner = identify_owner(immich, roster)

        assert (owner.person_id, owner.identified) == ("p1", "account")

    def test_being_told_who_the_owner_is_beats_the_account_name(self):
        immich = FakeImmich(people=[], months={}, account="Alex Example")
        roster = [
            PersonEvidence("p1", "Alex Example", 400, (date(2019, 1, 1),)),
            PersonEvidence("p2", "Sam Sample", 300, (date(2019, 1, 1),)),
        ]

        owner = identify_owner(immich, roster, owner_name="Sam Sample")

        assert (owner.person_id, owner.identified) == ("p2", "told")

    def test_an_account_nobody_tagged_falls_back_to_an_inference_that_says_so(self):
        immich = FakeImmich(people=[], months={}, account="admin")
        roster = [
            PersonEvidence("p1", "Alex Example", 4000, tuple(_monthly(date(2008, 1, 1), 200, 20))),
            PersonEvidence("p2", "Sam Sample", 900, tuple(_monthly(date(2020, 1, 1), 40, 20))),
        ]

        owner = identify_owner(immich, roster)

        assert (owner.person_id, owner.identified) == ("p1", "inferred")


class TestLinksOnTheGraph:
    def test_the_owner_s_partner_is_found_without_a_single_shared_frame(self):
        # The owner holds the camera, so no query would find these two
        # together. Their month curves are the same curve all the same.
        immich = FakeImmich(
            people=[_Person("p1", "Alex Example"), _Person("p2", "Sam Sample")],
            months={
                "p1": _monthly(date(2010, 1, 1), 190, 20),
                "p2": _monthly(date(2018, 6, 1), 90, 20),
            },
            shared={},
        )

        graph = build_graph(immich, today=date(2026, 8, 25))

        owner_node = next(node for node in graph.people if node.evidence.person_id == "p1")
        assert [(link.kind, link.target_id, link.via) for link in owner_node.links] == [
            (LinkKind.TIGHT_DYAD, "p2", "curve-pairing")
        ]

    def test_no_pair_query_is_spent_on_a_pair_containing_the_owner(self):
        immich = FakeImmich(
            people=[_Person("p1", "Alex Example"), _Person("p2", "Sam Sample")],
            months={
                "p1": _monthly(date(2010, 1, 1), 190, 20),
                "p2": _monthly(date(2018, 6, 1), 90, 20),
            },
        )

        build_graph(immich, today=date(2026, 8, 25))

        assert immich.pair_queries == 0

    def test_a_twin_is_read_as_the_pair_they_were_split_from(self):
        immich = FakeImmich(
            people=[
                _Person("p1", "Alex Example"),
                _Person("p2", "Robin Example", date(2024, 3, 4)),
                _Person("p3", "Wren Example", date(2024, 3, 4)),
            ],
            months={
                "p1": _monthly(date(2010, 1, 1), 190, 20),
                "p2": _monthly(date(2024, 3, 1), 29, 20),
                "p3": {date(2026, 6, 1): 15, date(2026, 7, 1): 15},
            },
            account="Alex Example",
        )

        graph = build_graph(immich, today=date(2026, 8, 25))

        wren = next(node for node in graph.people if node.evidence.name == "Wren Example")
        assert wren.tier is Tier.INNER
        assert wren.counts_reliable is False

    def test_a_pair_query_that_fails_does_not_end_the_scan(self):
        class Flaky(FakeImmich):
            def count_assets_with_people(self, person_ids):
                raise TimeoutError("Immich blinked")

        immich = Flaky(
            people=[
                _Person("p1", "Alex Example"),
                _Person("p2", "Sam Sample"),
                _Person("p3", "Kai Example"),
            ],
            months={
                "p1": _monthly(date(2010, 1, 1), 190, 20),
                "p2": _monthly(date(2018, 6, 1), 90, 20),
                "p3": _monthly(date(2019, 1, 1), 40, 20),
            },
        )

        graph = build_graph(immich, today=date(2026, 8, 25))

        assert len(graph.people) == 3


class TestGraphMetadata:
    def test_the_graph_records_when_it_was_built(self):
        immich = FakeImmich(
            people=[_Person("p1", "Alex Example")],
            months={"p1": _monthly(date(2019, 1, 1), 12, 10)},
        )

        graph = build_graph(immich, today=date(2026, 8, 25))

        assert isinstance(graph.built_at, datetime)
