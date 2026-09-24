"""Refuse expensive or broken readers before a full cell; never send one a picture."""

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import setup_matrix  # noqa: E402
import setup_matrix_probe_readers as probe  # noqa: E402
from setup_matrix_plan import (  # noqa: E402
    FROM_OPERATOR_CONFIG,
    Cell,
    CellPlan,
    Plan,
    PlanError,
    Step,
)

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


# A shape of the probe's own form whose verdict reads anything as in contract, so the gates
# around the shapes can be driven without replaying the recorded production prompts.
TEXT_SHAPE = ("episodes", "episodes.txt", 100, True, lambda _raw, _prompt: "ok")


def test_every_probe_shape_is_text_and_sends_no_picture(monkeypatch):
    observed = []

    async def query(prompt, config, **kwargs):
        observed.append(kwargs)
        return "{}"

    # WHY: query_llm is the reader's HTTP boundary, and no reader listens in a test.
    monkeypatch.setattr(probe, "query_llm", query)
    for shape in probe.SHAPES:
        probe.probe_shape(shape, LLMConfig(model="reader"), reader="local_model", pricing={})

    assert [shape[0] for shape in probe.SHAPES] == ["episodes", "story-pick"]
    assert observed and not any(kwargs.get("images") for kwargs in observed)


def test_a_reader_that_errors_fails_the_first_shape(monkeypatch):
    async def query(*args, **kwargs):
        raise ValueError("this model is not loaded")

    # WHY: query_llm is the reader's HTTP boundary, and no reader listens in a test.
    monkeypatch.setattr(probe, "query_llm", query)
    result = probe.probe_shape(
        probe.SHAPES[0],
        LLMConfig(model="reader"),
        reader="local_model",
        pricing={},
    )
    assert result.failed
    assert "not loaded" in result.verdict


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


def _text_reader(*, broken: bool = False):
    """A reader that answers in contract at a measured cost, or fails outright."""

    async def query(prompt, config, **kwargs):
        if broken:
            raise ValueError("the reader is down")
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
        return "{}"

    return query


def _text_budget(count: int) -> dict:
    return {**BUDGET, "calls": {"episodes": {"count": count, "parallel": False}}}


@pytest.mark.parametrize(
    "broken,override,exit_code", [(False, False, 1), (False, True, 0), (True, True, 1)]
)
def test_an_override_waives_cost_but_never_a_failed_reader(
    monkeypatch, tmp_path, broken, override, exit_code
):
    config_source = tmp_path / "config.yaml"
    config_source.write_text("immich:\n  url: http://localhost:9998\n  api_key: test-only\n")
    monkeypatch.setattr(probe, "SHAPES", (TEXT_SHAPE,))
    # WHY: query_llm is the reader's HTTP boundary, and no reader listens in a test.
    monkeypatch.setattr(probe, "query_llm", _text_reader(broken=broken))
    result = probe.probe_cells(
        _plan(),
        config_source,
        PRICING,
        budget=_text_budget(100),
        budget_overrides=["test-reader"] if override else [],
    )
    assert result == exit_code


def test_a_cluster_cell_on_the_operators_immich_is_probed_rather_than_crashing(
    monkeypatch, tmp_path
):
    """Probing a cell writes its config, and its manifests carry the operator's server.

    Nothing loaded that server before a probe did, so every reader probe of such
    a cell died on `KeyError: 'url'` before its first request, and the February
    hosted rows of 09-13/14 came back empty.
    """
    config_source = tmp_path / "config.yaml"
    config_source.write_text("immich:\n  url: http://localhost:9998\n  api_key: test-only\n")
    plan = _plan()
    item = replace(
        plan.cells[0],
        cell=replace(plan.cells[0].cell, lane="k8s"),
        manifests={"configmap.yaml": f"data:\n  url: {FROM_OPERATOR_CONFIG}\n"},
        operator_immich=True,
    )
    read_from = []

    def operator_immich(source):
        read_from.append(source)
        return "http://immich.invalid:2283", "operator-key"

    # WHY: read_operator_immich reads the operator's own config off this machine.
    monkeypatch.setattr(setup_matrix, "read_operator_immich", operator_immich)
    monkeypatch.setattr(probe, "SHAPES", (TEXT_SHAPE,))
    # WHY: query_llm is the reader's HTTP boundary, and no reader listens in a test.
    monkeypatch.setattr(probe, "query_llm", _text_reader())

    result = probe.probe_cells(
        replace(plan, cells=(item,)), config_source, PRICING, budget=_text_budget(1)
    )

    assert result == 0
    assert read_from == [config_source], "read once, from the config the probe was given"
