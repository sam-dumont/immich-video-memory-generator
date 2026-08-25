"""The shapes a person's numbers make, read without pixels.

Every person here is invented. The thresholds are the ones measured on a real
library and banked on #745, but no real person's numbers appear.
"""

from __future__ import annotations

from datetime import date

from immich_memories.people.signatures import (
    LinkKind,
    PersonEvidence,
    Tier,
    as_one_unit,
    classify,
    duplicate_links,
    first_sustained_month,
    owner_dyad_link,
    pair_key,
    tight_dyad_links,
    twin_links,
)

TODAY = date(2026, 8, 25)


def _months(year: int, first: int, count: int) -> list[date]:
    return [date(year + (first - 1 + i) // 12, (first - 1 + i) % 12 + 1, 1) for i in range(count)]


def _every_month(start: date, count: int) -> list[date]:
    return _months(start.year, start.month, count)


def _person(
    name: str,
    months: list[date],
    count: int,
    birth_date: date | None = None,
) -> PersonEvidence:
    return PersonEvidence(
        person_id=f"id-{name.lower().replace(' ', '-')}",
        name=name,
        count=count,
        active_months=tuple(sorted(months)),
        birth_date=birth_date,
    )


class TestOnset:
    def test_a_stray_early_month_is_not_the_onset(self):
        stray = [date(2011, 3, 1)]
        sustained = _months(2018, 1, 5)

        assert first_sustained_month(stray + sustained) == date(2018, 1, 1)

    def test_the_first_month_of_a_real_arrival_is_the_onset(self):
        assert first_sustained_month(_months(2018, 1, 5)) == date(2018, 1, 1)

    def test_epoch_dates_from_broken_exif_are_not_an_arrival(self):
        noise = _months(1970, 1, 6)
        real = _months(2015, 4, 4)

        assert first_sustained_month(noise + real) == date(2015, 4, 1)

    def test_a_person_seen_only_a_few_times_has_no_onset(self):
        assert first_sustained_month([date(2019, 2, 1), date(2021, 7, 1)]) is None


class TestTiers:
    def test_someone_present_most_months_for_years_is_inner(self):
        alex = _person("Alex Example", _every_month(date(2018, 1, 1), 80), count=1200)

        assert classify(alex, today=TODAY) is Tier.INNER

    def test_a_pile_of_pictures_from_four_months_is_an_event_companion(self):
        # The measured case: a race group member, ~160 pictures, a handful of
        # active months scattered across years. Volume says family, shape says
        # they were at four events.
        rowan = _person(
            "Rowan Example",
            [date(2019, 5, 1), date(2021, 5, 1), date(2023, 6, 1), date(2024, 5, 1)],
            count=160,
        )

        assert classify(rowan, today=TODAY) is Tier.EVENT

    def test_a_few_months_a_year_over_years_is_recurring_not_inner(self):
        months = [date(y, m, 1) for y in range(2016, 2026) for m in (4, 8, 12)]
        kai = _person("Kai Example", months, count=140)

        assert classify(kai, today=TODAY) is Tier.RECURRING

    def test_a_handful_of_months_in_a_decade_is_episodic(self):
        months = [date(y, 7, 1) for y in (2015, 2018, 2019, 2022, 2024)]
        sasha = _person("Sasha Example", months, count=30)

        assert classify(sasha, today=TODAY) is Tier.EPISODIC


class TestBirthdateOverride:
    def test_a_child_born_into_the_library_is_inner_despite_a_short_span(self):
        # Two and a half years of span cannot clear the inner spread bar, but a
        # person whose entire life is in the library is not merely recurring.
        robin = _person(
            "Robin Example",
            _every_month(date(2024, 3, 1), 30),
            count=900,
            birth_date=date(2024, 3, 4),
        )

        assert classify(robin, today=TODAY) is Tier.INNER

    def test_a_visiting_child_of_the_same_age_is_not_promoted(self):
        # Same birth year, same age-equals-span arithmetic — the difference is
        # that they are here eight months out of thirty, not thirty.
        noa = _person(
            "Noa Example",
            [date(2024, 4, 1), date(2024, 9, 1), date(2025, 1, 1), date(2025, 6, 1)]
            + [date(2025, 11, 1), date(2026, 2, 1), date(2026, 5, 1), date(2026, 8, 1)],
            count=40,
            birth_date=date(2024, 3, 4),
        )

        assert classify(noa, today=TODAY) is not Tier.INNER


class TestTwins:
    def test_two_children_of_one_family_sharing_a_birth_date_are_flagged(self):
        one = _person("Robin Example", _every_month(date(2024, 3, 1), 30), 576, date(2024, 3, 4))
        other = _person("Wren Example", [date(2026, 6, 1), date(2026, 7, 1)], 20, date(2024, 3, 4))

        links = twin_links([one, other])

        assert {(link.source_id, link.target_id) for link in links} == {
            (one.person_id, other.person_id),
            (other.person_id, one.person_id),
        }

    def test_a_shared_birth_date_across_families_is_not_a_twin(self):
        one = _person("Robin Example", _every_month(date(2024, 3, 1), 30), 576, date(2024, 3, 4))
        other = _person("Wren Sample", _every_month(date(2024, 5, 1), 20), 90, date(2024, 3, 4))

        assert twin_links([one, other]) == []

    def test_the_absorbed_twin_is_read_from_the_pair_not_from_their_own_count(self):
        # Face recognition merges identical faces, so one record ends up with
        # nearly every picture and the other with the few hand-tagged ones.
        # Read alone the second twin is a stranger; read as the unit they are,
        # they sit exactly where their sibling does.
        one = _person("Robin Example", _every_month(date(2024, 3, 1), 30), 576, date(2024, 3, 4))
        other = _person("Wren Example", [date(2026, 6, 1), date(2026, 7, 1)], 20, date(2024, 3, 4))

        assert classify(other, today=TODAY) is not Tier.INNER
        assert classify(as_one_unit(other, one), today=TODAY) is Tier.INNER


class TestDuplicates:
    def test_one_name_on_two_person_records_is_a_curation_flag(self):
        first = _person("Alex Example", _every_month(date(2018, 1, 1), 40), 300)
        second = PersonEvidence(
            person_id="id-alex-split",
            name="alex example",
            count=25,
            active_months=tuple(_every_month(date(2023, 1, 1), 6)),
        )

        links = duplicate_links([first, second])

        assert [link.kind for link in links] == [LinkKind.DUPLICATE, LinkKind.DUPLICATE]
        assert {link.target_id for link in links} == {first.person_id, second.person_id}

    def test_unnamed_records_are_not_duplicates_of_each_other(self):
        first = PersonEvidence(person_id="a", name="", count=90, active_months=())
        second = PersonEvidence(person_id="b", name="  ", count=70, active_months=())

        assert duplicate_links([first, second]) == []


class TestTightDyads:
    def test_two_people_who_are_mostly_in_each_other_s_frames_are_a_dyad(self):
        one = _person("Alex Example", _every_month(date(2016, 1, 1), 120), 900)
        other = _person("Sam Sample", _every_month(date(2016, 1, 1), 120), 800)
        shared = {pair_key(one.person_id, other.person_id): 400}

        links = tight_dyad_links([one, other], shared)

        assert [link.kind for link in links] == [LinkKind.TIGHT_DYAD, LinkKind.TIGHT_DYAD]
        assert all(link.via == "co-occurrence" for link in links)

    def test_a_one_sided_overlap_is_not_a_dyad(self):
        # Everyone who lives here appears in the household's frames; being a
        # large share of somebody's pictures only counts if it goes both ways.
        big = _person("Alex Example", _every_month(date(2016, 1, 1), 120), 4000)
        small = _person("Sasha Example", _every_month(date(2020, 1, 1), 40), 200)
        shared = {pair_key(big.person_id, small.person_id): 120}

        assert tight_dyad_links([big, small], shared) == []

    def test_pairs_with_the_owner_are_left_to_the_photographer_correction(self):
        owner = _person("Alex Example", _every_month(date(2010, 1, 1), 200), 3000)
        other = _person("Sam Sample", _every_month(date(2018, 6, 1), 99), 2500)
        shared = {pair_key(owner.person_id, other.person_id): 1500}

        assert tight_dyad_links([owner, other], shared, owner_id=owner.person_id) == []


class TestOwnerPairing:
    def test_the_partner_is_found_by_curve_even_with_no_shared_frames(self):
        # Measured: in the quarter the owner met their partner the partner
        # appears 25 times and they share zero frames — the owner is holding
        # the camera. Their months are the same months all the same.
        owner = _person("Alex Example", _every_month(date(2010, 1, 1), 200), 3000)
        partner = _person("Sam Sample", _every_month(date(2018, 6, 1), 99), 2500)
        acquaintance = _person("Kai Example", [date(2019, m, 1) for m in (3, 7, 11)], 30)

        link = owner_dyad_link(owner, [partner, acquaintance])

        assert link is not None
        assert link.target_id == partner.person_id
        assert link.via == "curve-pairing"

    def test_nobody_tracks_the_owner_means_no_partner_is_claimed(self):
        owner = _person("Alex Example", _every_month(date(2010, 1, 1), 200), 3000)
        friend = _person("Kai Example", [date(y, 6, 1) for y in range(2011, 2026)], 300)

        assert owner_dyad_link(owner, [friend]) is None
