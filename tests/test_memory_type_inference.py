"""`--month` used to need `--memory-type` before it did anything.

`--year 2025 --month 7` and `--month 8` both returned the whole year -- 3269
videos either way -- because the month never reached the date range. A user
could render a year-long memory believing they asked for July, with nothing in
the output contradicting them.

The memory type is now inferred from the date flags, so the combination someone
naturally types does what it looks like it does.
"""

from __future__ import annotations

from immich_memories.cli._date_resolution import infer_memory_type


def test_a_year_and_a_month_means_monthly_highlights():
    assert infer_memory_type(None, year=2025, month=7) == "monthly_highlights"


def test_a_year_alone_means_the_yearly_review():
    assert infer_memory_type(None, year=2025, month=None) == "year_in_review"


def test_an_explicit_type_always_wins():
    """Existing invocations must keep doing exactly what they did."""
    assert infer_memory_type("season", year=2025, month=7) == "season"
    assert infer_memory_type("trip", year=2025, month=8) == "trip"
    assert infer_memory_type("person_spotlight", year=2025, month=None) == "person_spotlight"


def test_nothing_is_inferred_without_a_year():
    """--start/--end and friends resolve their own range; leave them alone."""
    assert infer_memory_type(None, year=None, month=None) is None
    assert infer_memory_type(None, year=None, month=3) is None


def test_another_selector_suppresses_the_inference():
    """A person or a season means the user is asking for something specific;
    guessing `year_in_review` would quietly override it."""
    assert infer_memory_type(None, year=2025, month=None, has_person=True) is None
    assert infer_memory_type(None, year=2025, month=None, season="summer") is None
    assert infer_memory_type(None, year=2025, month=None, birthday="02/07") is None


def test_a_month_still_wins_over_a_person_filter():
    """--month is unambiguous: it narrows to one month whatever else is set."""
    assert infer_memory_type(None, year=2025, month=7, has_person=True) == "monthly_highlights"


class TestTheReportedSymptom:
    """The issue's reproduction: two different months returned the same range.

    `--year 2025 --month 7` and `--month 8` both reported 3269 videos, the whole
    year, because the range was resolved without the month.
    """

    @staticmethod
    def _range_for(month: int | None):
        from immich_memories.cli._date_resolution import resolve_date_range

        memory_type = infer_memory_type(None, year=2025, month=month)
        return resolve_date_range(
            2025, None, None, None, None, memory_type=memory_type, month=month
        )

    def test_two_months_no_longer_resolve_to_the_same_range(self):
        july = self._range_for(7)
        august = self._range_for(8)

        assert (july.start.date(), july.end.date()) != (august.start.date(), august.end.date())

    def test_a_month_resolves_to_that_month(self):
        july = self._range_for(7)

        assert july.start.date().month == 7
        assert july.end.date().month == 7

    def test_a_year_without_a_month_is_still_the_whole_year(self):
        whole = self._range_for(None)

        assert whole.start.date().month == 1
        assert whole.end.date().month == 12


def test_an_album_suppresses_the_inference():
    """--from-album brings its own assets; the date flags describe nothing."""
    assert infer_memory_type(None, year=2025, month=7, from_album="Holiday") is None
