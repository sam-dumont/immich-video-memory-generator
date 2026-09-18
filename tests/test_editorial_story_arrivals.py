"""The period reading can see who the library first holds inside the period it is reading."""

import json
import re
from datetime import date

from tests.editorial_film_fixtures import (
    Day,
    FilmJudge,
    film_source,
    home_days,
    known_person,
)
from tests.test_editorial_duration_planner_integration import run

MAY = (date(2030, 5, 1), date(2030, 5, 31))
ARRIVING = "Wren"
LONGSTANDING = "Ash"


def _film(tmp_path):
    """A month of ordinary days; one of them is the first the library holds of one person."""
    days = [
        *home_days(date(2030, 5, 1), 6),
        Day(date(2030, 5, 8), "A day with new company", people=(ARRIVING, LONGSTANDING)),
        *home_days(date(2030, 5, 9), 6, activity="Later home day"),
    ]
    return film_source(
        tmp_path,
        days,
        seconds=60,
        span=MAY,
        people={
            ARRIVING: known_person(
                ARRIVING,
                relationship="partner",
                tier="inner",
                first_month="2030-05",
                onset="2030-05",
            ),
            LONGSTANDING: known_person(
                LONGSTANDING, relationship="sister", tier="recurring", first_month="2024-03"
            ),
        },
    )


class _PromptJudge(FilmJudge):
    """Keeps every prompt it is asked, so a test can read what a stage was shown."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prompts: dict[str, str] = {}

    def answer(self, stage, prompt):
        self.prompts.setdefault(stage, prompt)
        return super().answer(stage, prompt)


def _cards(prompt):
    return json.JSONDecoder().raw_decode(
        prompt.split("remarkable, maybe or background)\n", 1)[1].lstrip()
    )[0]


def test_the_grouping_card_says_which_episode_the_library_first_holds_a_person_in(tmp_path):
    # WHY: the judge stands in for the reader; the test reads what the reader was shown.
    judge = _PromptJudge()

    run(_film(tmp_path), judge)

    cards = _cards(judge.prompts["story-understanding-1"])
    arriving = [card for card in cards if card.get("arrivals")]
    assert len(arriving) == 1
    assert arriving[0]["day"] == "2030-05-08"
    assert arriving[0]["arrivals"] == [
        {
            "person": ARRIVING,
            "relationship": "partner",
            "circle": "inner",
            "first_in_the_library": "2030-05",
            "present_from": "2030-05",
        }
    ]


def test_a_person_the_library_already_knew_is_not_an_arrival(tmp_path):
    # WHY: the judge stands in for the reader; the test reads what the reader was shown.
    judge = _PromptJudge()

    run(_film(tmp_path), judge)

    cards = _cards(judge.prompts["story-understanding-1"])
    assert not any(LONGSTANDING in json.dumps(card.get("arrivals") or []) for card in cards)


def test_the_weighing_row_of_that_story_carries_the_arrival(tmp_path):
    # WHY: the judge stands in for the reader; the test reads what the reader was shown.
    judge = _PromptJudge()

    run(_film(tmp_path), judge)

    rows = re.findall(r"^K\d{2} \|.*$", judge.prompts["story-weighing-source"], re.MULTILINE)
    carrying = [row for row in rows if "arrives here:" in row]
    assert len(carrying) == 1
    assert f"{ARRIVING} (partner, first in the library 2030-05)" in carrying[0]
    assert "anyone the library first holds here" in judge.prompts["story-weighing-source"]
