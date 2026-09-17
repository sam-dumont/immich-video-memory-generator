"""A detected trip is one story of the film, and its grant grows with its length."""

import json
from datetime import date

from tests.editorial_film_fixtures import FilmJudge, film_source, home_days, trip_days
from tests.test_editorial_duration_planner_integration import run

MAY = (date(2030, 5, 1), date(2030, 5, 31))


def _film(tmp_path, trip_length, **options):
    """Twenty ordinary days at home around one trip; more stories than the film has slots."""
    days = [
        *home_days(date(2030, 5, 1), 8),
        *trip_days(date(2030, 5, 10), trip_length),
        *home_days(date(2030, 5, 19), 12),
    ]
    return film_source(tmp_path, days, seconds=60, span=MAY, **options)


def _trip_rows(plan):
    return [row for row in plan["story"]["episodes"] if row.get("kind") == "trip"]


def test_a_detected_trip_is_one_story_whatever_the_reader_grouped(tmp_path):
    # WHY: the judge stands in for the reader, which here filed every trip day as its own story.
    plan = run(_film(tmp_path, 7), FilmJudge())

    trips = _trip_rows(plan)
    assert len(trips) == 1
    assert len(trips[0]["day_episodes"]) == 7
    assert trips[0]["seen"]["days"] == 7
    assert trips[0]["granted"] > 1
    others = [row for row in plan["story"]["episodes"] if row.get("kind") != "trip"]
    assert all(row["granted"] <= 1 for row in others)
    trip_carriers = [c for c in plan["carriers"] if c["story_episode"] == trips[0]["episode"]]
    assert len(trip_carriers) == trips[0]["granted"]


def test_the_trip_is_weighed_as_a_trip_with_its_stops(tmp_path):
    rows = []

    def weigh(row):
        rows.append(row)
        return "major" if "| trip:" in row else "minor"

    run(_film(tmp_path, 4), FilmJudge(weigh=weigh))

    trip_rows = [row for row in rows if "| trip:" in row]
    assert trip_rows, "the weighing never saw a trip row"
    assert "4 days away" in trip_rows[0]
    assert "Seaside" in trip_rows[0]


def test_a_longer_trip_earns_more_of_the_film(tmp_path):
    short = _trip_rows(run(_film(tmp_path / "short", 3), FilmJudge()))[0]
    long = _trip_rows(run(_film(tmp_path / "long", 8), FilmJudge()))[0]

    assert 1 < short["granted"] < long["granted"]


def test_without_a_home_base_there_are_no_trip_stories_and_the_record_says_why(tmp_path):
    source = _film(tmp_path, 7, home_base=False)
    plan = run(source, FilmJudge())

    assert not _trip_rows(plan)
    record = json.loads(next(source.artifact_dir.rglob("trip-stories.private.json")).read_text())
    assert record["trips"] == []
    assert "home base" in record["status"]
