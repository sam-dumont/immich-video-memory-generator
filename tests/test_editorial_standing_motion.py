"""The standing gate judges a moving picture on its motion, not on a still's line."""

import json
import re
from types import SimpleNamespace

from immich_memories.analysis.editorial_block_votes import (
    STANDING_PROMPT_VERSION,
    standing_pass_version,
)
from immich_memories.analysis.editorial_story_carriers import StandingGate
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


def gate(judge, *, bank=None, motion_identity="", motion_line=None):
    return StandingGate(
        judge,
        contract="contract",
        period_label="a period",
        line_of=lambda asset: f"10:00 | a scene at {asset}",
        life=lambda _asset: True,
        unit_by_asset={"clip": ("E1", CLIP), "still": ("E1", STILL)},
        pictures_of={},
        bank=bank,
        save=None,
        calls={"standing_rounds": 0},
        motion_line=motion_line,
        motion_identity=motion_identity,
    )


def test_a_video_row_carries_its_motion_sentence_its_length_and_its_speech():
    judge = VoteJudge()
    gate(judge, motion_line=lambda _unit: "A child runs across the grass and stops.").ensure(
        ["clip", "still"]
    )

    prompt = judge.calls[0]  # the block is asked in two orders
    assert "A child runs across the grass and stops." in prompt
    assert "video 7 s source, speech." in prompt
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
