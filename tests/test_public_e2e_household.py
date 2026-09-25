"""The pure rules of a public household: dates, home, episodes, manifest round trip."""

from __future__ import annotations

from datetime import datetime, timedelta

from tests.public_e2e.household import (
    Household,
    ManifestRow,
    credits_markdown,
    home_point,
    read_manifest,
    shift_years,
    snap_home,
    write_manifest,
)
from tests.public_e2e.timelines import Shot, episodes


def test_a_whole_year_shift_keeps_christmas_on_the_25th():
    assert shift_years(datetime(2008, 12, 25, 18, 30), 12) == datetime(2020, 12, 25, 18, 30)


def test_a_leap_day_landing_in_a_common_year_becomes_the_28th():
    assert shift_years(datetime(2008, 2, 29, 9), 13) == datetime(2021, 2, 28, 9)


def test_home_is_the_cell_shot_from_most_and_near_points_snap_onto_it():
    home_shots = [(50.8461, 4.3527)] * 5 + [(50.8475, 4.3570)] * 3
    trip = (43.2965, 5.3698)
    home = home_point([*home_shots, trip])

    assert home is not None
    assert snap_home((50.8475, 4.3570), home) == home
    assert snap_home(trip, home) == trip


def test_a_day_and_a_half_without_pictures_starts_a_new_episode():
    start = datetime(2009, 6, 1, 10)
    shots = [
        Shot(1, start, ""),
        Shot(2, start + timedelta(hours=20), ""),
        Shot(3, start + timedelta(days=3), ""),
    ]

    assert [[s.photoid for s in group] for group in episodes(shots)] == [[1, 2], [3]]


def test_the_manifest_reads_back_what_was_written(tmp_path):
    row = ManifestRow(
        key="cc-1",
        file="cc-1.jpg",
        kind="photo",
        source="commoncatalog",
        source_id="1",
        source_page="https://www.flickr.com/photos/x/1/",
        creator="x",
        licence="CC BY 2.0",
        licence_url="https://creativecommons.org/licenses/by/2.0/",
        taken_at="2020-06-01T10:00:00",
        scripted="taken_at",
    )
    path = tmp_path / "manifest.csv"
    write_manifest(path, [row])

    assert read_manifest(path) == [row]


def test_credits_name_the_creator_and_the_removal_contact():
    household = Household(
        name="dog-owner",
        summary="",
        uid="x",
        first_day="2008-01-01",
        last_day="2011-12-31",
        date_shift_years=12,
        owner="Robin",
        cast=(),
        relations=(),
        films=(),
    )
    row = ManifestRow(
        key="cc-1",
        file="cc-1.jpg",
        kind="photo",
        source="commoncatalog",
        source_id="1",
        source_page="",
        creator="Jane Doe",
        licence="CC BY 2.0",
        licence_url="",
        taken_at="2020-06-01T10:00:00",
    )
    text = credits_markdown(household, [row])

    assert "| Jane Doe | commoncatalog | CC BY 2.0 | 1 |" in text
    assert "sam@dropbars.be" in text


def _row(key: str, kind: str = "photo", derived_from: str = "") -> ManifestRow:
    return ManifestRow(
        key=key,
        file=f"{key}.jpg",
        kind=kind,
        source="commoncatalog",
        source_id="",
        source_page="",
        creator="",
        licence="",
        licence_url="",
        taken_at="",
        derived_from=derived_from,
    )


def test_a_burst_twin_kept_beside_its_source_counts_as_a_repeat():
    from tests.public_e2e.judge import Library, cut_metrics

    library = Library(
        key_of={},
        favourite=set(),
        manifest={
            "cc-1": _row("cc-1"),
            "syn-burst-0": _row("syn-burst-0", "clutter-burst", "cc-1"),
        },
        people={"Kim": {"a1"}},
    )
    shot = {"motion": False, "favourite": False, "day": "2019-07-01"}
    shots = [
        {**shot, "key": "cc-1", "kind": "photo", "people": ["Kim"]},
        {**shot, "key": "syn-burst-0", "kind": "clutter-burst", "people": []},
    ]

    metrics = cut_metrics(shots, library)

    assert metrics["repeats"] == 1
    assert metrics["burst_frames"] == 1
    assert metrics["per_person"] == {"Kim": 1}
