"""A large yearly memory keeps every story while bounding each model request."""

import json
import re
from collections import Counter
from datetime import date, timedelta

import pytest

from immich_memories.analysis.editorial_story_reading import read_period_story
from immich_memories.analysis.editorial_story_weight_contract import StoryWeightDecisionError
from immich_memories.analysis.editorial_text_failures import TextCompletionFailure
from tests.test_editorial_story_reading import ScriptedJudge, episode_row, opened, page_answer


class YearJudge:
    # WHY: replace only the external text model; exercise the real episode reader,
    # grouping, validation, weighting, and resulting story ledger.
    def __init__(self, join=()):
        self.weighing = []
        self.join = join

    def ask(self, stage, prompt, max_tokens=0):
        if stage.startswith("story-episodes"):
            rows = offered_rows(prompt)
            pairs = [(r["reading"], f"S{index + 1:04d}") for index, r in enumerate(rows)]
            titles = [r["what_happened"] for r in rows]
            return page_answer(
                pairs,
                [opened(key, title) for (_, key), title in zip(pairs, titles, strict=True)],
            )
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


def _row(index, day):
    """One banked episode row on a given day, titled so its global key stays recognisable."""
    return {
        **episode_row(index),
        "taken": f"{day}T12:00:00",
        "last_taken": f"{day}T12:00:00",
        "what_happened": f"Outing {index + 1:04d}",
    }


def offered_rows(prompt):
    """The rows one month page offered, exactly as the reader was handed them."""
    return json.loads(prompt.split("EPISODES TO PLACE (", 1)[1].split(")\n", 1)[1])


