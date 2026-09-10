"""Stage-1 gather math: quarantine and era day-shares (person annotation layer)."""

from datetime import UTC, date, datetime

from immich_memories.people.gather import suspicious_date


def test_default_looking_timestamps_are_quarantined_with_their_reason() -> None:
    """Jan-1 and midnight-adjacent stamps never feed a lifecycle fact unconfirmed."""
    assert suspicious_date(datetime(2004, 1, 1, 14, 30, tzinfo=UTC)) == "january-first default"
    assert suspicious_date(datetime(2011, 6, 12, 0, 0, 31, tzinfo=UTC)) == "midnight-adjacent"
    assert suspicious_date(datetime(2011, 6, 12, 9, 15, 2, tzinfo=UTC)) is None


def test_a_date_only_stamp_is_not_midnight_suspicious() -> None:
    """A plain date carries no clock to be suspicious about."""
    assert suspicious_date(date(2011, 6, 12)) is None


def test_day_share_is_a_share_of_the_library_days_inside_the_era() -> None:
    """Never raw counts: the library's own activity is the denominator, per era."""
    from immich_memories.people.gather import Era, era_day_share, photographed_days

    era = Era("covid", start=date(2020, 3, 1), end=date(2022, 6, 30))
    library_days = photographed_days(
        [datetime(2020, 4, 1 + n, 12, 0, tzinfo=UTC) for n in range(10)]
        + [datetime(2019, 7, 4, 12, 0, tzinfo=UTC)]  # outside the era, never counted
        + [datetime(2020, 1, 1, 12, 0, tzinfo=UTC)]  # quarantined, never a photographed day
    )
    person_days = photographed_days(
        [
            datetime(2020, 4, 2, 9, 0, tzinfo=UTC),
            datetime(2020, 4, 5, 9, 0, tzinfo=UTC),
            datetime(2020, 4, 9, 9, 0, tzinfo=UTC),
        ]
    )

    assert era_day_share(person_days, library_days, era) == 0.3
    empty_era = Era("pre-library", start=date(1990, 1, 1), end=date(1999, 12, 31))
    assert era_day_share(person_days, library_days, empty_era) is None


def test_era_day_shares_lists_only_eras_the_library_can_speak_for() -> None:
    """One row per era with data; an era the library never photographed says nothing."""
    from immich_memories.people.gather import COVID_ERA, Era, era_day_shares

    library_days = {date(2020, 4, n) for n in range(1, 11)}
    person_days = {date(2020, 4, 2), date(2020, 4, 5), date(2020, 4, 9)}
    silent_era = Era("pre-library", start=date(1990, 1, 1), end=date(1999, 12, 31))

    assert era_day_shares(person_days, library_days, eras=(COVID_ERA, silent_era)) == (
        ("covid", 0.3),
    )
