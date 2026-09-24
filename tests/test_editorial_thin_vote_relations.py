"""The thesis-fit vote reads who is in each shot and how they relate to the owner.

A month whose account centres on a newborn is still the owner's month: the owner's partner in it
is not "unrelated to the main subject". Synthetic lines, roles only.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

from immich_memories.analysis.editorial_story_replies import film_close_family
from immich_memories.analysis.editorial_thin_gates import ThinGates
from immich_memories.analysis.editorial_thin_layer import ThinPolish
from immich_memories.config_models_llm import LLMConfig
from tests.editorial_subject_family import film_of, subject_people

PARTNER = "with Person A (partner; aged 34; inner circle)"
SON = "with Person B (son; 5 days old; recurring circle)"
FRIEND = "with Person C (friend; aged 36; recurring circle)"


class FitJudge:
    """Names every row whose line says `filler`, in both orders, and keeps each prompt."""

    def __init__(self) -> None:
        self.config = SimpleNamespace(llm=LLMConfig(model="model-a"))
        self.prompts: list[str] = []

    def ask(self, stage, prompt, **_kwargs):
        self.prompts.append(prompt)
        weak = re.findall(r"^(P\d+): .*filler", prompt, re.MULTILINE)
        return json.dumps({"weak": dict.fromkeys(weak, "unrelated to the main subject")})

    @property
    def calls(self) -> list[str]:
        return self.prompts


class Standing:
    """# WHY: the standing gate's own reader; the test states its verdicts."""

    def __init__(self, scores=None) -> None:
        self.scores = scores or {}

    def ensure(self, assets, needs=None):
        return None

    def needs(self, asset, weight, story_key=""):
        return 2

    def stands(self, asset, weight, story_key=""):
        return self.scores.get(asset, 2) > 0


class Audience:
    """# WHY: the production audience gate needs detector rows this fixture has no source for."""

    def verdict_of(self, _unit):
        return "share"


STORY = SimpleNamespace(
    stories=[{"key": "S001", "title": "The newborn", "episodes": ["e1"], "gate": "remarkable"}],
    episodes=[SimpleNamespace(key="e1", moments=["m1"])],
    audit={"hints": {}},
)


def carrier(asset):
    return {
        "asset_id": asset,
        "story_episode": "S001",
        "taken": f"2024-02-0{asset[-1]}T09:00:00",
        "moment": f"m-{asset}",
        "seconds": 4.0,
        "kind": "still",
        "favourite": False,
    }


def polish_of(tmp_path, lines, *, standing=None, subject="", record=lambda _n, _p: None, **film):
    judge = FitJudge()
    polish = ThinPolish(
        bank_dir=tmp_path,
        read_period=lambda _stories: ("A month of domestic life around a newborn.", {}),
    )
    cut = [carrier(asset) for asset in lines]
    moments = {"m1": list(lines)}
    kept = polish.polish(
        cut,
        judge=judge,
        gates=ThinGates(standing or Standing(), Audience(), thumbnail_hash=lambda _a: None),
        catalogue=polish.catalogue_of(STORY, moments, drafted=cut),
        contract="contract",
        line_of=lines.get,
        record=record,
        subject=subject,
        **film,
    )
    return [c["asset_id"] for c in kept], judge


def test_a_close_family_members_only_shot_survives_a_vote_that_names_it(tmp_path):
    written: dict[str, dict] = {}
    kept, _judge = polish_of(
        tmp_path,
        {
            "a1": f"2024-02-01 09:00 | a baby asleep | {SON}",
            "a2": f"2024-02-02 09:00 | filler: a woman by a window | {PARTNER}",
            "a3": f"2024-02-03 09:00 | filler: a man at a table | {FRIEND}",
        },
        record=lambda name, payload: written.__setitem__(name, dict(payload)),
    )
    assert kept == ["a1", "a2"]
    verdict = written["thin-polish"]["verdicts"]["a2"]
    assert verdict["state"] == "kept" and verdict["named_by"] == 2
    assert "partner" in verdict["held_by"]


def test_a_close_family_member_with_another_shot_is_voted_like_anyone(tmp_path):
    kept, _judge = polish_of(
        tmp_path,
        {
            "a1": f"2024-02-01 09:00 | a woman holding a baby | {PARTNER}",
            "a2": f"2024-02-02 09:00 | filler: a woman by a window | {PARTNER}",
        },
    )
    assert kept == ["a1"]


def test_the_gates_still_refuse_a_close_family_members_only_shot(tmp_path):
    kept, _judge = polish_of(
        tmp_path,
        {
            "a1": f"2024-02-01 09:00 | a baby asleep | {SON}",
            "a2": f"2024-02-02 09:00 | a woman by a window | {PARTNER}",
        },
        standing=Standing({"a2": 0}),
    )
    assert kept == ["a1"]


def test_the_vote_is_told_whose_film_it_is_and_each_shots_relation_to_the_owner(tmp_path):
    _kept, judge = polish_of(
        tmp_path,
        {
            "a1": f"2024-02-01 09:00 | a baby asleep | {SON}",
            "a2": f"2024-02-02 09:00 | a woman by a window | {PARTNER}",
            "a3": f"2024-02-03 09:00 | a man at a table | {FRIEND}",
        },
        subject="the newborn",
    )
    prompt = judge.prompts[0]
    assert "THE FILM'S SUBJECT\nthe newborn" in prompt
    rows = dict(re.findall(r"^(P\d+): (.*)$", prompt, re.MULTILINE))
    assert "the owner's close family: partner" in rows["P02"]
    assert "the owner's close family: son" in rows["P01"]
    assert "close family" not in rows["P03"]


def test_a_person_film_holds_the_only_shot_of_its_subjects_father(tmp_path):
    """To the owner he is an in-law; in a film of his daughter he is her father, and a vote that
    calls his only shot filler does not remove it. A month film still lets the vote remove it."""
    _people, relation = subject_people(tmp_path)
    father = f"with Her Father ({relation['Her Father']})"
    lines = {
        "a1": f"2024-02-01 09:00 | a woman in a garden | {PARTNER}",
        "a2": f"2024-02-02 09:00 | filler: a man on a bench | {father}",
        "a3": f"2024-02-03 09:00 | a man at a table | {FRIEND}",
    }
    kept, rows = {}, {}
    for product in ("person_spotlight", "monthly_highlights"):
        film = film_of(tmp_path / product, product)
        kept[product], judge = polish_of(
            tmp_path / product, lines, close_family=film_close_family(film)
        )
        rows[product] = dict(re.findall(r"^(P\d+): (.*)$", judge.prompts[0], re.MULTILINE))

    assert kept == {"person_spotlight": ["a1", "a2", "a3"], "monthly_highlights": ["a1", "a3"]}
    assert "the film's subject's close family: parent" in rows["person_spotlight"]["P02"]
    assert "close family" not in rows["monthly_highlights"]["P02"]
