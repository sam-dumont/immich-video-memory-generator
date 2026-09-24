"""Standing asks every row for its own yes or no, and checks only the rows it doubted (#1205)."""

import json
import re
from types import SimpleNamespace

from immich_memories.analysis.editorial_carrier_eligibility import people_moment
from immich_memories.analysis.editorial_standing_vote import judge_standing, standing_prompt
from immich_memories.analysis.editorial_story_standing import StandingGate
from immich_memories.config_models_llm import LLMConfig


class RowJudge:
    """Answers every offered row: yes for the lines in `weak`, no for the rest."""

    def __init__(self, weak=("a mug",), second_weak=None):
        self.config = SimpleNamespace(llm=LLMConfig(model="model-a"))
        self.weak, self.second_weak = weak, second_weak
        self.calls = []

    def ask(self, stage, prompt, **_kwargs):
        self.calls.append((stage, prompt))
        weak = (
            self.second_weak
            if stage.startswith("standing-check") and self.second_weak is not None
            else self.weak
        )
        rows = re.findall(r"^(P\d+): (.*)$", prompt.split("PICTURES\n", 1)[1], re.MULTILINE)
        return json.dumps(
            {
                "weak": {
                    label: ("yes: nothing in it" if any(w in line for w in weak) else "no")
                    for label, line in rows
                }
            }
        )


LINES = {
    "m": "a mug on a table",
    "b": "a baby asleep on a blanket",
    "f": "a family of three posing",
}


def test_a_row_the_first_order_says_stands_is_not_asked_again():
    judge = RowJudge()
    scores = judge_standing(judge, pictures=list(LINES), line_of=LINES.get)
    assert {a: s for a, (s, _why) in scores.items()} == {"m": 0, "b": 2, "f": 2}
    assert [stage.split("-1")[0] for stage, _p in judge.calls] == ["standing", "standing-check"]
    check = judge.calls[1][1].split("PICTURES\n", 1)[1]
    assert "a mug" in check and "baby" not in check and "family" not in check


def test_a_doubt_the_second_order_does_not_share_is_not_a_refusal():
    judge = RowJudge(second_weak=())
    scores = judge_standing(judge, pictures=list(LINES), line_of=LINES.get)
    assert scores["m"][0] == 1


def test_decided_rows_are_banked_and_not_asked_again():
    judge, bank = RowJudge(), {}
    judge_standing(judge, pictures=list(LINES), line_of=LINES.get, bank=bank)
    again = RowJudge()
    scores = judge_standing(again, pictures=list(LINES), line_of=LINES.get, bank=bank)
    assert not again.calls
    assert scores["m"][0] == 0 and scores["b"][0] == 2


def test_the_question_asks_every_row_and_keeps_quiet_and_posed_people():
    prompt = standing_prompt("P01: a child plays on a blanket\nP02: a mug")
    assert "every label exactly once" in prompt
    assert "quiet or posed" in prompt
    assert "empty room or floor" in prompt
    assert '{"weak":{"P01":"no","P02":"yes: why"}}' in prompt


def test_a_people_moment_stands_without_a_question():
    judge = RowJudge(weak=("baby", "mug"))
    gate = StandingGate(
        judge,
        line_of=LINES.get,
        life=lambda _a: True,
        unit_by_asset={},
        pictures_of={},
        bank=None,
        save=None,
        calls={"standing_rounds": 0},
        people_moment=lambda asset: asset == "b",
    )
    gate.ensure(list(LINES))
    assert gate.scores["b"] == 2
    assert all("baby" not in prompt for _stage, prompt in judge.calls)


def test_the_people_moment_guard_reads_the_frame_head_and_refuses_a_blurry_frame():
    heads = {"p": SimpleNamespace(heads=(("frame_kind", "people_moment"),))}
    heads["o"] = SimpleNamespace(heads=(("frame_kind", "lone_everyday_object"),))
    heads["s"] = heads["p"]
    lines = {"p": "a family posing", "o": "a mug", "s": "a family posing | SOFT (blurry)"}
    assert people_moment(heads, lines, "p")
    assert not people_moment(heads, lines, "o")
    assert not people_moment(heads, lines, "s")
    assert not people_moment(heads, lines, "missing")