def read_year(judge, count=578, record=lambda _: None):
    evidence = [
        {
            **episode_row(i),
            "taken": f"{date(2030, 1, 1) + timedelta(days=i // 2)}T12:00:00",
            "last_taken": f"{date(2030, 1, 1) + timedelta(days=i // 2)}T12:00:00",
            "what_happened": f"Outing {i + 1:04d}",
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


@pytest.mark.parametrize("count", [8, 70])
def test_multiple_centres_are_resolved_once_even_when_their_rows_fit(count):
    model = YearJudge()

    def reply(stage, prompt):
        answer = json.loads(model.ask(stage, prompt))
        if stage.startswith("story-understanding"):
            answer["about"] = [
                key
                for story in answer["stories"]
                for key in story["episodes"]
                if key in {"S0001", "S0002", "S0003", "S0004"}
            ]
        elif stage.startswith("story-weighing") and "-candidate-context" not in stage:
            offered = re.search(r"THE READING SAYS THIS MEMORY IS ABOUT: (.*)", prompt)[1]
            # WHY: the external model repeats every nominated centre while it
            # also has to weigh a table, as observed in the real short control.
            answer["about"] = offered.split(", ")
        return json.dumps(answer)

    result = read_period_story(
        ScriptedJudge(reply),
        evidence=[_row(i, date(2030, 1, 1) + timedelta(days=i * 2)) for i in range(count)],
        contract="The requested memory.",
        prior={},
        enrich=lambda episodes: {
            e.key: {"day": e.facts[0]["taken"][:10], "moments": 2} for e in episodes
        },
    )

    assert len(result.stories) == count
    assert [story["key"] for story in result.stories if story["weight"] == "dominant"] == ["K01"]
    assert all(story["weight"] == "minor" for story in result.stories if story["key"] != "K01")
    assert sum("-candidate-context" in stage for stage, *_ in model.weighing) == 2
    assert not any("repair" in stage or "part-" in stage for stage, *_ in model.weighing)


@pytest.mark.parametrize("comparison_weights", [True, False])
def test_large_shared_context_is_compared_before_weighing_every_story_in_bounded_pages(
    comparison_weights,
):
    model = YearJudge()
    central = {f"S{i:04d}" for i in range(1, 48)}

    def reply(stage, prompt):
        raw = model.ask(stage, prompt)
        if stage.startswith("story-understanding"):
            answer = json.loads(raw)
            answer["about"] = [
                episode
                for story in answer["stories"]
                for episode in story["episodes"]
                if episode in central
            ]
            return json.dumps(answer)
        if "-candidate-context" in stage:
            answer = json.loads(raw)
            if not comparison_weights:
                return json.dumps({"about": answer["about"]})
            answer["weights"] = dict.fromkeys(answer["weights"], "none")
            answer["retitle"] = {"K02": "A provisional comparison title"}
            return json.dumps(answer)
        return raw

    def day(index):
        if index < 47:
            return date(2030, 1, 1) + timedelta(days=index * 2)
        return date(2030, 8, 1) + timedelta(days=max(0, index - 62) * 2)

    result = read_period_story(
        ScriptedJudge(reply),
        evidence=[_row(i, day(i)) for i in range(66)],
        contract="The whole year.",
        prior={},
        enrich=lambda episodes: {
            e.key: {"day": e.facts[0]["taken"][:10], "moments": 2} for e in episodes
        },
    )

    assert len(result.stories) == 66
    candidates = {f"K{i:02d}" for i in range(1, 48)}
    occasion = {f"K{i:02d}" for i in range(48, 64)}
    assert max(len(prompt) for _, prompt, _, _ in model.weighing) <= 48_000
    assert max(len(keys) for _, _, _, keys in model.weighing) <= 60
    assert all(story["weight"] == "minor" for story in result.stories if story["key"] != "K01")
    assert (
        next(story["title"] for story in result.stories if story["key"] == "K02") == "Outing 0002"
    )
    for order in ("source", "reversed"):
        comparisons = [
            set(keys)
            for stage, _, _, keys in model.weighing
            if order in stage and "-candidate-context" in stage
        ]
        assert comparisons == [candidates]
        pages = [
            set(keys)
            for stage, _, _, keys in model.weighing
            if order in stage and "-candidate-context" not in stage
        ]
        assert set.union(*pages) == {f"K{i:02d}" for i in range(1, 67)}
        assert all("K01" in keys for keys in pages)
        assert all(occasion <= keys for keys in pages if keys & occasion)


def test_a_shared_context_that_exceeds_the_character_limit_is_never_sent():
    model = YearJudge()

    def reply(stage, prompt):
        raw = model.ask(stage, prompt)
        if stage.startswith("story-understanding"):
            answer = json.loads(raw)
            answer["thesis"] = "The family visits and outings throughout the year. " * 1000
            return json.dumps(answer)
        return raw

    with pytest.raises(ValueError, match="context exceeds the bounded request size"):
        read_year(ScriptedJudge(reply), count=130)

    assert model.weighing == []


@pytest.mark.parametrize(
    "invalid_about",
    [None, "K01", ["K99"], ["K01", "K01"], ["K01", "K02", "K03"], [["K01"]]],
)
def test_central_confirmation_repairs_invalid_choices_before_full_year_weighting(invalid_about):
    model = YearJudge()
    candidates = {f"S{i:04d}" for i in range(1, 61)}

    def reply(stage, prompt):
        answer = json.loads(model.ask(stage, prompt))
        if stage.startswith("story-understanding"):
            answer["about"] = [
                episode
                for story in answer["stories"]
                for episode in story["episodes"]
                if episode in candidates
            ]
        elif stage == "story-weighing-source-candidate-context":
            return json.dumps({"about": invalid_about})
        elif "-candidate-context" in stage:
            return '{"about": ["K01"]}'
        return json.dumps(answer)

    result = read_period_story(
        ScriptedJudge(reply),
        evidence=[_row(i, date(2030, 1, 1) + timedelta(days=i * 2)) for i in range(130)],
        contract="The whole year.",
        prior={},
        enrich=lambda episodes: {
            e.key: {"day": e.facts[0]["taken"][:10], "moments": 2} for e in episodes
        },
    )

    assert len(result.stories) == 130
    assert [story["key"] for story in result.stories if story["weight"] == "dominant"] == ["K01"]
    assert all(story["weight"] == "minor" for story in result.stories if story["key"] != "K01")
    comparisons = [stage for stage, _, _, _ in model.weighing if "-candidate-context" in stage]
    assert comparisons == [
        "story-weighing-source-candidate-context",
        "story-weighing-source-candidate-context-retry",
        "story-weighing-reversed-candidate-context",
    ]


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


def test_an_exhausted_page_is_read_in_smaller_groups_without_using_partial_edits():
    model = YearJudge()

    def reply(stage, prompt):
        answer = model.ask(stage, prompt)
        if not stage.startswith("story-weighing"):
            return answer
        keys = re.findall(r"^(K\d+) \|", prompt, re.MULTILINE)
        value = json.loads(answer)
        if "K89" in keys and len(keys) > 32:
            del value["weights"]["K89"]
            value["retitle"] = {"K89": "An edit from a rejected answer"}
            value["join"] = [["K89", "K90"]]
        return json.dumps(value)

    result = read_year(ScriptedJudge(reply), count=200)

    assert len(result.stories) == 200
    recovered = next(s for s in result.stories if s["key"] == "K89")
    assert recovered["weight"] == "minor"
    assert recovered["title"] == "Outing 0089"
    assert recovered["episodes"] == ["S0089"]
    assert any("K89" in keys and len(keys) <= 32 for _, _, _, keys in model.weighing)


@pytest.mark.parametrize("truncated_attempt", ["initial", "repair"])
def test_truncated_weighing_replies_recover_in_smaller_complete_groups(truncated_attempt):
    model = YearJudge()

    def reply(stage, prompt):
        answer = model.ask(stage, prompt)
        keys = re.findall(r"^(K\d+) \|", prompt, re.MULTILINE)
        if "K89" not in keys or len(keys) <= 32:
            return answer
        if truncated_attempt == "initial" or stage.endswith("-repair"):
            raise TextCompletionFailure(
                [
                    {
                        "outcome": "incomplete",
                        "raw": '{"weights":',
                        "max_tokens": budget,
                        "error": "LLM returned incomplete content",
                    }
                    for budget in (1740, 3480)
                ]
            )
        value = json.loads(answer)
        del value["weights"]["K89"]
        return json.dumps(value)

    result = read_year(ScriptedJudge(reply), count=200)

    assert len(result.stories) == 200
    assert all(story["weight"] == "minor" for story in result.stories if story["key"] != "K01")
    for order in ("source", "reversed"):
        recovered = [
            set(keys)
            for stage, _, _, keys in model.weighing
            if order in stage and "K89" in keys and len(keys) <= 32
        ]
        assert recovered and all({"K89", "K90"} <= keys for keys in recovered)
