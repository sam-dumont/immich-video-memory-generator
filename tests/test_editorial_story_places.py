"""One place of a trip holds its own share of the film, not the film."""

import json
from datetime import date, timedelta

from tests.editorial_film_fixtures import Day, FilmJudge, film_source
from tests.test_editorial_duration_planner_integration import run

JULY = (date(2030, 7, 1), date(2030, 7, 8))
CHURCH = (43.0, 9.0, "Churchtown", "Farland")
STOPS = [
    (43.1, 9.1, "Harbour", "Farland"),
    (43.2, 9.2, "Marketplace", "Farland"),
    (43.3, 9.3, "Hillside", "Farland"),
    (43.4, 9.4, "Riverbank", "Farland"),
    (43.5, 9.5, "Oldgate", "Farland"),
    (43.6, 9.6, "Lakeside", "Farland"),
]


def _trip(tmp_path, days):
    return film_source(tmp_path, days, seconds=60, span=JULY, product="trip", home_base=False)


def _many_stops(tmp_path):
    """Eight days away: two spent inside one building, six at a stop each."""
    inside = [
        Day(date(2030, 7, 1) + timedelta(days=n), f"Inside the building, day {n + 1}", CHURCH, 6)
        for n in range(2)
    ]
    elsewhere = [
        Day(date(2030, 7, 3) + timedelta(days=n), f"A day at stop {n + 1}", where, 3)
        for n, where in enumerate(STOPS)
    ]
    return _trip(tmp_path, [*inside, *elsewhere])


def _one_venue(tmp_path):
    """Eight days away, every one of them at the same place."""
    return _trip(
        tmp_path,
        [
            Day(date(2030, 7, 1) + timedelta(days=n), f"A day at the festival {n + 1}", CHURCH, 2)
            for n in range(8)
        ],
    )


def _at(plan, city):
    return [carrier for carrier in plan["carriers"] if f"at {city}," in carrier["line"]]


# WHY: the judge stands in for the reader, which weighed the days inside the building the
# heaviest thing the trip held.
def _judge():
    return FilmJudge(weigh=lambda row: "major" if "Inside the building" in row else "minor")


def test_one_place_of_a_trip_does_not_hold_most_of_the_film(tmp_path):
    plan = run(_many_stops(tmp_path), _judge())

    inside = _at(plan, "Churchtown")
    assert 0 < len(inside) < len(plan["carriers"]) / 2
    assert len({c["line"].split("at ", 1)[1].split(",", 1)[0] for c in plan["carriers"]}) > 2


def test_the_film_is_not_made_shorter_by_the_bound(tmp_path):
    plan = run(_many_stops(tmp_path), _judge())

    assert len(plan["carriers"]) == plan["slots_total"]


def test_a_trip_that_really_is_one_place_still_fills_its_film(tmp_path):
    plan = run(_one_venue(tmp_path), FilmJudge(weigh=lambda _row: "major"))

    assert len(_at(plan, "Churchtown")) == len(plan["carriers"]) == plan["slots_total"]


def test_the_run_records_what_each_place_was_allowed(tmp_path):
    source = _many_stops(tmp_path)

    run(source, _judge())

    places = json.loads(
        (source.artifact_dir / "derived-decisions" / "story-places.private.json").read_text()
    )
    scope = next(s for s in places["scopes"] if s["scope"] == "")
    assert scope["places"]["Churchtown, Farland"] < scope["allowance"]
    assert scope["places"]["Churchtown, Farland"] > scope["places"]["Harbour, Farland"]
