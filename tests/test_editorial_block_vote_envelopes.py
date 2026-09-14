"""What a block vote reads when the model drops the wrapper key the contract asked for.

The replies here are the shapes recorded against the owner's February 2024 library: the
reference reader wrapped its picks in "worthy", a hosted reader returned the same picks as a
bare fenced mapping. Names in the fixtures are placeholders; the JSON shape is verbatim.
"""

import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from immich_memories.analysis.editorial_block_votes import judge_standing, judge_worthiness
from immich_memories.config_models_llm import LLMConfig
from tests.test_editorial_duration_planner_integration import run
from tests.test_editorial_story_first_planner import StoryJudge, make_source

FIXTURES = Path(__file__).parent / "fixtures"
WRAPPED = (FIXTURES / "worthy_reply_wrapped.txt").read_text()
FLAT_FENCED = (FIXTURES / "worthy_reply_flat_fenced.txt").read_text()


class RecordedJudge:
    """Replays one recorded reply for every order of a block."""

    def __init__(self, reply: str):
        self.config = SimpleNamespace(llm=LLMConfig(model="model-a"))
        self.reply = reply

    def ask(self, _stage, _prompt, **_kwargs):
        return self.reply


def worthiness(reply: str, offered: tuple[str, ...]):
    return judge_worthiness(
        RecordedJudge(reply),
        happenings=list(offered),
        label_of={label: label for label in offered},
        text_of=lambda label: f"what {label} shows",
        near_home=lambda _: None,
        contract="contract",
        contract_key="contract hash",
        criterion="Pick the happenings",
        marker="",
        period_label="February 2024",
    )


OFFERED = ("F01", "F04", "F07", "F08", "F09", "F10", "F11", "F12")


def remarkable(tier: dict[str, int]) -> set[str]:
    return {label for label, value in tier.items() if value == 0}


def test_a_flat_reply_names_the_same_happenings_as_a_wrapped_one():
    flat, _reasons, _rounds = worthiness(FLAT_FENCED, OFFERED)
    wrapped, _, _ = worthiness(WRAPPED, OFFERED)
    assert remarkable(flat) == {"F01", "F04", "F08", "F10"}
    assert remarkable(wrapped) == {"F04", "F07", "F08", "F09", "F11", "F12"}


@pytest.mark.parametrize(
    "reply",
    [
        '{"F01": "belongs", "note": "the rest were ordinary"}',
        '{"picks": {"F01": "belongs"}}',
        '{"F99": "a label nobody offered"}',
        "no JSON at all",
    ],
)
def test_a_bare_object_that_is_not_the_answer_is_not_read_as_one(reply):
    tier, _reasons, rounds = worthiness(reply, OFFERED)
    assert remarkable(tier) == set()
    assert {entry["envelope"] for entry in rounds} == {"unreadable"}


def test_a_round_that_reads_nothing_is_warned_about_and_recorded(caplog):
    with caplog.at_level(logging.WARNING):
        _tier, _reasons, rounds = worthiness("the month was unremarkable", OFFERED)
    assert [entry["round"] for entry in rounds] == ["worthy-1-source", "worthy-1-hashed"]
    assert all(entry["offered"] == len(OFFERED) and entry["picked"] == 0 for entry in rounds)
    warnings = [record.getMessage() for record in caplog.records]
    assert len(warnings) == 2
    assert "worthy-1-source read 0 of 8 offered happenings (envelope=unreadable)" in warnings[0]


def test_a_round_the_reader_could_open_is_not_warned_about(caplog):
    with caplog.at_level(logging.WARNING):
        _tier, _reasons, rounds = worthiness(FLAT_FENCED, OFFERED)
    assert [entry["envelope"] for entry in rounds] == ["flat", "flat"]
    assert [entry["picked"] for entry in rounds] == [4, 4]
    assert caplog.records == []


def test_a_model_that_says_none_qualify_is_still_announced_as_a_zero(caplog):
    with caplog.at_level(logging.WARNING):
        _tier, _reasons, rounds = worthiness('{"worthy": {}}', OFFERED)
    assert [entry["envelope"] for entry in rounds] == ["wrapped", "wrapped"]
    assert len(caplog.records) == 2, "a real 'none of these' and an unread reply both need saying"


class MixedEnvelopeJudge(RecordedJudge):
    """February block 3 verbatim: the source order answered flat, the hashed order wrapped."""

    def __init__(self, picks: dict[str, str], wrapped_order: str):
        super().__init__("")
        self.picks = picks
        self.wrapped_order = wrapped_order

    def ask(self, stage, _prompt, **_kwargs):
        return json.dumps(
            {"worthy": self.picks} if stage.endswith(self.wrapped_order) else self.picks
        )


@pytest.mark.parametrize("wrapped_order", ["source", "hashed"])
def test_both_orders_of_the_parity_pair_normalise_the_same_way(wrapped_order):
    picks = dict.fromkeys(("F01", "F04", "F08"), "worth keeping")
    judge = MixedEnvelopeJudge(picks, wrapped_order)
    tier, _reasons, rounds = judge_worthiness(
        judge,
        happenings=list(OFFERED),
        label_of={label: label for label in OFFERED},
        text_of=lambda label: f"what {label} shows",
        near_home=lambda _: None,
        contract="contract",
        contract_key="contract hash",
        criterion="Pick the happenings",
        marker="",
        period_label="February 2024",
    )
    assert remarkable(tier) == set(picks), "a mixed pair must still agree on a firm yes"
    assert sorted(entry["envelope"] for entry in rounds) == ["flat", "wrapped"]


class FlatAnsweringJudge(StoryJudge):
    """A planner judge whose worthy rounds answer in the shape glm-5.3-flash used."""

    def answer(self, stage, prompt):
        if stage.startswith("worthy-"):
            labels = re.findall(r"^(F\d+)(?: \(near home\))?:", prompt, re.MULTILINE)
            body = json.dumps(dict.fromkeys(labels, "An outing worth keeping"), indent=2)
            return f"```json\n{body}\n```"
        return super().answer(stage, prompt)


def test_the_gate_artifact_records_every_round_and_the_envelope_it_read(tmp_path):
    source = make_source(tmp_path)
    run(source, FlatAnsweringJudge())
    gate = json.loads(
        (source.artifact_dir / "derived-decisions/memory-worthy-gate.private.json").read_text()
    )
    assert gate["counts"]["remarkable"] == len(gate["tiers"]) > 0
    assert [entry["envelope"] for entry in gate["rounds"]] == ["flat", "flat"]
    assert all(entry["picked"] == entry["offered"] for entry in gate["rounds"])


def test_the_standing_sibling_reads_a_flat_rejection_list_too():
    """`weak` runs the same block vote over `P` labels, and its silent zero fails open: every
    picture would be left standing. The offer list makes the normalisation just as deterministic."""
    pictures = ("first", "second", "third")
    judge = RecordedJudge('```json\n{"P02": "a screen, nothing to show"}\n```')
    votes = judge_standing(
        judge,
        pictures=list(pictures),
        line_of=lambda asset: f"a picture of {asset}",
        contract="contract",
        period_label="February 2024",
    )
    assert votes["second"][0] == 0, "named by both orders is a firm rejection"
    assert {votes[a][0] for a in ("first", "third")} == {2}
