"""A month page is banked by its own bytes, so reading it again costs nothing.

The page prompt carries no contract, no wall alias and no earlier page's summaries, so it
is a pure function of that month's episode rows. The judgment bank is keyed on the exact
request, which means the second read of a year, and a February read inside or outside one,
all answer from the bank.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

from immich_memories.analysis.editorial_story_reading import read_period_story
from tests.test_editorial_duration_planner_integration import EditorialJudge
from tests.test_editorial_story_reading import episode_row, opened, page_answer, weighing


class MonthBankJudge(EditorialJudge):
    # WHY: stands in for the text model. Its bank is keyed on prompt and budget exactly like
    # the judgment cache, so a hit here is a call the real bank would also answer for free.
    def ask(self, stage, prompt, max_tokens=260, **options):
        if self.require_hits and not stage.startswith("story-episodes"):
            return self.answer(stage, prompt)  # only the month pages are replayed
        return super().ask(stage, prompt, max_tokens, **options)

    @staticmethod
    def answer(stage, prompt):
        if stage.startswith("story-episodes"):
            offered = re.findall(r'"reading": "(r\d+)"', prompt)
            return page_answer(
                [(reading, f"S{index + 1:04d}") for index, reading in enumerate(offered)],
                [
                    opened(f"S{index + 1:04d}", f"Outing {index + 1}")
                    for index in range(len(offered))
                ],
            )
        if stage.startswith("story-weighing"):
            keys = re.findall(r"^(K\d+) \|", prompt, re.MULTILINE)
            return weighing(dict.fromkeys(keys, "minor"))
        cards = re.findall(r'"episode": "(S\d{4})"', prompt)
        return json.dumps(
            {
                "thesis": "A year of outings.",
                "about": [],
                "stories": [
                    {"title": f"Outing {key}", "episodes": [key], "purpose": "An outing."}
                    for key in dict.fromkeys(cards)
                ],
                "uncertainties": [],
            }
        )


def _year_rows():
    start = date(2030, 1, 6)
    return [
        episode_row(index, day=str(start + timedelta(days=index * 13)), minute=index)
        for index in range(24)
    ]


def _read(judge, rows):
    return read_period_story(
        judge,
        evidence=rows,
        contract="The whole year.",
        prior={},
        enrich=lambda episodes: {
            episode.key: {"day": episode.facts[0]["taken"][:10], "moments": 1}
            for episode in episodes
        },
    )


def test_a_year_read_twice_asks_no_page_again_and_february_alone_asks_none():
    cold = MonthBankJudge()
    year = _read(cold, _year_rows())
    pages = year.audit["reading_calls"]["pages"]
    assert pages == len({row["taken"][:7] for row in _year_rows()})
    assert year.audit["reading_calls"] == {
        "pages": pages,
        "fresh": pages,
        "banked": 0,
        "retries": 0,
    }

    warm = _read(MonthBankJudge(cold.bank, require_hits=True), _year_rows())
    assert warm.audit["reading_calls"] == {
        "pages": pages,
        "fresh": 0,
        "banked": pages,
        "retries": 0,
    }

    february = [row for row in _year_rows() if row["taken"].startswith("2030-02")]
    alone = _read(MonthBankJudge(cold.bank, require_hits=True), february)
    assert alone.audit["reading_calls"]["fresh"] == 0
    assert alone.audit["reading_calls"]["banked"] == len(alone.audit["pages"])
    assert {page["evidence_key"] for page in alone.audit["pages"]} <= {
        page["evidence_key"] for page in year.audit["pages"]
    }
