"""A trip is named at the scale its pictures cover, from Immich's own place fields.

The pictures below carry what Immich's offline reverse geocoding stores per
asset (GeoNames city, admin-1 region, country). No geocoder is passed, which is
what a default install does: no coordinate leaves the machine.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from immich_memories.analysis.trip_detection import detect_trips
from immich_memories.api.models import Asset, AssetType, ExifInfo
from immich_memories.i18n_places import localise_place

# Far from every trip below, so every picture counts as away.
_HOME = (64.0, -150.0)


def _picture(n: int, lat: float, lon: float, city: str, state: str, country: str) -> Asset:
    ts = datetime(2025, 7, 1, 10, tzinfo=UTC) + timedelta(hours=6 * n)
    exif = ExifInfo(latitude=lat, longitude=lon, city=city, state=state, country=country)
    return Asset(
        id=f"a{n}",
        type=AssetType.IMAGE,
        fileCreatedAt=ts,
        fileModifiedAt=ts,
        updatedAt=ts,
        exifInfo=exif,
    )


def _trip_name(stops: list[tuple[int, float, float, str, str, str]]) -> str:
    pictures: list[Asset] = []
    for count, lat, lon, city, state, country in stops:
        pictures += [
            _picture(len(pictures) + i, lat, lon, city, state, country) for i in range(count)
        ]
    (trip,) = detect_trips(pictures, *_HOME, min_distance_km=50, max_gap_days=3)
    return trip.location_name


def test_a_city_trip_is_named_after_the_city():
    name = _trip_name(
        [
            (18, 36.17, -115.14, "Las Vegas", "Nevada", "United States"),
            (2, 36.01, -114.74, "Boulder City", "Nevada", "United States"),
        ]
    )

    assert name == "Las Vegas, United States"


def test_a_hike_across_one_regions_towns_is_named_after_the_region():
    name = _trip_name(
        [
            (5, 50.96, 13.94, "Pirna", "Saxony", "Germany"),
            (6, 50.92, 14.15, "Bad Schandau", "Saxony", "Germany"),
            (4, 50.92, 14.07, "Königstein", "Saxony", "Germany"),
            (5, 50.98, 14.10, "Hohnstein", "Saxony", "Germany"),
        ]
    )

    assert name == "Saxony, Germany"


def test_a_crete_trip_across_several_towns_is_named_after_the_island():
    name = _trip_name(
        [
            (6, 35.34, 25.13, "Heraklion", "Crete", "Greece"),
            (5, 35.37, 24.47, "Rethymno", "Crete", "Greece"),
            (5, 35.51, 24.02, "Chania", "Crete", "Greece"),
            (4, 35.19, 25.72, "Agios Nikolaos", "Crete", "Greece"),
        ]
    )

    assert name == "Crete, Greece"
    assert localise_place(name, "fr") == "Crète, Grèce"


def test_a_cyprus_trip_across_its_districts_is_named_cyprus():
    name = _trip_name(
        [
            (7, 34.77, 32.42, "Paphos", "Pafos", "Cyprus"),
            (7, 34.68, 33.04, "Limassol", "Limassol District", "Cyprus"),
            (6, 34.92, 33.63, "Larnaca", "Larnaka", "Cyprus"),
        ]
    )

    assert name == "Cyprus"
    assert localise_place(name, "fr") == "Chypre"


def test_a_puglia_trip_is_named_after_the_region():
    name = _trip_name(
        [
            (5, 41.12, 16.87, "Bari", "Apulia", "Italy"),
            (5, 40.99, 17.22, "Polignano a Mare", "Apulia", "Italy"),
            (5, 40.73, 17.58, "Ostuni", "Apulia", "Italy"),
            (5, 40.35, 18.17, "Lecce", "Apulia", "Italy"),
        ]
    )

    assert name == "Apulia, Italy"
    assert localise_place(name, "fr") == "Pouilles, Italie"


def test_a_road_trip_mostly_across_two_states_names_both():
    name = _trip_name(
        [
            (8, 36.17, -115.14, "Las Vegas", "Nevada", "United States"),
            (2, 37.20, -112.99, "Springdale", "Utah", "United States"),
            (4, 37.63, -112.17, "Bryce Canyon City", "Utah", "United States"),
            (4, 38.57, -109.55, "Moab", "Utah", "United States"),
            (2, 36.86, -111.46, "Page", "Arizona", "United States"),
        ]
    )

    assert name == "Utah and Nevada, United States"
    assert localise_place(name, "fr") == "Utah et Nevada, États-Unis"


def test_a_road_trip_spread_over_many_states_is_named_after_the_country():
    name = _trip_name(
        [
            (5, 36.17, -115.14, "Las Vegas", "Nevada", "United States"),
            (5, 37.20, -112.99, "Springdale", "Utah", "United States"),
            (5, 36.86, -111.46, "Page", "Arizona", "United States"),
            (5, 34.05, -118.24, "Los Angeles", "California", "United States"),
        ]
    )

    assert name == "United States"


def test_an_island_listed_offline_wins_over_its_region():
    name = _trip_name(
        [
            (10, 39.57, 2.65, "Palma", "Balearic Islands", "Spain"),
            (10, 39.87, 3.02, "Pollença", "Balearic Islands", "Spain"),
        ]
    )

    assert name == "Mallorca, Spain"
    assert localise_place(name, "fr") == "Majorque, Espagne"


def test_a_region_label_in_another_script_names_the_trip_one_scale_up():
    name = _trip_name(
        [
            (10, 38.0, 23.7, "Athens", "Αττική", "Greece"),
            (10, 37.9, 23.0, "Corinth", "Αττική", "Greece"),
        ]
    )

    assert name == "Greece"


def test_an_administrative_label_is_shortened():
    name = _trip_name(
        [
            (10, 34.68, 33.04, "Limassol", "Limassol District", "Cyprus"),
            (10, 34.70, 33.02, "Germasogeia", "Limassol District", "Cyprus"),
        ]
    )

    assert name == "Cyprus"  # an island that is its own country is named once


def test_a_geocoder_is_not_asked_to_name_an_island():
    pictures = [
        _picture(i, 35.34 if i % 2 else 35.51, 25.13 if i % 2 else 24.02, c, "Crete", "Greece")
        for i, c in enumerate(["Heraklion", "Chania"] * 10)
    ]
    asked: list[tuple] = []

    def geocoder(*args, **kwargs):
        asked.append(args)
        return "Περιφερειακή Ενότητα Ηρακλείου, Ελλάδα"

    (trip,) = detect_trips(pictures, *_HOME, max_gap_days=3, geocoder=geocoder)

    assert trip.location_name == "Crete, Greece"
    assert asked == []
