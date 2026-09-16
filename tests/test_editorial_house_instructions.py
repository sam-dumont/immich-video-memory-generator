"""Operator house instructions ride the intent contract without touching its gates."""

from __future__ import annotations

import click
import pytest

from immich_memories.analysis.editorial_intent import build_editorial_intent
from immich_memories.cli.generate_resolution import apply_house_instructions
from immich_memories.config_models_editorial import EditorialConfig
from tests.test_editorial_on_this_day_year_limit import ranges


def test_house_instructions_render_last_with_their_rank():
    intent = build_editorial_intent(
        "monthly_highlights",
        ranges((2030,)),
        brief="The requested memory",
        house_instructions="I like selfies\nprefer pictures with mom",
    )

    block = intent.prompt_block()

    assert "OPERATOR INSTRUCTIONS" in block
    assert block.endswith("I like selfies\nprefer pictures with mom")


def test_house_instructions_change_the_bank_identity():
    base = build_editorial_intent(
        "monthly_highlights", ranges((2030,)), brief="The requested memory"
    )
    with_taste = build_editorial_intent(
        "monthly_highlights",
        ranges((2030,)),
        brief="The requested memory",
        house_instructions="I like selfies",
    )

    assert base.identity() != with_taste.identity()


def test_empty_instructions_keep_the_prompt_byte_for_byte():
    base = build_editorial_intent(
        "monthly_highlights", ranges((2030,)), brief="The requested memory"
    )
    same = build_editorial_intent(
        "monthly_highlights",
        ranges((2030,)),
        brief="The requested memory",
        house_instructions="",
    )

    assert base.prompt_block() == same.prompt_block()
    assert base.identity() == same.identity()


def test_house_instructions_reach_the_reader_and_the_record(tmp_path):
    from dataclasses import replace

    from tests.editorial_story_fixtures import ControlledStoryJudge
    from tests.test_editorial_duration_planner_integration import run
    from tests.test_editorial_on_this_day_year_limit import make_source

    source = make_source(tmp_path / "taste")
    steered = replace(
        source,
        intent=replace(source.intent, house_instructions="prefer pictures with mom"),
        bank_dir=tmp_path / "taste" / "banks",
        artifact_dir=tmp_path / "taste" / "plan",
    )
    judge = ControlledStoryJudge()
    plan = run(steered, judge)

    assert plan["intent"]["house_instructions"] == "prefer pictures with mom"
    assert any("prefer pictures with mom" in call["prompt"] for call in judge.calls)


def test_different_instructions_key_their_own_banks(tmp_path):
    from dataclasses import replace

    from tests.editorial_story_fixtures import ControlledStoryJudge
    from tests.test_editorial_duration_planner_integration import run
    from tests.test_editorial_on_this_day_year_limit import make_source

    first_source = make_source(tmp_path / "first")
    second_source = make_source(tmp_path / "second")
    arms = (
        replace(
            first_source,
            intent=replace(first_source.intent, house_instructions="I like selfies"),
            bank_dir=tmp_path / "first" / "banks",
            artifact_dir=tmp_path / "first" / "plan",
        ),
        replace(
            second_source,
            intent=replace(second_source.intent, house_instructions="diverse days over deep days"),
            bank_dir=tmp_path / "second" / "banks",
            artifact_dir=tmp_path / "second" / "plan",
        ),
    )

    keys = [run(arm, ControlledStoryJudge())["contract_key"] for arm in arms]

    assert keys[0] != keys[1]


def test_instructions_past_the_cap_are_rejected_at_the_intent():
    with pytest.raises(ValueError, match="exceed 1000"):
        build_editorial_intent(
            "monthly_highlights",
            ranges((2030,)),
            brief="The requested memory",
            house_instructions="x" * 1001,
        )


def test_the_cli_override_sets_the_config_only_when_given():
    config = EditorialConfig()

    apply_house_instructions(config, None)
    assert config.house_instructions == ""

    apply_house_instructions(config, "diverse days over deep days")
    assert config.house_instructions == "diverse days over deep days"

    with pytest.raises(click.UsageError, match="exceeds 1000"):
        apply_house_instructions(config, "x" * 1001)
