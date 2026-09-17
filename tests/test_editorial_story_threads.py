"""A recurring activity shares one grant within an era of the film, and only there."""

from datetime import date, timedelta

from tests.editorial_film_fixtures import POOL, Day, FilmJudge, film_source
from tests.test_editorial_duration_planner_integration import run

CHORES = [
    "Baking",
    "Painting",
    "Kites",
    "Gardening",
    "Puzzles",
    "Lanterns",
    "Chess",
    "Pottery",
    "Knitting",
    "Juggling",
    "Origami",
    "Sketching",
    "Weaving",
    "Carving",
    "Quilting",
    "Brewing",
    "Fishing",
    "Rowing",
]


def _recorder(asked):
    def threads(rows, _prompt):
        asked.append(rows)
        return []

    return threads


def _days(start: date, *, lessons: int, every: int) -> list[Day]:
    """Swimming lessons at the same pool, weeks apart, and two unrelated days at home after each:
    more stories than the film has slots."""
    pool = [
        Day(start + timedelta(days=every * n), "Swimming lesson at Pooltown", POOL)
        for n in range(lessons)
    ]
    home = [
        Day(start + timedelta(days=every * n + 3 + extra), CHORES[2 * n + extra])
        for n in range(lessons)
        for extra in (0, 1)
    ]
    return sorted([*pool, *home], key=lambda day: day.day)


def _weigh(row):
    return "major" if "Swimming" in row else "minor"


def _same_activity(rows, _prompt):
    """The reader's answer: every offered swimming lesson is one recurring activity."""
    return [[row.split(" |", 1)[0] for row in rows if "Swimming" in row]]


def _threads(plan):
    return [row for row in plan["story"]["episodes"] if row.get("kind") == "thread"]


def test_a_recurring_activity_in_a_one_year_film_is_one_story_with_one_grant(tmp_path):
    asked = []

    def threads(rows, prompt):
        asked.append(prompt)
        return _same_activity(rows, prompt)

    days = _days(date(2030, 1, 7), lessons=6, every=21)
    source = film_source(
        tmp_path,
        days,
        seconds=40,
        span=(date(2030, 1, 1), date(2030, 12, 31)),
        product="year_in_review",
    )
    # WHY: the judge stands in for the reader's weights and its recurring-activity answer.
    plan = run(source, FilmJudge(weigh=_weigh, threads=threads))

    threads_found = _threads(plan)
    assert len(threads_found) == 1
    assert len(threads_found[0]["day_episodes"]) == 6
    assert threads_found[0]["granted"] == 1
    swims = [c for c in plan["carriers"] if "swimming" in c["line"].lower()]
    assert len(swims) == 1
    assert len(asked) == 1
    assert "2030-01-01" in asked[0] and "2030-12-31" in asked[0]


def test_a_film_of_several_eras_keeps_one_thread_per_era(tmp_path):
    days = _days(date(2030, 3, 4), lessons=9, every=100)
    source = film_source(
        tmp_path,
        days,
        seconds=40,
        span=(date(2030, 1, 1), date(2032, 12, 31)),
        product="person_spotlight",
    )
    plan = run(source, FilmJudge(weigh=_weigh, threads=_same_activity))

    threads_found = _threads(plan)
    years = sorted({day[:4] for row in threads_found for day in row["thread"]["days"]})
    assert years == ["2030", "2031", "2032"]
    assert len(threads_found) == 3
    for row in threads_found:
        assert len({day[:4] for day in row["thread"]["days"]}) == 1
    swims = [c for c in plan["carriers"] if "swimming" in c["line"].lower()]
    assert len({c["taken"][:4] for c in swims}) == 3


def test_steps_worth_showing_apart_stay_apart(tmp_path):
    days = _days(date(2030, 1, 7), lessons=4, every=21)
    source = film_source(tmp_path, days, seconds=40, span=(date(2030, 1, 1), date(2030, 12, 31)))
    plan = run(source, FilmJudge(weigh=_weigh))

    assert not _threads(plan)
    swims = [c for c in plan["carriers"] if "swimming" in c["line"].lower()]
    assert len(swims) == 4


def test_days_that_only_share_a_place_are_never_asked_about(tmp_path):
    asked = []
    days = [Day(date(2030, 1, 7) + timedelta(days=21 * n), CHORES[n], POOL) for n in range(4)]
    source = film_source(tmp_path, days, seconds=40, span=(date(2030, 1, 1), date(2030, 12, 31)))
    run(source, FilmJudge(weigh=_weigh, threads=_recorder(asked)))

    assert asked == []


def test_a_shared_name_is_company_not_an_activity(tmp_path):
    asked = []
    days = [
        Day(
            date(2030, 1, 7) + timedelta(days=21 * n),
            f"{CHORES[n]} with Wilhelmina",
            POOL,
            company="Wilhelmina (friend)",
        )
        for n in range(4)
    ]
    source = film_source(tmp_path, days, seconds=40, span=(date(2030, 1, 1), date(2030, 12, 31)))
    run(source, FilmJudge(weigh=_weigh, threads=_recorder(asked)))

    assert asked == []
