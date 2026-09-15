"""A large yearly memory keeps every story while bounding each model request."""

import json
import re
from collections import Counter
from datetime import date, timedelta

import pytest

from immich_memories.analysis.editorial_story_reading import read_period_story
from immich_memories.analysis.editorial_story_weight_contract import StoryWeightDecisionError
from tests.test_editorial_story_reading import ScriptedJudge, fragment, opened, page_answer


class YearJudge:
    # WHY: replace only the external text model; exercise the real episode reader,
    # grouping, validation, weighting, and resulting story ledger.
    def __init__(self, join=()):
        self.weighing = []
        self.join = join

    def ask(self, stage, prompt, max_tokens=0):
        if stage.startswith("story-episodes"):
            rows, _ = json.JSONDecoder().raw_decode(prompt.split("NEW FRAGMENTS TO PLACE\n")[1])
            pairs = [(r["reading"], f"S{int(r['capture_group'][1:]) + 1:04d}") for r in rows]
            return page_answer(pairs, [opened(key, f"Outing {key}") for _, key in pairs])
        if stage.startswith("story-understanding"):
            cards = json.loads(prompt.rsplit("\n", 2)[-2])
            return json.dumps(
                {
                    "thesis": "A year of family visits and outings.",
                    "about": [c["episode"] for c in cards if c["episode"] == "S0001"],
                    "stories": [
                        {"title": c["title"], "episodes": [c["episode"]], "purpose": "A visit."}
                        for c in cards
                    ],
                }
            )
        keys = re.findall(r"^(K\d+) \|", prompt, re.MULTILINE)
        self.weighing.append((stage, prompt, max_tokens, keys))
        return json.dumps(
            {
                "about": ["K01"],
                "weights": {key: "minor" for key in keys if key != "K01"},
                "join": [self.join] if self.join and set(self.join) <= set(keys) else [],
            }
        )


def read_year(judge, count=578, record=lambda _: None):
    evidence = [
        {
            **fragment(i),
            "taken": f"{date(2030, 1, 1) + timedelta(days=i // 2)}T12:00:00",
        }
        for i in range(count)
    ]
    return read_period_story(
        judge,
        evidence=evidence,
        contract="One person over the full year.",
        prior={},
        record=record,
        enrich=lambda episodes: {
            e.key: {"day": e.facts[0]["taken"][:10], "moments": 2, "pictures": 2} for e in episodes
        },
    )


def test_yearly_reader_weighs_all_578_stories_in_bounded_requests():
    judge = YearJudge()

    result = read_year(judge)

    assert len(result.stories) == 578
    assert len(judge.weighing) > 2
    assert max(len(prompt) for _, prompt, _, _ in judge.weighing) <= 48_000
    assert max(budget for _, _, budget, _ in judge.weighing) <= 1_740
    for order in ("source", "reversed"):
        counts = Counter(
            key for stage, _, _, keys in judge.weighing if order in stage for key in keys
        )
        assert set(counts) == {s["key"] for s in result.stories}
        assert all(n == 1 for key, n in counts.items() if key not in {"K01", "K02"})
    assert [s["key"] for s in result.stories if s["weight"] == "dominant"] == ["K01"]
    assert all("A year of family visits and outings." in p for _, p, _, _ in judge.weighing)


def test_paging_keeps_two_stories_of_one_afternoon_available_to_join():
    result = read_year(YearJudge(join=("K119", "K120")), count=130)

    joined = next(s for s in result.stories if s["key"] == "K119")
    assert joined["episodes"] == ["S0119", "S0120"]
    assert len(result.stories) == 129


def test_a_failed_late_page_leaves_the_whole_year_incomplete():
    model = YearJudge(join=("K03", "K04"))
    records = []

    def reply(stage, prompt):
        answer = model.ask(stage, prompt)
        if stage.startswith("story-weighing") and re.search(r"^K130 \|", prompt, re.MULTILINE):
            return '{"about":[],"weights":{}}'
        return answer

    with pytest.raises(StoryWeightDecisionError):
        read_year(ScriptedJudge(reply), count=130, record=records.append)

    assert records[-1]["status"] == "incomplete"
    assert not any(r["stage"] == "story-weighing" for r in records[-1]["synthesis"])
    assert any(r["status"] == "complete" for r in records[-1]["synthesis"] if "status" in r)
