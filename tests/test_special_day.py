"""Telling a day something happened on from an afternoon of one subject.

Volume does not separate them. In a real library the busiest single day is
166 photos of a work shoot inside one hour, and the second busiest is 413 of
one street performer. What separates them is how long the day stayed alive.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import patch

from immich_memories.analysis.special_day import (  # noqa: F401
    MIN_ACTIVE_HOURS,
    ask_if_special,
    candidate_days,
    sample_across_day,
)


def _asset(hour: int, minute: int = 0, day: int = 4) -> SimpleNamespace:
    return SimpleNamespace(
        file_created_at=datetime(2021, 4, day, hour, minute, tzinfo=UTC),
        exif_info=SimpleNamespace(city="Someplace", country="Belgium"),
        people=[],
    )


def test_a_day_spread_over_many_hours_is_a_candidate() -> None:
    """The track day: 133 photos across seven hours."""
    day = [_asset(h, m) for h in range(9, 17) for m in (0, 15, 30)]

    assert candidate_days(day)


def test_one_hour_of_shooting_is_not_a_candidate() -> None:
    """166 photos of a work shoot, all inside a single hour."""
    day = [_asset(14, m % 60) for m in range(0, 160)]

    assert not candidate_days(day), "volume is not the question"


def test_a_long_but_thin_day_is_not_a_candidate() -> None:
    """Hours alone are not enough either — a handful of snaps is not an event."""
    day = [_asset(h) for h in range(8, 20)]

    assert not candidate_days(day)


def test_the_sample_spreads_over_the_day_not_the_busiest_minute() -> None:
    """A sample drawn from one burst would describe the burst, not the day."""
    day = [_asset(9, m) for m in range(40)] + [_asset(h) for h in (12, 15, 18, 21)]

    hours = {a.file_created_at.hour for a in sample_across_day(day, count=5)}

    assert len(hours) >= 4, f"sample collapsed onto {hours}"


def test_an_unreachable_model_is_not_a_verdict() -> None:
    """A failed question must not silently mark every day special."""
    # WHY: the LLM is the external boundary; here it is simply down.
    with patch(
        "immich_memories.analysis.llm_query.query_llm", side_effect=OSError("no route to host")
    ):
        verdict = ask_if_special([_asset(10)], llm_config=SimpleNamespace())

    assert verdict.special is False
    assert verdict.title == ""


def test_the_threshold_sits_below_every_labelled_occasion() -> None:
    """Measured: birth 18h, wedding 12h, nephew 10h, track day 7h; worst negative 5h."""
    assert MIN_ACTIVE_HOURS <= 6


class TestTitlesStayGrounded:
    """A title card is the wrong place for a plausible invention.

    Asked about a night out whose photos carry no city at all, the model
    answered "a person, a person and a person at Someplace". The three names were real
    and Someplace was invented, in spite of the prompt forbidding it.
    """

    def _day_with(
        self, city: str | None, names: list[str], *, gps: bool = False
    ) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                file_created_at=datetime(2011, 12, 2, hour, tzinfo=UTC),
                exif_info=SimpleNamespace(
                    city=city,
                    state=None,
                    country=city and "Belgium",
                    latitude=50.31 if gps else None,
                    longitude=4.66 if gps else None,
                ),
                people=[SimpleNamespace(name=n) for n in names],
            )
            for hour in range(8, 16)
        ]

    def _answer(self, day: list, payload: str) -> object:
        # WHY: the model is the boundary; this pins what we do with its answer.
        with patch("immich_memories.analysis.special_day._ask_text_only", return_value=payload):
            return ask_if_special(day, llm_config=SimpleNamespace())

    def test_a_place_nobody_mentioned_is_dropped(self) -> None:
        day = self._day_with(city=None, names=["First Person", "Second Person"])

        result = self._answer(
            day, '{"special": true, "title": "Two names at Someplace", "subtitle": ""}'
        )

        assert result.special is True, "the day is still an occasion"
        assert result.title == "", "but it cannot be named after a place nobody was in"

    def test_a_place_that_is_in_the_data_survives(self) -> None:
        day = self._day_with(city="Someplace", names=["First Person"])

        result = self._answer(
            day, '{"special": true, "title": "A name at Someplace", "subtitle": ""}'
        )

        assert result.title == "A name at Someplace"

    def test_ordinary_words_do_not_need_grounding(self) -> None:
        day = self._day_with(city=None, names=[])

        result = self._answer(
            day, '{"special": true, "title": "A long Saturday morning", "subtitle": ""}'
        )

        assert result.title == "A long Saturday morning"

    def test_coordinates_let_the_model_name_what_it_sees(self) -> None:
        """With GPS the model may read the pictures and the map.

        An earlier version policed every capitalised word and threw away
        "Audi R8 V10 Track Day at Nearby" — a correct reading of a real
        track day — because no EXIF field contains the word Audi.
        """
        day = self._day_with(city=None, names=[], gps=True)

        result = self._answer(
            day,
            '{"special": true, "title": "Audi R8 V10 Track Day at Nearby", "subtitle": ""}',
        )

        assert result.title == "Audi R8 V10 Track Day at Nearby"

    def test_a_guessed_brand_name_is_dropped_even_with_gps(self) -> None:
        """Told twice not to name an event it could not read, it still guessed.

        The same a place day came back as "Attending KubeCon in a place"
        and then "Attending GitLab All-Hands". It had recognised a hall full
        of lanyards and invented which conference it was.
        """
        day = self._day_with(city="Township", names=[], gps=True)

        result = self._answer(
            day, '{"special": true, "title": "Attending GitLab All-Hands", "subtitle": ""}'
        )

        assert result.title == ""

    def test_a_car_the_model_can_see_is_not_a_guessed_brand(self) -> None:
        day = self._day_with(city="Someplace", names=[], gps=True)

        result = self._answer(
            day, '{"special": true, "title": "Driving an R8 at Someplace", "subtitle": ""}'
        )

        assert result.title == "Driving an R8 at Someplace"


class TestATitleHasToBeATitle:
    """Two things the model falls back on when it is unsure, both useless.

    In a library sweep, seven of twenty-three titles came back as either the
    date — "Monday 13 August 2007" — or a person's name. Both are already on
    the card; neither says what happened.
    """

    def _day(self, names: list[str]) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                file_created_at=datetime(2007, 8, 13, hour, tzinfo=UTC),
                exif_info=SimpleNamespace(
                    city="Anytown",
                    state=None,
                    country="Belgium",
                    latitude=50.72,
                    longitude=4.87,
                ),
                people=[SimpleNamespace(name=n) for n in names],
            )
            for hour in range(9, 17)
        ]

    def _answer(self, day: list, title: str) -> object:
        # WHY: the model is the boundary; this pins what we accept from it.
        with patch(
            "immich_memories.analysis.special_day._ask_text_only",
            return_value=f'{{"special": true, "title": "{title}", "what": "something"}}',
        ):
            return ask_if_special(day, llm_config=SimpleNamespace())

    def test_a_date_is_not_a_title(self) -> None:
        assert self._answer(self._day([]), "Monday 13 August 2007").title == ""

    def test_a_persons_name_is_not_a_title(self) -> None:
        day = self._day(["Third Person"])

        assert self._answer(day, "Third Person").title == ""

    def test_saying_what_happened_is(self) -> None:
        day = self._day(["Third Person"])

        assert self._answer(day, "Scout camp at Anytown").title == "Scout camp at Anytown"

    def test_rejecting_a_title_does_not_reject_the_day(self) -> None:
        """A day with a useless title is still a day something happened on."""
        verdict = self._answer(self._day([]), "Monday 13 August 2007")

        assert verdict.title == ""
        assert verdict.special is True


class TestAPlaceHasToBeSomewhereTheDayWas:
    """The failure that kept recurring: name the famous instance, not the real one.

    Asked about a track day whose EXIF holds Someplace, Nearby and
    a place, the model answered "the famous circuit" —
    Belgium's well-known circuit rather than the one the coordinates sit on.
    """

    def _day(self, cities: list[str]) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                file_created_at=datetime(2021, 4, 4, hour, tzinfo=UTC),
                exif_info=SimpleNamespace(
                    city=cities[hour % len(cities)],
                    state=None,
                    country="Belgium",
                    latitude=50.31,
                    longitude=4.66,
                ),
                people=[],
            )
            for hour in range(8, 16)
        ]

    def _answer(self, day: list, title: str) -> object:
        # WHY: the model is the boundary; this pins what we accept back.
        with patch(
            "immich_memories.analysis.special_day._ask_text_only",
            return_value=f'{{"special": true, "title": "{title}", "subtitle": ""}}',
        ):
            return ask_if_special(day, llm_config=SimpleNamespace())

    def test_a_circuit_the_day_never_visited_is_dropped(self) -> None:
        day = self._day(["Someplace", "Nearby"])

        assert self._answer(day, "A drive day at Circuit de a place").title == ""

    def test_the_place_it_was_actually_at_survives(self) -> None:
        day = self._day(["Someplace", "Nearby"])

        assert self._answer(day, "A drive day at Someplace").title == "A drive day at Someplace"

    def test_the_other_spelling_of_a_local_place_survives(self) -> None:
        """EXIF carries the local spelling; the model writes the English one.

        A strict match would reject a correct title over one letter, so the
        comparison is fuzzy: near-identical spellings pass, different towns
        do not.
        """
        day = self._day(["Gantvile", "Othertown"])

        assert self._answer(day, "A Sunday morning run in Ghantville").title == (
            "A Sunday morning run in Ghantville"
        )


class TestADayEndsWhenThePhotographsDo:
    """Midnight is an arbitrary place to cut an occasion in half.

    A birth ran past midnight: one continuous run from the evening before
    through the following afternoon, which grouping by calendar date cut into
    three, leaving the detector looking at the middle slice.
    """

    def _at(self, day: int, hour: int, minute: int = 0) -> SimpleNamespace:
        return SimpleNamespace(
            file_created_at=datetime(2024, 3, day, hour, minute, tzinfo=UTC),
            exif_info=SimpleNamespace(city="Anytown", state=None, country="Belgium"),
            people=[],
        )

    def test_a_night_that_runs_past_midnight_is_one_day(self) -> None:
        night = (
            [self._at(6, h) for h in (22, 23)]
            + [self._at(7, h) for h in range(0, 12)]
            + [self._at(7, h, 30) for h in range(0, 12)]
        )

        candidates = candidate_days(night)

        assert len(candidates) == 1, f"the night was split into {len(candidates)} days"
        assert next(iter(candidates)) == date(2024, 3, 6), "and it belongs to the evening it began"

    def test_two_ordinary_days_stay_two_days(self) -> None:
        """A night's sleep between them is exactly what separates them."""
        two = [
            self._at(day, hour, minute)
            for day in (6, 7)
            for hour in range(8, 20)
            for minute in (0, 30)
        ]

        assert len(candidate_days(two)) == 2
