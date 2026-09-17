"""Every reader puts its shared preamble first, byte for byte.

A server that reuses a prompt prefix needs no API asking it to: llama.cpp and vLLM match
on prefix, oMLX keeps a prompt cache in RAM, OpenAI caches above a length threshold. All
of them need the same thing, which is for the bytes two calls in a stage have in common
to be at the front and for everything that varies to come strictly after. That is a
property of the prompt, not of the transport, so it is pinned here per stage.

Measured on February 2024 before this layout, the reader prompts shared 3% to 47% of
their bytes as a prefix. The floors below are bytes, taken from the rendered prompts of
each stage; they are lower bounds, not targets.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from immich_memories.config_models_llm import LLMConfig

CONTRACT = "A month of one family's pictures. " * 6


def shared_prefix(first: str, second: str) -> int:
    """How many leading bytes two prompts of one stage hand a server for free."""
    return len(os.path.commonprefix([first, second]).encode())


class RecordingJudge:
    """A judge that answers nothing and keeps every prompt it was handed."""

    def __init__(self) -> None:
        self.config = SimpleNamespace(llm=LLMConfig(model="model-a"))
        self.calls: list[str] = []

    def ask(self, _stage: str, prompt: str, **_kwargs: object) -> str:
        self.calls.append(prompt)
        if prompt.startswith("Inventory distinct depicted moments"):
            sources = re.findall(r'"source": "(U\d+)"', prompt)
            return json.dumps(
                {
                    "moments": [
                        {"same_as": None, "sources": [s], "primary": s, "content": f"a view {s}"}
                        for s in sources
                    ]
                }
            )
        return "{}"

    def record_failure(self, _stage: str, _record: object) -> None:
        return None


def _worth_prompts() -> tuple[str, str]:
    from immich_memories.analysis.editorial_block_votes import judge_worthiness

    judge = RecordingJudge()
    happenings = [f"f{i}" for i in range(24)]
    judge_worthiness(
        judge,
        happenings=happenings,
        label_of={f: f"F{i + 1:02d}" for i, f in enumerate(happenings)},
        text_of=lambda f: f"a day out at {f}",
        near_home=lambda _f: False,
        contract=CONTRACT,
        contract_key="c1",
        criterion="Pick the happenings worth making a memory of.",
        marker="",
        period_label="February 2024",
    )
    return judge.calls[0], judge.calls[2]


def _standing_prompts() -> tuple[str, str]:
    from immich_memories.analysis.editorial_block_votes import judge_standing

    judge = RecordingJudge()
    judge_standing(
        judge,
        pictures=[f"a{i}" for i in range(24)],
        line_of=lambda a: f"2024-02-01 a picture of {a}",
        contract=CONTRACT,
        period_label="February 2024",
    )
    return judge.calls[0], judge.calls[2]


def _audience_evidence(members: int, word: str) -> dict:
    return {
        "members": [
            {"member": f"p{i}", "caption": f"{word} number {i}"} for i in range(1, members + 1)
        ]
    }


def _activity_prompts() -> tuple[str, str]:
    from immich_memories.analysis.editorial_shareability_audience import audience_check_prompt

    return (
        audience_check_prompt(_audience_evidence(2, "a kitchen")),
        audience_check_prompt(_audience_evidence(5, "a beach walk")),
    )


def _exposure_prompts() -> tuple[str, str]:
    from immich_memories.analysis.editorial_shareability_audience import audience_exposure_prompt

    return (
        audience_exposure_prompt(_audience_evidence(2, "a kitchen"), ["p1", "p2"]),
        audience_exposure_prompt(_audience_evidence(3, "a beach walk"), ["p1", "p3"]),
    )


def _inventory_prompts() -> tuple[str, str]:
    from immich_memories.analysis.editorial_moment_inventory import inventory_event

    judge = RecordingJudge()
    units = [
        {"asset_id": f"a{i:03d}", "taken": f"2024-02-0{i % 9 + 1}T10:00:00Z", "moment": f"m{i}"}
        for i in range(90)
    ]
    inventory_event(
        judge,
        event="S0001",
        units=units,
        context="A walk by the river, then lunch.",
        line=lambda u: f"a picture taken at {u['taken']}",
    )
    assert len(judge.calls) >= 2, "the fixture must be big enough to page"
    return judge.calls[0], judge.calls[1]


def _episode_page_prompts() -> tuple[str, str]:
    from immich_memories.analysis.editorial_story_reading import _episode_page_prompt

    first = [{"reading": "r1", "what_happened": "a walk"}]
    second = [
        {"reading": "r1", "what_happened": "a lunch"},
        {"reading": "r2", "what_happened": "a swim"},
    ]
    return (
        _episode_page_prompt(first, "2024-02"),
        _episode_page_prompt(second, "2024-03"),
    )


def _grouping_prompts() -> tuple[str, str]:
    from immich_memories.analysis.editorial_story_grouping import _grouping_prompt

    return (
        _grouping_prompt([{"id": "S0001"}], contract=CONTRACT, prior={"thesis": "a quiet month"}),
        _grouping_prompt(
            [{"id": "S0002"}, {"id": "S0003"}], contract=CONTRACT, prior={"thesis": "a busy month"}
        ),
    )


def _weighing_prompts() -> tuple[str, str]:
    from immich_memories.analysis.editorial_story_weighing import _weighing_prompt

    return (
        _weighing_prompt(
            ["K01 | a walk"], thesis="a quiet month", contract=CONTRACT, candidates=["K01"]
        ),
        _weighing_prompt(
            ["K02 | a party", "K03 | a trip"],
            thesis="a busy month",
            contract=CONTRACT,
            candidates=[],
        ),
    )


def _pick_prompts() -> tuple[str, str]:
    from immich_memories.analysis.editorial_story_shortlist import _pick_prompt

    return (
        _pick_prompt(
            CONTRACT,
            {"title": "A walk", "purpose": "it opens the month"},
            "M01 a river\nM02 a bridge",
            count=2,
            allow_fewer=False,
            sampled_motion=False,
        ),
        _pick_prompt(
            CONTRACT,
            {"title": "A party", "purpose": "the one occasion"},
            "M01 a cake\nM02 a table\nM03 a dance",
            count=3,
            allow_fewer=False,
            sampled_motion=False,
        ),
    )


def _episode_read_prompts() -> tuple[str, str]:
    from immich_memories.analysis.text_episode_answers import _EpisodeRequestScope
    from immich_memories.analysis.text_episode_reader import _prompt_for
    from immich_memories.store.episode_readings import EpisodeReadingIdentity

    def scope(episode_id: str, assets: tuple[str, ...]) -> _EpisodeRequestScope:
        return _EpisodeRequestScope(
            identity=EpisodeReadingIdentity(
                group_id=episode_id, producer_key="p", evidence_key="e"
            ),
            full_asset_ids=assets,
            page_asset_ids=assets,
            page_number=1,
            page_count=1,
        )

    lines = {f"a{i}": f"a picture of thing {i}" for i in range(6)}
    return (
        _prompt_for((scope("e1", ("a0", "a1")),), lines),
        _prompt_for((scope("e2", ("a2", "a3", "a4")),), lines),
    )


def _period_prompts() -> tuple[str, str]:
    from immich_memories.analysis.text_period_wire import _PeriodEpisodeFacts, _prompt_for

    def facts(day: int, place: str) -> _PeriodEpisodeFacts:
        moment = datetime(2024, 2, day, 10, 0, tzinfo=UTC)
        return _PeriodEpisodeFacts(
            first_taken_at=moment,
            last_taken_at=moment,
            place=place,
            people=("a parent",),
            asset_count=day,
            what_happened=f"something at {place}",
        )

    return (
        _prompt_for((facts(1, "a river"),)),
        _prompt_for((facts(2, "a park"), facts(3, "a beach"))),
    )


def _synthesis_prompts() -> tuple[str, str]:
    from immich_memories.analysis.text_period_wire import _SYNTHESIS_PROMPT

    return (
        _SYNTHESIS_PROMPT.format(part_count=2, episode_count=9, parts="part 1: a river"),
        _SYNTHESIS_PROMPT.format(part_count=3, episode_count=14, parts="part 1: a park"),
    )


def _title_prompts() -> tuple[str, str]:
    from immich_memories.titles.llm_titles import build_title_prompt

    return (
        build_title_prompt(
            memory_type="trip",
            locale="en",
            start_date="2024-02-01",
            end_date="2024-02-08",
            duration_days=8,
            country="France",
        ),
        build_title_prompt(
            memory_type="year",
            locale="en",
            start_date="2024-01-01",
            end_date="2024-12-31",
            duration_days=366,
        ),
    )


def _special_day_prompts() -> tuple[str, str]:
    from immich_memories.analysis.special_day import _PROMPT

    return (
        _PROMPT.format(lines="09:00 a river"),
        _PROMPT.format(lines="10:00 a park\n11:00 a beach"),
    )


# Byte floors for the head each stage hands a server on every call. Lower bounds on what
# this layout guarantees, checked against the rendered prompt rather than a constant, so
# a variable slipped back into the middle of a preamble fails here.
STAGES = [
    ("worthy", _worth_prompts, 400),
    ("standing", _standing_prompts, 800),
    ("shareability-activity", _activity_prompts, 2800),
    ("shareability-exposure", _exposure_prompts, 1000),
    # 1400, not 1500: the inferred episode context left the preamble, so the head is
    # ~90 bytes shorter and the same for every film asking the same day.
    ("moment-inventory", _inventory_prompts, 1400),
    ("story-episodes", _episode_page_prompts, 900),
    ("story-grouping", _grouping_prompts, 1500),
    ("story-weighing", _weighing_prompts, 1500),
    ("story-pick", _pick_prompts, 900),
    ("episode-read", _episode_read_prompts, 800),
    ("period-insight", _period_prompts, 600),
    ("period-synthesis", _synthesis_prompts, 800),
    ("title", _title_prompts, 1500),
    ("special-day", _special_day_prompts, 900),
]


@pytest.mark.parametrize(("stage", "build", "floor"), STAGES, ids=[s[0] for s in STAGES])
def test_two_calls_in_a_stage_share_a_byte_identical_head(stage, build, floor):
    first, second = build()
    assert first != second, f"{stage}: the two prompts must differ somewhere"
    shared = shared_prefix(first, second)
    assert shared >= floor, (
        f"{stage}: only {shared} shared leading bytes, expected at least {floor}. "
        "Something that varies moved ahead of something shared."
    )


def test_a_repair_round_keeps_the_head_it_was_asked_with():
    """Recovery appends; it must never rewrite the front of the prompt."""
    from immich_memories.analysis.editorial_json_completion import json_format_repair_prompt

    original = _grouping_prompts()[0]
    assert json_format_repair_prompt(original).startswith(original)


def test_the_preamble_survives_a_json_round_trip_of_the_evidence():
    """Two pages of different evidence still agree on everything before the evidence."""
    first, second = _inventory_prompts()
    head = first[: shared_prefix(first, second)]
    assert "NEW SOURCES" not in head
    assert json.dumps({"source": "U0001"}) not in head
