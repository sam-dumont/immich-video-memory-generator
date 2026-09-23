"""The audience question asked of twelve carriers at once, and every hold it must keep."""

from __future__ import annotations

import json
import re

import pytest

from immich_memories.analysis.editorial_structure_audience import AudienceBank, AudienceGate
from tests.editorial_thin_fixtures import PRIVATE, CountingJudge, PictureEvidence


def carriers(count: int, private=()):
    lines, units = {}, []
    for index in range(count):
        asset = f"a{index:02d}"
        caption = (
            f"{PRIVATE}, shot {index}" if index in private else f"people at a table, shot {index}"
        )
        lines[asset] = f"2024-02-{index % 28 + 1:02d}T09:00 | {caption}"
        units.append({"asset_id": asset, "kind": "still"})
    return lines, units


def gate(tmp_path, judge, lines, library=None):
    return AudienceGate(
        judge,
        audience="family",
        picture_evidence=PictureEvidence(lines),
        flag_rows={},
        lines=lines,
        bank_path=tmp_path / "shareability.private.json",
        library=library
        or AudienceBank(tmp_path / "audience-verdicts.private.json", answerer="full|model-a"),
    )


def test_twenty_five_carriers_are_asked_in_three_batches_of_two_orders_and_held(tmp_path):
    lines, units = carriers(25, private={3, 17, 24})
    judge = CountingJudge()
    audience = gate(tmp_path, judge, lines)

    audience.prefetch(units, batch=12)
    verdicts = {unit["asset_id"]: audience.verdict_of(unit) for unit in units}

    assert len(judge.calls) == 6
    assert {asset for asset, verdict in verdicts.items() if verdict != "share"} == {
        "a03",
        "a17",
        "a24",
    }


class HoldsInOneOrderOnly(CountingJudge):
    """A reader whose batched answers depend on the order of the rows: only the order named
    `holding` sees the private picture for what it is."""

    def __init__(self, holding: str) -> None:
        super().__init__()
        self.holding = holding

    def ask(self, stage, prompt, max_tokens=260, **options):
        raw = super().ask(stage, prompt, max_tokens, **options)
        if "batch" not in stage or self.holding in stage:
            return raw
        return json.dumps({label: {"finding": "none", "why": "-"} for label in json.loads(raw)})


@pytest.mark.parametrize("holding", ["-source-", "-shuffled-"])
def test_a_carrier_either_order_holds_is_held(tmp_path, holding):
    lines, units = carriers(8, private={5})
    judge = HoldsInOneOrderOnly(holding)
    audience = gate(tmp_path, judge, lines)

    audience.prefetch(units, batch=12)
    verdicts = {unit["asset_id"]: audience.verdict_of(unit) for unit in units}

    assert verdicts["a05"] == "do_not_show"
    assert all("batch" in stage for stage in judge.calls)


class SkipsOneCarrier(CountingJudge):
    """A reader that never answers the group showing `shot 2`, in either order."""

    def ask(self, stage, prompt, max_tokens=260, **options):
        raw = super().ask(stage, prompt, max_tokens, **options)
        skipped = re.findall(r"^(G\d+): .*shot 2\"", prompt, re.MULTILINE)
        if not skipped:
            return raw
        answer = json.loads(raw)
        answer.pop(skipped[0])
        return json.dumps(answer)


def test_a_carrier_the_batch_did_not_answer_is_asked_alone(tmp_path):
    lines, units = carriers(5, private={4})
    judge = SkipsOneCarrier()
    audience = gate(tmp_path, judge, lines)

    audience.prefetch(units, batch=12)
    verdicts = {unit["asset_id"]: audience.verdict_of(unit) for unit in units}

    assert verdicts["a04"] == "do_not_show"
    assert [stage for stage in judge.calls if "batch" not in stage] == ["shareability-03-activity"]


def test_a_carrier_the_library_already_answered_is_not_asked_again(tmp_path):
    lines, units = carriers(3)
    first = gate(tmp_path, CountingJudge(), lines)
    first.prefetch(units, batch=12)
    for unit in units:
        first.verdict_of(unit)
    second = CountingJudge()

    gate(tmp_path, second, lines).prefetch(units, batch=12)

    assert second.calls == []


def test_a_carrier_a_detector_already_refuses_for_this_audience_is_not_asked(tmp_path):
    lines, units = carriers(3)
    lines["a01"] += " | nsfw=yes"
    judge = CountingJudge()
    audience = gate(tmp_path, judge, lines)
    audience.audience = "sendable"

    audience.prefetch(units, batch=12)
    verdicts = {unit["asset_id"]: audience.verdict_of(unit) for unit in units}

    assert verdicts["a01"] == "family_only"
    asked = " ".join(prompt for _stage, prompt in judge.prompts)
    assert "shot 1" not in asked
    assert "shot 0" in asked


def test_without_a_batch_every_carrier_is_asked_alone(tmp_path):
    lines, units = carriers(4)
    judge = CountingJudge()
    audience = gate(tmp_path, judge, lines)

    audience.prefetch(units, batch=0)
    for unit in units:
        audience.verdict_of(unit)

    assert len(judge.calls) == 4
