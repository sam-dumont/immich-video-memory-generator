"""The standing gate judges a moving picture on its motion, not on a still's line."""

import json
import re
from types import SimpleNamespace

import pytest

from immich_memories.analysis.editorial_block_votes import (
    STANDING_PROMPT_VERSION,
    standing_pass_version,
)
from immich_memories.analysis.editorial_story_standing import StandingGate
from immich_memories.config_models_llm import LLMConfig


class VoteJudge:
    # WHY: the text model is the standing gate's only external boundary; every rule under
    # test is mechanical and runs against a recorded reply.
    def __init__(self):
        self.config = SimpleNamespace(llm=LLMConfig(model="model-a"))
        self.calls = []

    def ask(self, _stage, prompt, **_kwargs):
        self.calls.append(prompt)
        return json.dumps({"weak": {}})


CLIP = {
    "asset_id": "clip",
    "kind": "video",
    "raw_seconds": 7.0,
    "members": ["clip"],
    "speech_regions": [[1.0, 2.0]],
}
STILL = {"asset_id": "still", "kind": "still", "members": ["still"]}


def gate(judge, *, bank=None, motion_identity="", motion_line=None, clip=None):
    return StandingGate(
        judge,
        contract="contract",
        period_label="a period",
        line_of=lambda asset: f"10:00 | a scene at {asset}",
        life=lambda _asset: True,
        unit_by_asset={"clip": ("E1", CLIP if clip is None else clip), "still": ("E1", STILL)},
        pictures_of={},
        bank=bank,
        save=None,
        calls={"standing_rounds": 0},
        motion_line=motion_line,
        motion_identity=motion_identity,
    )


def test_a_video_row_carries_its_motion_sentence_and_its_source_length():
    judge = VoteJudge()
    gate(judge, motion_line=lambda _unit: "A child runs across the grass and stops.").ensure(
        ["clip", "still"]
    )

    prompt = judge.calls[0]  # the block is asked in two orders
    assert "A child runs across the grass and stops." in prompt
    assert "video 7 s source." in prompt
    # a still's row is its own line and nothing else
    assert re.search(r"^P\d+: 10:00 \| a scene at still$", prompt, re.MULTILINE)


def test_without_a_banked_sentence_a_video_still_says_it_is_one():
    judge = VoteJudge()
    gate(judge).ensure(["clip"])

    assert "video 7 s source" in judge.calls[0]


def test_a_new_motion_producer_retires_the_banked_standing_rows():
    bank, first = {}, VoteJudge()
    gate(first, bank=bank, motion_identity="motion-line-v1@seat-a").ensure(["clip"])
    again = VoteJudge()
    gate(again, bank=bank, motion_identity="motion-line-v1@seat-a").ensure(["clip"])
    assert not again.calls  # the same producer replays

    renamed = VoteJudge()
    gate(renamed, bank=bank, motion_identity="motion-line-v1@seat-b").ensure(["clip"])
    assert renamed.calls


def test_the_pass_version_is_the_criterion_and_the_producer_that_wrote_its_rows():
    assert standing_pass_version("") == STANDING_PROMPT_VERSION
    assert standing_pass_version("seat-a").startswith(f"{STANDING_PROMPT_VERSION}/")
    assert standing_pass_version("seat-a") != standing_pass_version("seat-b")


@pytest.mark.parametrize("kind", ["video", "live-motion"])
@pytest.mark.parametrize("fallback", [False, True])
def test_measuring_speech_after_a_cut_reuses_its_standing_judgment(kind, fallback, tmp_path):
    from immich_memories.analysis.editorial_preparation_motion import BankedMotionLines

    motion = BankedMotionLines(store_path=tmp_path / "facts.sqlite", assets={}, described=False)
    motion_line = motion.observe if fallback else lambda _unit: "A child walks."
    clip = CLIP | {"kind": kind, "speech_regions": []}
    bank, first = {}, VoteJudge()
    before = gate(first, bank=bank, clip=clip, motion_line=motion_line)
    before.ensure(["clip"])
    assert len(first.calls) == 2

    measured = clip | {"speech_regions": [[1.0, 2.0]]}
    repeated = VoteJudge()
    after = gate(repeated, bank=bank, clip=measured, motion_line=motion_line)
    after.ensure(["clip"])

    assert not repeated.calls
    assert after.stands("clip", "major") == before.stands("clip", "major")
    assert measured["speech_regions"] == [[1.0, 2.0]]


@pytest.mark.parametrize(
    "clip,description",
    [(CLIP | {"raw_seconds": 9.0}, "A child walks."), (CLIP, "A child jumps.")],
)
def test_changed_source_duration_or_motion_evidence_still_needs_a_new_judgment(clip, description):
    bank, first = {}, VoteJudge()
    gate(first, bank=bank, motion_line=lambda _unit: "A child walks.").ensure(["clip"])

    changed = VoteJudge()
    gate(changed, bank=bank, clip=clip, motion_line=lambda _unit: description).ensure(["clip"])

    assert len(changed.calls) == 2


@pytest.mark.parametrize("kind", ["video", "live-motion"])
def test_motion_rejected_in_both_orders_cannot_bypass_standing_in_a_major_story(kind):
    from immich_memories.analysis.editorial_structure_lines import UnitLines

    unit = CLIP | {"kind": kind}
    lines = UnitLines({"clip": "2022-01-01 | An empty room with tiled floors."})
    admission = StandingGate(
        VoteJudge(),
        contract="Show the family visit",
        period_label="a year",
        line_of=lambda _asset: lines.line(unit),
        life=lambda _asset: lines.shows_life(unit),
        unit_by_asset={"clip": ("E1", unit)},
        pictures_of={"K01": 7},
        bank=None,
        save=None,
        calls={"standing_rounds": 0},
        score_of=lambda _asset: 0,
    )
    admission.ensure(["clip"])

    assert not admission.stands("clip", "major", "K01")


@pytest.mark.parametrize("kind", ["video", "live-motion"])
@pytest.mark.parametrize("pictures", [1, 7])
def test_approved_motion_can_stand_when_its_still_has_no_people(kind, pictures):
    from immich_memories.analysis.editorial_structure_lines import UnitLines

    unit = CLIP | {"kind": kind}
    lines = UnitLines({"clip": "2022-01-01 | An empty diving board above a pool."})
    judge = VoteJudge()
    admission = StandingGate(
        judge,
        contract="Show the visit",
        period_label="a year",
        line_of=lambda _asset: lines.line(unit),
        life=lambda _asset: lines.shows_life(unit),
        unit_by_asset={"clip": ("E1", unit)},
        pictures_of={"K01": pictures},
        bank=None,
        save=None,
        calls={"standing_rounds": 0},
        motion_line=lambda _unit: "A swimmer dives from the board into the pool.",
    )
    admission.ensure(["clip"])

    assert admission.stands("clip", "major", "K01")
    assert len(judge.calls) == 2
    assert all("A swimmer dives" in prompt for prompt in judge.calls)
