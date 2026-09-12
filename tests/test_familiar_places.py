"""Familiar captions follow repeated attendance, never a city-name blacklist."""

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from immich_memories.analysis.familiar_places import PlaceHistory, PlaceObservation


def test_regular_visits_across_years_are_local_but_a_holiday_burst_is_not():
    # Public landmark: Royal Palace, Brussels. Never a personal address.
    regular = [
        PlaceObservation(50.843, 4.362, date(year, month, day), "Belgium")
        for year in (2023, 2024)
        for month in range(1, 13)
        for day in (1, 2)
    ]
    holiday = [
        PlaceObservation(51.2, 3.0, date(year, 8, day), "Belgium")
        for year in (2023, 2024)
        for day in range(1, 15)
        for _ in range(20)
    ]
    history = PlaceHistory(regular + holiday)

    assert history.is_familiar(50.843, 4.362)
    assert history.is_familiar(50.844, 4.362)  # 111 m from the same home
    assert not history.is_familiar(50.847, 4.362)  # another outing in the same city
    assert not history.is_familiar(51.2, 3.0)


def test_quarterly_visits_over_many_years_count_without_a_dense_two_year_burst():
    history = PlaceHistory(
        [
            PlaceObservation(50.843, 4.362, date(year, month, 1), "Belgium")
            for year in (2015, 2018, 2020, 2022, 2024)
            for month in (2, 6, 11)
        ]
    )

    assert history.is_familiar(50.843, 4.362)


def test_history_pages_the_whole_library_once_and_isolates_credentials(tmp_path):
    from immich_memories.analysis.familiar_place_cache import load_place_history

    class Library:
        base_url = "https://immich.example.test/api"
        api_key = "test-account-one"

        def __init__(self):
            self.pages = []

        def search_metadata(self, *, page, size):
            self.pages.append(page)
            assets = [
                SimpleNamespace(
                    file_created_at=date(2020 + page, month, 1),
                    exif_info=SimpleNamespace(latitude=50.843, longitude=4.362, country="Belgium"),
                )
                for month in range(1, 13)
            ]
            # The API supplies datetimes, with local capture dates retained.
            from datetime import datetime

            for asset in assets:
                asset.file_created_at = datetime.combine(asset.file_created_at, datetime.min.time())
            return SimpleNamespace(all_assets=assets, next_page="2" if page == 1 else None)

    client = Library()
    assert load_place_history(client, tmp_path).is_familiar(50.843, 4.362)
    assert client.pages == [1, 2]
    assert load_place_history(client, tmp_path).is_familiar(50.843, 4.362)
    assert client.pages == [1, 2], "warm rendering must not rescan the library"
    client.api_key = "test-account-two"
    load_place_history(client, tmp_path)
    assert client.pages == [1, 2, 1, 2]
    for cache in Path(tmp_path).rglob("*.json"):
        assert "test-account" not in cache.read_text()
        assert cache.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize(
    "point", [(None, 4.362), (50.843, None), (0, 0), (91, 4), (float("nan"), 4)]
)
def test_invalid_gps_cannot_be_a_familiar_place(point):
    from immich_memories.analysis.familiar_places import valid_coordinates

    assert not valid_coordinates(*point)


def test_a_failed_second_page_never_becomes_a_warm_history(tmp_path):
    from immich_memories.analysis.familiar_place_cache import load_place_history

    class InterruptedLibrary:
        base_url = "https://immich.example.test/api"
        api_key = "test-token"

        def search_metadata(self, *, page, size):
            if page == 2:
                raise ConnectionError("offline")
            return SimpleNamespace(all_assets=[], next_page="2")

    with pytest.raises(ConnectionError):
        load_place_history(InterruptedLibrary(), tmp_path)
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("privacy,overlay", [(True, True), (False, False)])
def test_no_history_scan_when_places_are_hidden_or_anonymized(tmp_path, privacy, overlay):
    from immich_memories.config_loader import Config
    from immich_memories.generate import GenerationParams
    from immich_memories.generate_captions import prepare_location_captions

    class NoNetwork:
        def search_metadata(self, **kwargs):
            pytest.fail("this render must not inspect real GPS history")

    params = GenerationParams(
        clips=[],
        output_path=tmp_path / "memory.mp4",
        config=Config(),
        client=NoNetwork(),
        privacy_mode=privacy,
        add_place_overlay=overlay,
    )
    assert prepare_location_captions(params, []) == []
