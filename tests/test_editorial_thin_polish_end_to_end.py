"""One whole polish over a rules draft, through the production gates, vote, picker and refill."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from immich_memories.analysis.editorial_standing_facts import carries_nothing
from immich_memories.analysis.editorial_story_standing import StandingGate
from immich_memories.analysis.editorial_thin_gates import ThinGates
from immich_memories.analysis.editorial_thin_layer import ThinPolish
from immich_memories.config_models_llm import LLMConfig

ACCOUNT = "The month a family found its feet."
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


# The frame head reads the blurred wall as an accidental frame; every other shot carries a moment.
HEADS = {"g1": {"frame_kind": "accidental_or_blurred_frame"}}


def standing_gate():
    return StandingGate(
        lambda asset: 0 if carries_nothing(HEADS.get(asset, {}), LINES[asset]) else 2,
        line_of=LINES.get,
        life=lambda _asset: True,
        unit_by_asset={row["asset_id"]: ("fam", row) for rows in POOL.values() for row in rows},
        pictures_of={"S1": 3, "S2": 3, "S3": 3},
    )


def polish_once(tmp_path, judge):
    standing = standing_gate()
    layer = ThinPolish(bank_dir=tmp_path, read_period=lambda _stories: (ACCOUNT, {}))
    return layer.polish(
        DRAFT,
        judge=judge,
        gates=ThinGates(standing=standing, audience=Audience(), thumbnail_hash=lambda _a: None),
        catalogue=layer.catalogue_of(STORY, MOMENTS, {"n1": "the first of them"}, drafted=DRAFT),
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


def test_the_polish_spends_one_vote_one_pick_and_one_check_and_no_standing_question(tmp_path):
    judge = PolishJudge()
    polish_once(tmp_path, judge)

    # the vote named a shot, so it asked its second order; the one contested page is picked in
    # one order, the facts say whether its pick stands, and the re-check named nobody in its
    # first order
    assert sum(1 for stage in judge.calls if stage.startswith("thesis-fit-")) == 3
    assert sum(1 for stage in judge.calls if stage.startswith("story-pick-")) == 1
    assert len(judge.calls) == 4


def test_a_second_run_over_the_same_bank_asks_nothing_and_cuts_the_same_film(tmp_path):
    bank: dict = {}
    cold = polish_once(tmp_path, PolishJudge(bank))
    asked_cold = len(bank)
    # `require_hits` fails the run on the first question the bank has no answer for
    warm = polish_once(tmp_path, PolishJudge(bank, require_hits=True))

    assert [row["asset_id"] for row in warm] == [row["asset_id"] for row in cold]
    assert len(bank) == asked_cold


def banked_records(tmp_path):
    """The record the reading of S3's episode left behind, read back the way a run reads it."""
    from contextlib import closing

    from immich_memories.analysis.catalogue_runtime import banked_notable_records
    from immich_memories.store.episode_readings import (
        BankedEpisodeReading,
        EpisodeReadingIdentity,
        EpisodeReadingStore,
        EpisodeRepresentative,
    )

    bank = tmp_path / "annotations.sqlite"
    identity = EpisodeReadingIdentity(
        group_id="e3", producer_key="producer-a", evidence_key="evidence-a"
    )
    with closing(EpisodeReadingStore(bank)) as store:
        store.remember(
            [
                BankedEpisodeReading(
                    identity=identity,
                    full_asset_ids=("n1",),
                    what_happened="A first.",
                    representatives=(EpisodeRepresentative("n1", "the only frame"),),
                    cull_decisions=(),
                    notable_moments=(EpisodeRepresentative("n1", "the first of them"),),
                )
            ]
        )
    return banked_notable_records([identity], store_path=bank)


def test_a_story_the_bank_records_something_about_is_seated_from_the_bank(tmp_path):
    """No caller hands the records in: the layer reads them off the period's own readings."""
    judge = PolishJudge()
    standing = standing_gate()
    records = banked_records(tmp_path)
    layer = ThinPolish(bank_dir=tmp_path, read_period=lambda _stories: (ACCOUNT, records))

    cut = layer.polish(
        DRAFT,
        judge=judge,
        gates=ThinGates(standing=standing, audience=Audience(), thumbnail_hash=lambda _a: None),
        catalogue=layer.catalogue_of(STORY, MOMENTS, drafted=DRAFT),
        contract="contract",
        line_of=LINES.get,
        record=lambda _name, _payload: None,
        candidates_of=lambda key: POOL.get(key, []),
        content_cap=52.5,
    )

    assert "n1" in [row["asset_id"] for row in cut]


def test_every_vote_including_the_newcomers_re_check_is_banked_for_the_next_run(tmp_path):
    """The re-check over a refilled cut is a paid answer like any other, so it is read back.

    Both runs get their own judge with an empty bank of its own, so the only thing that can
    keep the second one from voting again is the layer's own `thesis-fit.private.json`. The
    picks that remain are the judgment cache's to answer, which production keeps in SQLite and
    this fixture's judge stands in for.
    """
    cold = polish_once(tmp_path, PolishJudge())
    second = PolishJudge()

    warm = polish_once(tmp_path, second)

    assert [stage for stage in second.calls if stage.startswith("thesis-fit-")] == []
    assert [row["asset_id"] for row in warm] == [row["asset_id"] for row in cold]


def test_a_shot_is_never_voted_on_alone_when_the_bank_holds_its_neighbours(tmp_path):
    """A reject-only vote names whatever it is shown when it has nothing to compare against."""
    asked = []

    class Watching(PolishJudge):
        def ask(self, stage, prompt, max_tokens=260, **options):
            if stage.startswith("thesis-fit-"):
                asked.append(prompt.count("\nP"))
            return super().ask(stage, prompt, max_tokens, **options)

    polish_once(tmp_path, Watching())
    first_round = list(asked)
    asked.clear()

    polish_once(tmp_path, Watching())

    assert first_round and min(first_round) > 1
    assert asked == []
