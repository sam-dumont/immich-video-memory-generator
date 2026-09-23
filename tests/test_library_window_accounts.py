"""A film over several years is told one year at a time, and the window over its years."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime

from immich_memories.analysis.catalogue_runtime import catalogue_banked_episodes
from immich_memories.analysis.library_catalogue import LibraryEpisode
from immich_memories.store.episode_readings import EpisodeReadingStore
from immich_memories.store.library_catalogue import CatalogueStore, LibraryAccount
from immich_memories.store.library_overviews import library_period_account
from tests.test_library_catalogue import Reader, config_for, reading

# Born late in 2005, filmed until a day in 2026: the edge years are only partly in the window.
LIFETIME = "2005-12-03..2026-09-23"
YEARS = range(2006, 2026)


def two_episodes_a_year() -> tuple[list[LibraryEpisode], dict[str, datetime]]:
    episodes, dates = [], {}
    for year in YEARS:
        for month in (3, 8):
            asset = f"p{year}{month:02d}"
            dates[asset] = datetime(year, month, 10, 12, tzinfo=UTC)
            episodes.append(
                LibraryEpisode(
                    reading=reading(f"e{year}{month:02d}", (asset,)), taken_at=dates[asset]
                )
            )
    return episodes, dates


def banked(bank, episodes) -> list:
    with closing(EpisodeReadingStore(bank)) as store:
        store.remember([episode.reading for episode in episodes])
    return [episode.reading.identity for episode in episodes]


def kinds(bank) -> set[str]:
    with closing(sqlite3.connect(bank)) as connection:
        return {kind for (kind,) in connection.execute("SELECT kind FROM library_overviews")}


def test_twenty_years_are_told_in_one_account_a_year_and_one_for_the_window(tmp_path) -> None:
    bank = tmp_path / "annotations.sqlite"
    episodes, dates = two_episodes_a_year()
    asked = Reader()

    catalogue_banked_episodes(
        banked(bank, episodes),
        store_path=bank,
        capture_dates=dates,
        config=config_for(tmp_path),
        requester=asked,
        period=LIFETIME,
    )

    assert 0 < len(asked.prompts) <= len(YEARS) + 1
    assert library_period_account(bank, LIFETIME)
    assert library_period_account(bank, "2010")
    assert not kinds(bank) & {"month", "month-part"}
    assert library_period_account(bank, "2010-03") == ""


def test_a_second_film_over_the_same_readings_asks_nothing(tmp_path) -> None:
    bank = tmp_path / "annotations.sqlite"
    episodes, dates = two_episodes_a_year()
    identities = banked(bank, episodes)
    again = Reader()

    for asked in (Reader(), again):
        catalogue_banked_episodes(
            identities,
            store_path=bank,
            capture_dates=dates,
            config=config_for(tmp_path),
            requester=asked,
            period=LIFETIME,
        )

    assert again.prompts == []


def test_a_year_the_window_only_touches_is_not_banked_as_that_year(tmp_path) -> None:
    """A later film of that whole year must not read an account of two weeks of it."""
    bank = tmp_path / "annotations.sqlite"
    episodes = [
        LibraryEpisode(reading=reading(f"e{day}", (f"p{day}",)), taken_at=taken)
        for day, taken in (
            (1, datetime(2023, 12, 22, tzinfo=UTC)),
            (2, datetime(2023, 12, 28, tzinfo=UTC)),
            (3, datetime(2024, 1, 2, tzinfo=UTC)),
            (4, datetime(2024, 1, 4, tzinfo=UTC)),
        )
    ]
    window = "2023-12-20..2024-01-05"

    catalogue_banked_episodes(
        banked(bank, episodes),
        store_path=bank,
        capture_dates={e.reading.full_asset_ids[0]: e.taken_at for e in episodes},
        config=config_for(tmp_path),
        requester=Reader(),
        period=window,
    )

    assert library_period_account(bank, window)
    assert library_period_account(bank, "2023") == ""
    assert library_period_account(bank, "2024") == ""


def test_a_month_the_library_already_accounted_for_is_read_not_retold(tmp_path) -> None:
    bank = tmp_path / "annotations.sqlite"
    episodes, dates = two_episodes_a_year()
    march_2010 = [e for e in episodes if e.taken_at.strftime("%Y-%m") == "2010-03"]
    with closing(CatalogueStore(bank)) as store:
        store.remember(
            [
                LibraryAccount(
                    "banked-march", "month", "2010-03", "a banked account of March", ("x", "y")
                )
            ]
        )
    asked = Reader()

    catalogue_banked_episodes(
        banked(bank, episodes),
        store_path=bank,
        capture_dates=dates,
        config=config_for(tmp_path),
        requester=asked,
        period=LIFETIME,
    )

    told = "\n".join(asked.prompts)
    assert "a banked account of March" in told
    assert march_2010[0].reading.what_happened not in told


def test_the_facts_of_unread_episodes_reach_their_year_not_a_month(tmp_path) -> None:
    bank = tmp_path / "annotations.sqlite"
    episodes, dates = two_episodes_a_year()
    facts = LibraryAccount("facts-2012", "episode-facts", "2012-06", "a quiet day at the lake", ())
    asked = Reader()

    catalogue_banked_episodes(
        banked(bank, episodes),
        store_path=bank,
        capture_dates=dates,
        config=config_for(tmp_path),
        requester=asked,
        unread_facts=(facts,),
        period=LIFETIME,
    )

    assert "a quiet day at the lake" in "\n".join(asked.prompts)
    assert not kinds(bank) & {"month", "month-part"}


def test_a_month_film_still_banks_its_month_and_no_year(tmp_path) -> None:
    bank = tmp_path / "annotations.sqlite"
    episodes, dates = two_episodes_a_year()
    march = [e for e in episodes if e.taken_at.strftime("%Y-%m") == "2010-03"]

    catalogue_banked_episodes(
        banked(bank, march),
        store_path=bank,
        capture_dates=dates,
        config=config_for(tmp_path),
        requester=Reader(),
        period="2010-03",
    )

    assert kinds(bank) <= {"month", "month-part"}


def test_a_year_film_still_banks_its_months_and_its_year(tmp_path) -> None:
    bank = tmp_path / "annotations.sqlite"
    episodes, dates = two_episodes_a_year()
    year = [e for e in episodes if e.taken_at.year == 2010]

    catalogue_banked_episodes(
        banked(bank, year),
        store_path=bank,
        capture_dates=dates,
        config=config_for(tmp_path),
        requester=Reader(),
        period="2010",
    )

    assert library_period_account(bank, "2010")
    assert library_period_account(bank, "2010-03")
