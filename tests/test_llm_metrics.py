"""What a run spent on the LLM, counted where the answers arrive.

The truncation counter is the point of the exercise. `_query_openai` has
always detected `finish_reason == "length"` on a thinking call, logged a
warning and retried without thinking — silently. That silence is why #600's
2.4-hour truncation tax lived in an unread server log for months.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from immich_memories.analysis.llm_metrics import collecting
from immich_memories.config_models_llm import LLMConfig


def _openai_response(content='{"ok": true}', finish_reason="stop", usage=None):
    # WHY: the LLM server is the external boundary these tests measure across.
    response = AsyncMock()
    response.status_code = 200
    body = {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}
    if usage is not None:
        body["usage"] = usage
    response.json = MagicMock(return_value=body)
    response.raise_for_status = lambda: None
    return response


def _thinking_config(**overrides) -> LLMConfig:
    fields = {
        "provider": "openai-compatible",
        "base_url": "http://localhost:8080/v1",
        "model": "qwen-reasoning",
        "thinking": True,
    }
    fields.update(overrides)
    return LLMConfig(**fields)


@pytest.mark.asyncio
async def test_a_truncated_thinking_call_is_counted() -> None:
    """#600's regression test: the retry is silent, the count is not."""
    from immich_memories.analysis.llm_query import query_llm

    replies = [_openai_response(finish_reason="length"), _openai_response()]
    # WHY: the LLM server is the external boundary; two replies = truncation then retry.
    with collecting() as counters, patch("httpx.AsyncClient.post", side_effect=replies):
        await query_llm("Judge this cut", _thinking_config(), thinking=True)

    assert counters.truncated == 1
    assert counters.calls == 2


@pytest.mark.asyncio
async def test_a_healthy_call_records_one_call_and_no_truncation() -> None:
    from immich_memories.analysis.llm_query import query_llm

    # WHY: the LLM server is the external boundary this request reaches.
    with collecting() as counters, patch("httpx.AsyncClient.post", return_value=_openai_response()):
        await query_llm("Describe this", _thinking_config(thinking=False))

    assert counters.calls == 1
    assert counters.truncated == 0
    assert counters.wall_seconds >= 0.0


@pytest.mark.asyncio
async def test_token_usage_is_recorded_when_the_server_reports_it() -> None:
    """Servers that omit `usage` must not break counting — tokens stay zero."""
    from immich_memories.analysis.llm_query import query_llm

    reply = _openai_response(usage={"prompt_tokens": 1200, "completion_tokens": 34})
    # WHY: the LLM server is the external boundary reporting its own usage.
    with collecting() as counters, patch("httpx.AsyncClient.post", return_value=reply):
        await query_llm("Describe this", _thinking_config(thinking=False))

    assert counters.prompt_tokens == 1200
    assert counters.completion_tokens == 34


@pytest.mark.asyncio
async def test_counting_is_off_unless_a_run_asked_for_it() -> None:
    """No collector active means every record call is a no-op, not a crash."""
    from immich_memories.analysis.llm_query import query_llm

    # WHY: the LLM server is the external boundary this request reaches.
    with patch("httpx.AsyncClient.post", return_value=_openai_response()):
        answer = await query_llm("Describe this", _thinking_config(thinking=False))

    assert answer == '{"ok": true}'


def test_a_phase_records_only_the_llm_spend_that_happened_during_it(tmp_path) -> None:
    """Per-phase attribution is the point: which phase spent the model budget.

    A run-level total says "11 calls". The delta says the review made nine of
    them, which is the sentence that changes what you optimise.
    """
    from immich_memories.analysis import llm_metrics
    from immich_memories.tracking import RunTracker

    tracker = RunTracker("metrics-phases", db_path=tmp_path / "runs.db", capture_system=False)
    tracker.start_run(source="manual")

    with llm_metrics.collecting():
        tracker.start_phase("analysis", 2)
        llm_metrics.record_reply(prompt_tokens=100, completion_tokens=10)
        tracker.start_phase("selection", 1)  # completes "analysis"
        llm_metrics.record_reply(prompt_tokens=900, completion_tokens=20)
        llm_metrics.record_truncation()
        tracker.complete_phase()

    phases = {p.phase_name: p.extra_metrics for p in tracker.db.get_phase_stats(tracker.run_id)}

    assert phases["analysis"]["llm_calls"] == 1
    assert phases["analysis"]["llm_prompt_tokens"] == 100
    assert "llm_truncated" not in phases["analysis"]
    assert phases["selection"]["llm_calls"] == 1
    assert phases["selection"]["llm_prompt_tokens"] == 900
    assert phases["selection"]["llm_truncated"] == 1


def test_a_phase_with_no_llm_work_carries_no_llm_keys(tmp_path) -> None:
    """ "Did not use the model" must read differently from "used it for free"."""
    from immich_memories.analysis import llm_metrics
    from immich_memories.tracking import RunTracker

    tracker = RunTracker("metrics-quiet", db_path=tmp_path / "runs.db", capture_system=False)
    tracker.start_run(source="manual")

    with llm_metrics.collecting():
        tracker.start_phase("assembly", 1)
        tracker.complete_phase()

    stats = tracker.db.get_phase_stats(tracker.run_id)[0]

    assert not any(key.startswith("llm_") for key in stats.extra_metrics)


def test_the_run_row_carries_the_whole_runs_llm_spend(tmp_path) -> None:
    """Phases cannot hold the total, because the costly work happens outside them.

    RunTracker records only clip_extraction, assembly and music — all inside
    generate_memory — while analysis and selection run before the run row
    exists and spend nearly all of the model budget. So the run-level total is
    not a sum over phases; it has to be recorded in its own right.
    """
    from immich_memories.analysis import llm_metrics
    from immich_memories.tracking import RunDatabase, RunTracker

    db_path = tmp_path / "runs.db"
    tracker = RunTracker("metrics-run-total", db_path=db_path, capture_system=False)
    tracker.start_run(source="manual")

    with llm_metrics.collecting():
        # spend that belongs to no tracked phase, as analysis and selection do
        llm_metrics.record_reply(prompt_tokens=5000, completion_tokens=120)
        llm_metrics.record_truncation()
        tracker.start_phase("assembly", 1)
        llm_metrics.record_reply(prompt_tokens=40, completion_tokens=4)
        tracker.complete_phase()
        tracker.complete_run(clips_analyzed=9, clips_selected=4)

    run = RunDatabase(db_path).get_run("metrics-run-total")

    assert run is not None
    assert run.llm_metrics["llm_calls"] == 2
    assert run.llm_metrics["llm_prompt_tokens"] == 5040
    assert run.llm_metrics["llm_truncated"] == 1


def test_a_run_that_never_used_the_model_stores_no_llm_metrics(tmp_path) -> None:
    from immich_memories.tracking import RunDatabase, RunTracker

    db_path = tmp_path / "runs.db"
    tracker = RunTracker("metrics-run-quiet", db_path=db_path, capture_system=False)
    tracker.start_run(source="manual")
    tracker.complete_run()

    run = RunDatabase(db_path).get_run("metrics-run-quiet")

    assert run is not None
    assert run.llm_metrics == {}
