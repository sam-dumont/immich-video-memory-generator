"""Refuse expensive readers and image-incompatible endpoints before a full cell."""

import io
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import setup_matrix_probe_readers as probe  # noqa: E402
from setup_matrix_plan import Cell, CellPlan, Plan, PlanError, Step  # noqa: E402

from immich_memories.analysis import editorial_picture_facts  # noqa: E402
from immich_memories.analysis.llm_wire import LLMTransportAttempt  # noqa: E402
from immich_memories.config_models_llm import LLMConfig  # noqa: E402


def _result(shape="story-pick", seconds=10, completion=10_000):
    return probe.ShapeResult(
        shape=shape,
        model="reader",
        status="200",
        finish_reason="stop",
        content_chars=100,
        completion_tokens=completion,
        reasoning_tokens=9000,
        prompt_tokens=1000,
        seconds=seconds,
        cost="EUR 0.01010",
        verdict="ok",
        usage_measured=True,
    )


PRICING = {
    "hosted_test": {
        "currency": "EUR",
        "reader": {
            "input_per_million": 0.1,
            "output_per_million": 1,
        },
    }
}
BUDGET = {
    "max_seconds": 300,
    "max_cost": {"EUR": 0.25},
    "calls": {"story-pick": {"count": 100, "parallel": True}},
}


def test_projected_spend_includes_all_calls_without_double_billing_reasoning(capsys):
    config = LLMConfig(model="reader", reader_concurrency=4)
    problems = probe.check_budget(
        [_result()],
        budget=BUDGET,
        config=config,
        reader="hosted_test",
        pricing=PRICING,
    )
    output = capsys.readouterr().out
    assert "250s / 300s" in output
    assert "EUR 1.01000 / 0.25000" in output
    assert problems == ["projected cost exceeds the library ceiling"]


def test_a_serial_reader_is_refused_on_time_even_when_it_costs_no_tokens(capsys):
    config = LLMConfig(model="reader", reader_concurrency=1)
    problems = probe.check_budget(
        [_result()],
        budget=BUDGET,
        config=config,
        reader="local_model",
        pricing={},
    )
    assert problems == ["projected time exceeds the library ceiling"]
    assert "1000s / 300s" in capsys.readouterr().out


def test_an_unpriced_hosted_reader_does_not_silently_pass_the_cost_gate():
    config = LLMConfig(model="reader", reader_concurrency=4)
    problems = probe.check_budget(
        [_result()],
        budget=BUDGET,
        config=config,
        reader="hosted_test",
        pricing={},
    )
    assert problems == ["cannot project hosted cost: missing prices or token usage"]


def test_picture_probe_sends_the_production_prompt_and_a_real_fixture_tile(monkeypatch):
    observed = {}

    async def query(prompt, config, **kwargs):
        observed.update(prompt=prompt, **kwargs)
        return json.dumps(
            {
                field: "no" if field == "uncovered_person" else "not visible"
                for field in editorial_picture_facts.FIELDS
            }
        )

    monkeypatch.setattr(probe, "query_llm", query)
    result = probe.probe_shape(
        probe.SHAPES[0],
        LLMConfig(model="reader"),
        reader="local_model",
        pricing={},
    )
    assert result.shape == "picture-facts"
    assert not result.failed
    assert observed["prompt"] == editorial_picture_facts.PROMPT
    assert observed["max_tokens"] == editorial_picture_facts.MAX_OUTPUT_TOKENS
    assert observed["image_detail"] == "high"
    with Image.open(io.BytesIO(observed["images"][0])) as picture:
        assert max(picture.size) == 800
        assert len(picture.getcolors(256) or []) != 1


def test_a_reader_that_rejects_images_fails_the_first_shape(monkeypatch):
    async def query(*args, **kwargs):
        raise ValueError("this model does not support images")

    monkeypatch.setattr(probe, "query_llm", query)
    result = probe.probe_shape(
        probe.SHAPES[0],
        LLMConfig(model="reader"),
        reader="local_model",
        pricing={},
    )
    assert result.failed
    assert "does not support images" in result.verdict


def _plan():
    cell = Cell(
        id="test-reader",
        lane="mac",
        reader="hosted_test",
        facts="local",
        tier="no_captions",
        why="",
        requires_env=(),
        config={},
        inference_overlay=False,
    )
    item = CellPlan(
        cell=cell,
        pins={
            "llm.model": "reader",
            "llm.provider": "openai-compatible",
            "llm.base_url": "http://localhost:9999/v1",
        },
        config_yaml="",
        steps=(Step("probe-readers", ("python", "probe.py")),),
        manifests={},
        app_credentials=(),
        cache_dir="",
    )
    return Plan(
        library="demo", month="2024-06", image="image:tag", anonymize_required=False, cells=(item,)
    )


def test_budget_override_and_config_are_forwarded_only_to_the_named_probe(tmp_path):
    plan = _plan()
    other = replace(plan.cells[0], cell=replace(plan.cells[0].cell, id="other"))
    plan = replace(plan, cells=(*plan.cells, other))
    configured = probe.configure_reader_probes(
        plan,
        config_source=tmp_path / "config.yaml",
        env_files=[tmp_path / ".env"],
        budget_overrides=["test-reader"],
    )
    first, second = (item.steps[0].command for item in configured.cells)
    assert first[-2:] == ("--allow-reader-budget-overrun", "test-reader")
    assert "--allow-reader-budget-overrun" not in second
    assert str(tmp_path / "config.yaml") in first
    assert str(tmp_path / ".env") in first
    with pytest.raises(PlanError, match="unselected"):
        probe.configure_reader_probes(
            plan, config_source=None, env_files=[], budget_overrides=["typo"]
        )


@pytest.mark.parametrize(
    "broken,override,exit_code", [(False, False, 1), (False, True, 0), (True, True, 1)]
)
def test_an_override_waives_cost_but_never_image_failure(
    monkeypatch, tmp_path, broken, override, exit_code
):
    config_source = tmp_path / "config.yaml"
    config_source.write_text("immich:\n  url: http://localhost:9998\n  api_key: test-only\n")
    # One real image shape is enough to exercise this gate without replaying the text contracts.
    monkeypatch.setattr(probe, "SHAPES", probe.SHAPES[:1])

    async def query(prompt, config, **kwargs):
        if broken:
            raise ValueError("images not supported")
        kwargs["transport_observer"](
            LLMTransportAttempt(
                attempt=1,
                outcome="success",
                status_code=200,
                finish_reason="stop",
                prompt_tokens=1000,
                completion_tokens=10_000,
            )
        )
        return json.dumps(
            {
                field: "no" if field == "uncovered_person" else "not visible"
                for field in editorial_picture_facts.FIELDS
            }
        )

    monkeypatch.setattr(probe, "query_llm", query)
    budget = {**BUDGET, "calls": {"picture-facts": {"count": 100, "parallel": False}}}
    result = probe.probe_cells(
        _plan(),
        config_source,
        PRICING,
        budget=budget,
        budget_overrides=["test-reader"] if override else [],
    )
    assert result == exit_code
