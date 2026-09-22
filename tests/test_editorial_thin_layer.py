"""The model polish over a rules draft: what it reads, what it removes, and when it stands down."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from types import SimpleNamespace

from immich_memories.analysis.editorial_thin_gates import ThinGates
from immich_memories.analysis.editorial_thin_layer import ThinPolish, catalogued_period
from immich_memories.config_models_llm import LLMConfig
from immich_memories.timeperiod import DateRange


class FitJudge:
    def __init__(self) -> None:
        self.config = SimpleNamespace(llm=LLMConfig(model="model-a"))
        self.calls: list[str] = []

    def ask(self, stage, prompt, **_kwargs):
        self.calls.append(stage)
        weak = re.findall(r"^(P\d+): .*filler", prompt, re.MULTILINE)
        return json.dumps({"weak": dict.fromkeys(weak, "adds nothing")})


class Standing:
    """# WHY: the standing gate's own reader; the answers are stated so the layer's route is
    read against a verdict the test chose."""

    def __init__(self, scores=None) -> None:
        self.scores = scores or {}

    def ensure(self, assets):
        return None

    def stands(self, asset, weight, story_key=""):
        return self.scores.get(asset, 2) > 0


class Audience:
    """# WHY: the production audience gate needs detector rows this fixture has no source for."""

    def __init__(self, verdicts=None) -> None:
        self.verdicts = verdicts or {}

    def verdict_of(self, unit):
        return self.verdicts.get(unit["asset_id"], "share")


def carrier(asset, story, *, favourite=False):
    return {
        "asset_id": asset,
        "story_episode": story,
        "taken": f"2024-02-0{asset[-1]}T09:00:00",
        "moment": f"m-{asset}",
        "seconds": 4.0,
        "kind": "still",
        "favourite": favourite,
    }


STORY = SimpleNamespace(
    stories=[
        {"key": "S001", "title": "First", "episodes": ["e1"], "gate": "remarkable"},
        {"key": "S002", "title": "Second", "episodes": ["e2"], "gate": "remarkable"},
    ],
    episodes=[
        SimpleNamespace(key="e1", moments=["m1"]),
        SimpleNamespace(key="e2", moments=["m2"]),
    ],
    audit={"hints": {"e1": {"day": "2024-02-01"}}},
)
MOMENTS = {"m1": ["a1"], "m2": ["a2", "a3"]}


def run(polish, carriers, judge, lines, *, gates=None, record=lambda _n, _p: None):
    return polish.polish(
        carriers,
        judge=judge,
        gates=gates or ThinGates(Standing(), Audience(), thumbnail_hash=lambda _a: None),
        catalogue=polish.catalogue_of(STORY, MOMENTS),
        contract="contract",
        line_of=lines.get,
        record=record,
    )


def test_a_period_the_library_has_no_account_of_is_not_polished(tmp_path):
    judge = FitJudge()
    polish = ThinPolish(account="", bank_dir=tmp_path)
    cut = [carrier("a1", "S001"), carrier("a2", "S002")]
    assert run(polish, cut, judge, {"a1": "filler", "a2": "the afternoon"}) == cut
    assert judge.calls == []


def test_only_the_shot_the_vote_named_leaves_the_cut(tmp_path):
    judge = FitJudge()
    polish = ThinPolish(account="the month a family moved", bank_dir=tmp_path)
    cut = [carrier("a1", "S001"), carrier("a2", "S002"), carrier("a3", "S002")]
    kept = run(polish, cut, judge, {"a1": "filler", "a2": "the afternoon", "a3": "the evening"})
    assert [c["asset_id"] for c in kept] == ["a2", "a3"]


def test_a_starred_shot_the_vote_named_keeps_its_place(tmp_path):
    judge = FitJudge()
    polish = ThinPolish(account="the month a family moved", bank_dir=tmp_path)
    cut = [carrier("a1", "S001", favourite=True), carrier("a2", "S002")]
    kept = run(polish, cut, judge, {"a1": "filler", "a2": "the afternoon"})
    assert [c["asset_id"] for c in kept] == ["a1", "a2"]


def test_a_shot_the_gates_refuse_never_reaches_the_vote(tmp_path):
    judge = FitJudge()
    polish = ThinPolish(account="the month a family moved", bank_dir=tmp_path)
    written: dict[str, dict] = {}
    cut = [carrier("a1", "S001"), carrier("a2", "S002")]
    kept = run(
        polish,
        cut,
        judge,
        {"a1": "the morning", "a2": "the afternoon"},
        gates=ThinGates(Standing({"a1": 0}), Audience(), thumbnail_hash=lambda _a: None),
        record=lambda name, payload: written.__setitem__(name, dict(payload)),
    )
    assert [c["asset_id"] for c in kept] == ["a2"]
    audit = written["thin-polish"]
    assert audit["draft_shots"] == 2
    assert [row["rule"] for row in audit["refused_by_the_gates"]] == ["standing"]
    assert audit["voted"] == 1


def test_the_polish_records_what_it_asked_and_what_it_held(tmp_path):
    judge = FitJudge()
    polish = ThinPolish(account="the month a family moved", bank_dir=tmp_path)
    written: dict[str, dict] = {}
    cut = [carrier("a1", "S001", favourite=True), carrier("a2", "S002")]
    run(
        polish,
        cut,
        judge,
        {"a1": "filler", "a2": "the afternoon"},
        record=lambda name, payload: written.__setitem__(name, dict(payload)),
    )
    audit = written["thin-polish"]
    assert audit["ran"] is True
    assert audit["voted"] == 2
    assert audit["removed_by_the_vote"] == []
    assert audit["held_by_the_owner"] == ["a1"]


def test_a_second_run_over_the_same_bank_asks_the_model_nothing(tmp_path):
    polish = ThinPolish(account="the month a family moved", bank_dir=tmp_path)
    cut = [carrier("a1", "S001"), carrier("a2", "S002")]
    lines = {"a1": "filler", "a2": "the afternoon"}
    first = FitJudge()
    cold = run(polish, cut, first, lines)
    second = FitJudge()
    warm = run(polish, cut, second, lines)
    assert [c["asset_id"] for c in warm] == [c["asset_id"] for c in cold]
    assert first.calls and second.calls == []


def test_story_membership_follows_the_episodes_own_moments(tmp_path):
    catalogue = ThinPolish(account="an account", bank_dir=tmp_path).catalogue_of(STORY, MOMENTS)
    assert catalogue is not None
    assert {story.key: story.asset_ids for story in catalogue.stories} == {
        "S001": ("a1",),
        "S002": ("a2", "a3"),
    }
    assert catalogue.hints["e1"]["day"] == "2024-02-01"


def test_only_a_whole_month_or_a_whole_year_names_a_catalogued_period():
    def span(first, last):
        return (DateRange(datetime(*first, tzinfo=UTC), datetime(*last, tzinfo=UTC)),)

    assert catalogued_period(span((2024, 2, 1), (2024, 2, 29, 23, 59, 59))) == "2024-02"
    assert catalogued_period(span((2024, 1, 1), (2024, 12, 31, 23, 59, 59))) == "2024"
    assert catalogued_period(span((2024, 2, 3), (2024, 2, 29, 23, 59, 59))) == ""
    assert catalogued_period(span((2024, 2, 1), (2024, 3, 15, 23, 59, 59))) == ""
    assert catalogued_period(()) == ""
