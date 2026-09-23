"""A picture's standing answer is the library's, not one film's: every cut that reaches it reads it."""

import json
from types import SimpleNamespace

from immich_memories.analysis.editorial_rule_banked_facts import (
    configured_text_identity,
    open_banked_facts,
)
from immich_memories.analysis.editorial_story_standing import StandingBankFile, StandingGate
from immich_memories.config_models_llm import LLMConfig

LINES = {"a": "10:00 | a lone mug on a table", "b": "11:00 | two children build a sandcastle"}


class VoteJudge:
    # WHY: the text model is the standing gate's only external boundary; the reply is recorded.
    def __init__(self):
        self.config = SimpleNamespace(llm=LLMConfig(model="model-a"))
        self.calls = []

    def ask(self, _stage, prompt, **_kwargs):
        self.calls.append(prompt)
        weak = [line.split(":")[0] for line in prompt.splitlines() if "lone mug" in line]
        return json.dumps({"weak": dict.fromkeys(weak, "an object")})


def _cut(judge, case_bank_dir, *, subject=""):
    bank = StandingBankFile.open(case_bank_dir)
    gate = StandingGate(
        judge,
        subject=subject,
        line_of=LINES.get,
        life=lambda asset: asset == "b",
        unit_by_asset={a: ("E1", {"asset_id": a, "kind": "still"}) for a in LINES},
        pictures_of={},
        bank=bank.entries,
        save=bank.save,
        calls={"standing_rounds": 0},
    )
    gate.ensure(list(LINES))
    return gate.scores


def test_a_year_cut_reuses_the_standing_answers_a_month_cut_of_the_same_pictures_banked(tmp_path):
    banks = tmp_path / "structure-banks"
    month_judge, year_judge = VoteJudge(), VoteJudge()

    month = _cut(month_judge, banks / "month-2020-05")
    year = _cut(year_judge, banks / "year-2020")

    assert month_judge.calls, "the month paid for its answers"
    assert year_judge.calls == []
    assert year == month == {"a": 0, "b": 2}


def test_the_year_s_no_model_draft_reads_what_the_month_s_model_answered(tmp_path):
    banks = tmp_path / "structure-banks"
    _cut(VoteJudge(), banks / "month-2020-05")

    banked = open_banked_facts(
        bank_dir=banks / "year-2020",
        attempts_dir=None,
        store_path=None,
        audience="family",
        model_identity=configured_text_identity(LLMConfig(model="model-a")),
        subject="",
        motion_identity="",
        rows_of=LINES,
        episode_cards={},
    )

    assert (banked.standing_of("a"), banked.standing_of("b")) == (0, 2)


def test_a_memory_about_a_subject_asks_its_own_question(tmp_path):
    banks = tmp_path / "structure-banks"
    _cut(VoteJudge(), banks / "month-2020-05")
    custom = VoteJudge()

    _cut(custom, banks / "custom-kitchen", subject="the kitchen renovation")

    assert custom.calls
    assert all("the kitchen renovation" in prompt for prompt in custom.calls)


def test_two_cuts_saving_at_once_keep_each_other_s_answers(tmp_path):
    banks = tmp_path / "structure-banks"
    first = StandingBankFile.open(banks / "month-2020-05")
    second = StandingBankFile.open(banks / "month-2020-06")
    first.entries.setdefault("rows", {})["one"] = {"votes": 0, "why": ""}
    second.entries.setdefault("rows", {})["two"] = {"votes": 2, "why": "a screen"}

    first.save()
    second.save()

    assert set(StandingBankFile.open(banks / "year-2020").entries["rows"]) == {"one", "two"}
