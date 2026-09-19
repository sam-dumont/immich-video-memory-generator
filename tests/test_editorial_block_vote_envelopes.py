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
from immich_memories.analysis.editorial_page_recovery import PageReadFailure
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
        self.failures = []

    def ask(self, _stage, _prompt, **_kwargs):
        return self.reply

    def record_failure(self, stage, record):
        self.failures.append((stage, record))


def test_malformed_vote_cannot_become_a_banked_rejection():
    # WHY: replay the real reader's malformed identifier without calling the model.
    judge = RecordedJudge('{"weak": {"P01 (near home)": "a screen"}}')
    bank = {}
    with pytest.raises(PageReadFailure):
        judge_standing(
            judge,
            pictures=["photo"],
            line_of=lambda _: "a screen",
            contract="contract",
            period_label="one month",
            bank=bank,
        )
    assert not bank.get("rows")
    assert judge.failures[0][1]["attempt_count"] == 3


def test_invalid_labels_are_reasked_and_only_valid_votes_are_reused():
    class RecoveringJudge(RecordedJudge):
        def __init__(self):
            super().__init__('{"weak": {"P01": "a screen"}}')
            self.calls = 0

        def ask(self, stage, prompt, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return '{"weak": {"P01 (near home)": "a screen"}}'
            return super().ask(stage, prompt, **kwargs)

    # WHY: one malformed model response followed by a valid answer exercises bank reuse.
    judge, bank = RecoveringJudge(), {}
    kwargs = {
        "pictures": ["photo"],
        "line_of": lambda _: "a screen",
        "contract": "contract",
        "period_label": "one month",
        "bank": bank,
    }
    assert judge_standing(judge, **kwargs)["photo"][0] == 0
    assert judge.calls == 3
    assert judge_standing(judge, **kwargs)["photo"][0] == 0
    assert judge.calls == 3


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
        '{"worthy": [42]}',
        "no JSON at all",
    ],
)
def test_a_bare_object_that_is_not_the_answer_is_not_read_as_one(reply):
    with pytest.raises(PageReadFailure):
        worthiness(reply, OFFERED)


def test_an_unreadable_round_reports_its_bounded_attempts():
    with pytest.raises(PageReadFailure) as raised:
        worthiness("the month was unremarkable", OFFERED)
    assert raised.value.stage == "worthy-1-source"
    assert len(raised.value.attempts) == 3


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
    assert len(caplog.records) == 2, "a valid empty vote remains visible in the run log"


def test_near_home_context_is_not_written_as_part_of_the_vote_label():
    class LabelReadingJudge(RecordedJudge):
        def ask(self, _stage, prompt, **_kwargs):
            labels = re.findall(r"^(F[^:]+):", prompt, re.MULTILINE)
            return json.dumps({"worthy": dict.fromkeys(labels, "a meaningful occasion")})

    # WHY: the real model copied the full text before the colon as its identifier.
    tier, _, _ = judge_worthiness(
        LabelReadingJudge(""),
        happenings=["occasion"],
        label_of={"occasion": "F01"},
        text_of=lambda _: "a family celebration",
        near_home=lambda _: True,
        contract="contract",
        contract_key="contract hash",
        criterion="Pick occasions",
        marker="",
        period_label="one month",
    )
    assert tier == {"occasion": 0}


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


@pytest.mark.parametrize(
    "reply",
    ['```json\n{"P02": "a screen, nothing to show"}\n```', '{"weak": ["P02"]}'],
)
def test_the_standing_sibling_reads_a_flat_rejection_list_too(reply):
    """`weak` runs the same block vote over `P` labels, and its silent zero fails open: every
    picture would be left standing. The offer list makes the normalisation just as deterministic."""
    pictures = ("first", "second", "third")
    judge = RecordedJudge(reply)
    votes = judge_standing(
        judge,
        pictures=list(pictures),
        line_of=lambda asset: f"a picture of {asset}",
        contract="contract",
        period_label="February 2024",
    )
    assert votes["second"][0] == 0, "named by both orders is a firm rejection"
    assert {votes[a][0] for a in ("first", "third")} == {2}
