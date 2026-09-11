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


def test_era_day_shares_lists_only_eras_the_library_can_speak_for() -> None:
    """One row per era with data; an era the library never photographed says nothing."""
    from immich_memories.people.gather import COVID_ERA, Era, era_day_shares

    library_days = {date(2020, 4, n) for n in range(1, 11)}
    person_days = {date(2020, 4, 2), date(2020, 4, 5), date(2020, 4, 9)}
    silent_era = Era("pre-library", start=date(1990, 1, 1), end=date(1999, 12, 31))

    assert era_day_shares(person_days, library_days, eras=(COVID_ERA, silent_era)) == (
        ("covid", 0.3),
    )
