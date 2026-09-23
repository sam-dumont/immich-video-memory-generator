"""One whole polish over a rules draft, through the production gates, vote, picker and refill."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from immich_memories.analysis.editorial_story_standing import StandingGate
from immich_memories.analysis.editorial_thin_gates import ThinGates
from immich_memories.analysis.editorial_thin_layer import ThinPolish
from immich_memories.config_models_llm import LLMConfig

JUNK = "an empty worktop"
DOUBTED = "a plain corridor"
UNSTEADY = "a blurred wall"


class PolishJudge:
    """Answers the two questions this layer asks, and banks every request by its exact prompt."""

    def __init__(self, bank=None, *, require_hits=False) -> None:
        self.config = SimpleNamespace(llm=LLMConfig(model="model-a"))
        self.bank = {} if bank is None else bank
        self.calls: list[str] = []
        self.require_hits = require_hits

    def ask(self, stage, prompt, max_tokens=260, *, accepts=None, **options):
        key = (stage, prompt, max_tokens)
        hit = key in self.bank
        if self.require_hits and not hit:
            pytest.fail(f"a replay asked something new at {stage}")
        if not hit:
            self.bank[key] = self.answer(stage, prompt)
        self.calls.append(stage)
        return self.bank[key]

    @staticmethod
    def answer(stage, prompt):
        if stage.startswith("standing-"):
            weak = re.findall(rf"^(P\d+): .*{UNSTEADY}", prompt, re.MULTILINE)
            return json.dumps({"weak": dict.fromkeys(weak, "nothing stands in it")})
        if stage.startswith("thesis-fit-"):
            named = re.findall(rf"^(P\d+): .*{JUNK}", prompt, re.MULTILINE)
            if stage.endswith("-source"):
                # one order doubts the starred corridor; the other does not
                named += re.findall(rf"^(P\d+): .*{DOUBTED}", prompt, re.MULTILINE)
            return json.dumps({"weak": dict.fromkeys(named, "adds nothing")})
        if stage.startswith("story-pick-"):
            labels = re.findall(r"^(M\d{2}) \|", prompt, re.MULTILINE)
            return json.dumps({"keep": labels[:1]})
        raise AssertionError(f"the polish asked an unexpected question: {stage}")


class Audience:
    """# WHY: the production audience gate reads detector rows and a picture-evidence overlay
    that this fixture has no source for; it has its own tests. Every picture is showable here."""

    def verdict_of(self, unit):
        return "share"


LINES = {
    "a1": f"2024-02-01T09:00 | {JUNK}",
    "a2": f"2024-02-02T09:00 | {DOUBTED}",
    "g1": f"2024-02-05T09:00 | {UNSTEADY}",
    "g2": "2024-02-05T09:30 | the whole table at lunch",
    "g3": "2024-02-05T10:00 | the garden after lunch",
    "spare": "2024-02-01T18:00 | the evening walk",
    "n1": "2024-02-09T09:00 | the first steps",
}


def unit(asset, story, *, moment, favourite=False, seconds=4.0):
    return {
        "asset_id": asset,
        "story_episode": story,
        "taken": LINES[asset][:16].replace(" ", "T")[:16] + ":00",
        "moment": moment,
        "seconds": seconds,
        "kind": "still",
        "favourite": favourite,
        "line": LINES[asset],
    }


DRAFT = [
    unit("a1", "S1", moment="m1"),
    unit("a2", "S1", moment="m2", favourite=True),
    unit("g1", "S2", moment="q1"),
]
POOL = {
    "S1": [DRAFT[0], DRAFT[1], unit("spare", "S1", moment="m3")],
    "S2": [DRAFT[2], unit("g2", "S2", moment="q1"), unit("g3", "S2", moment="q2")],
    "S3": [unit("n1", "S3", moment="p1")],
}
STORY = SimpleNamespace(
    stories=[
        {
            "key": "S1",
            "title": "At home",
            "episodes": ["e1"],
            "gate": "maybe",
            "first_day": "2024-02-01",
        },
        {
            "key": "S2",
            "title": "The lunch",
            "episodes": ["e2"],
            "gate": "maybe",
            "first_day": "2024-02-05",
        },
        {
            "key": "S3",
            "title": "A first",
            "episodes": ["e3"],
            "gate": "maybe",
            "first_day": "2024-02-09",
        },
    ],
    episodes=[
        SimpleNamespace(key="e1", moments=["m1", "m2", "m3"]),
        SimpleNamespace(key="e2", moments=["q1", "q2"]),
        SimpleNamespace(key="e3", moments=["p1"]),
    ],
    audit={"hints": {"e1": {"day": "2024-02-01"}}},
)
MOMENTS = {
    "m1": ["a1"],
    "m2": ["a2"],
    "m3": ["spare"],
    "q1": ["g1", "g2"],
    "q2": ["g3"],
    "p1": ["n1"],
}


def polish_once(tmp_path, judge):
    unit_by_asset = {row["asset_id"]: ("fam", row) for rows in POOL.values() for row in rows}
    standing = StandingGate(
        judge,
        line_of=LINES.get,
        life=lambda _asset: True,
        unit_by_asset=unit_by_asset,
        pictures_of={"S1": 3, "S2": 3, "S3": 3},
        bank={},
        save=None,
        calls={"standing_rounds": 0},
    )
    layer = ThinPolish(account="The month a family found its feet.", bank_dir=tmp_path)
    return layer.polish(
        DRAFT,
        judge=judge,
        gates=ThinGates(standing=standing, audience=Audience(), thumbnail_hash=lambda _a: None),
        catalogue=layer.catalogue_of(STORY, MOMENTS, {"n1": "the first of them"}),
        contract="contract",
        line_of=LINES.get,
        record=lambda _name, _payload: None,
        candidates_of=lambda key: POOL.get(key, []),
        content_cap=52.5,
    )


def test_one_polish_drops_the_junk_keeps_the_star_and_refills_what_the_gates_took(tmp_path):
    judge = PolishJudge()
    cut = polish_once(tmp_path, judge)

    kept = [row["asset_id"] for row in cut]
    # the shot both orders named is gone; the starred one an order doubted keeps its place
    assert "a1" not in kept
    assert "a2" in kept
    # the shot the standing gate refused is replaced from its own story, its own moment first
    assert "g1" not in kept
    assert "g2" in kept
    # the story the catalogue records something about, silent in the draft, now speaks
    assert "n1" in kept
    assert kept == sorted(kept, key=lambda asset: LINES[asset])


def test_the_polish_spends_one_standing_round_one_vote_one_pick_and_one_check(tmp_path):
    judge = PolishJudge()
    polish_once(tmp_path, judge)

    # every question is asked in both orders: the draft's standing, then every candidate's in one
    # block, then the vote over the cut, one pick for the only contested page, and the re-check
    assert sum(1 for stage in judge.calls if stage.startswith("standing-")) == 4
    assert sum(1 for stage in judge.calls if stage.startswith("thesis-fit-")) == 4
    assert sum(1 for stage in judge.calls if stage.startswith("story-pick-")) == 2
    assert len(judge.calls) == 10


def test_a_second_run_over_the_same_bank_asks_nothing_and_cuts_the_same_film(tmp_path):
    bank: dict = {}
    cold = polish_once(tmp_path, PolishJudge(bank))
    asked_cold = len(bank)
    # `require_hits` fails the run on the first question the bank has no answer for
    warm = polish_once(tmp_path, PolishJudge(bank, require_hits=True))

    assert [row["asset_id"] for row in warm] == [row["asset_id"] for row in cold]
    assert len(bank) == asked_cold
