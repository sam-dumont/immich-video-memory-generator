"""One closed question over a finished cut, and what its answer is allowed to move."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

from immich_memories.analysis.editorial_thin_vote import (
    balanced_groups,
    classify_fit,
    vote_thesis_fit,
)
from immich_memories.config_models_llm import LLMConfig


class FitJudge:
    """Names every row whose line says `filler`, in both orders."""

    def __init__(self) -> None:
        self.config = SimpleNamespace(llm=LLMConfig(model="model-a"))
        self.calls: list[tuple[str, str]] = []

    def ask(self, stage, prompt, **_kwargs):
        self.calls.append((stage, prompt))
        weak = re.findall(r"^(P\d+): .*filler", prompt, re.MULTILINE)
        return json.dumps({"weak": dict.fromkeys(weak, "adds nothing")})


def shot(asset, *, favourite=False, record=""):
    return {"asset_id": asset, "favourite": favourite, "notable_record": record}


def test_a_vote_block_never_holds_a_single_lonely_row():
    """A reject-only vote whose last block holds one row has no company to judge it against."""
    assert balanced_groups(list(range(12))) == [list(range(12))]
    assert [len(block) for block in balanced_groups(list(range(13)))] == [7, 6]
    assert [len(block) for block in balanced_groups(list(range(14)))] == [7, 7]
    assert [len(block) for block in balanced_groups(list(range(16)))] == [8, 8]
    # more than one block always means equal blocks, never twelve and a remainder
    assert [len(block) for block in balanced_groups(list(range(17)))] == [9, 8]
    assert [len(block) for block in balanced_groups(list(range(25)))] == [9, 9, 7]
    assert min(len(block) for block in balanced_groups(list(range(37)))) >= 4


def test_the_vote_names_a_shot_only_when_both_orders_do():
    judge = FitJudge()
    votes, rounds = vote_thesis_fit(
        judge,
        pictures=["a", "b"],
        line_of=lambda asset: "filler" if asset == "a" else "the whole afternoon",
        thesis="the month a family moved",
        contract="contract",
    )
    assert votes["a"][0] == 2
    assert votes["b"][0] == 0
    assert rounds and all(record["envelope"] not in {"failed", "unreadable"} for record in rounds)


def test_the_thesis_is_inside_the_question_and_therefore_inside_every_row_key():
    asked = {}
    for thesis in ("one account", "a different account"):
        judge = FitJudge()
        bank: dict = {}
        for _ in range(2):
            vote_thesis_fit(
                judge,
                pictures=["a"],
                line_of=lambda _asset: "the whole afternoon",
                thesis=thesis,
                contract="contract",
                bank=bank,
            )
        asked[thesis] = len(judge.calls)
    # the second run of the same film replays; a different account is a different question
    assert asked == {"one account": 2, "a different account": 2}


def test_a_favourite_is_never_moved_by_the_vote_whatever_the_orders_said():
    """A star, or a picture carrying a banked record, leaves the cut only on a gate refusal."""
    cut = [
        shot("plain"),
        shot("star", favourite=True),
        shot("record", record="the first of them"),
        shot("star-once", favourite=True),
        shot("fine"),
        shot("doubted"),
    ]
    votes = {
        "plain": (2, "an appliance"),
        "star": (2, "filler"),
        "record": (2, "a plain detail"),
        "star-once": (1, "plain"),
        "fine": (0, ""),
        "doubted": (1, "maybe"),
    }
    verdicts = classify_fit(cut, votes)
    assert verdicts["plain"]["state"] == "bad"
    assert verdicts["doubted"]["state"] == "weak"
    assert verdicts["fine"]["state"] == "kept"
    for asset in ("star", "record", "star-once"):
        assert verdicts[asset]["state"] == "kept", asset
        assert verdicts[asset]["protected"] and verdicts[asset]["held_by"]
    # the disagreement is still on the record, so a run can be asked how often this fired
    assert verdicts["star"]["named_by"] == 2
    assert verdicts["star-once"]["named_by"] == 1
    assert verdicts["fine"]["named_by"] == 0 and not verdicts["fine"]["held_by"]
