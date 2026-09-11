"""A user stop survives model fallbacks and leaves completed answers reusable."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from immich_memories.analysis.editorial_async_bridge import _run_sync
from immich_memories.analysis.editorial_case import TextRequest
from immich_memories.analysis.editorial_text_gateway import QueryTextRequester
from immich_memories.analysis.llm_query import query_llm
from immich_memories.cache.judgment_cache import JudgmentCache
from immich_memories.config_models_llm import LLMConfig
from immich_memories.operations.cancellation import (
    PipelineCancelled,
    cancellation_scope,
    check_cancelled,
)
from tests.test_editorial_source_progress import pipeline_for
from tests.test_editorial_source_route import photo


def stop():
    raise PipelineCancelled("Cancelled by user")


@pytest.mark.parametrize("stop_at", ["source-stage", "tracker-start"])
def test_cancelled_before_planning_cleans_up_without_false_completion(
    mock_immich_client, mock_analysis_cache, mock_thumbnail_cache, stop_at
):
    def must_not_plan(*_args, **_kwargs):
        pytest.fail("A cancelled job started planning")

    pipeline = pipeline_for(
        SimpleNamespace(plan_source=must_not_plan),
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
    )
    cancellation = PipelineCancelled("original cancellation")

    def cancelled_display(_event):
        raise cancellation

    if stop_at == "tracker-start":
        pipeline.tracker.add_callback(cancelled_display)

    # WHY: observe native resource release and completion without replacing the stop callback.
    with (
        patch.object(pipeline.tracker, "finish", wraps=pipeline.tracker.finish) as finish,
        patch.object(pipeline.analyzer, "close") as close_analyzer,
        patch.object(pipeline.previewer, "close") as close_previewer,
        pytest.raises(PipelineCancelled) as caught,
    ):
        pipeline.run_editorial_source([photo("one")], cancelled_display)
    assert caught.value is cancellation
    finish.assert_not_called()
    close_analyzer.assert_called_once()
    close_previewer.assert_called_once()
    assert pipeline.tracker.progress.operational_event is None


@pytest.mark.asyncio
async def test_cancel_from_running_ui_reaches_sync_bridge_thread():
    checks = []

    def check():
        checks.append(True)
        if len(checks) > 1:
            stop()

    async def request():
        check_cancelled()
        pytest.fail("Cancellation was lost across the async bridge")

    with cancellation_scope(check), pytest.raises(PipelineCancelled):
        _run_sync(request())
    check_cancelled()  # No stopped scope leaks into the next run.


@pytest.mark.asyncio
async def test_completed_request_is_banked_but_cancel_prevents_next_request(tmp_path, monkeypatch):
    stopped = False
    requests = []

    def check():
        if stopped:
            stop()

    async def dispatch(*args, **_kwargs):
        nonlocal stopped
        requests.append(args[0])
        stopped = True
        return '{"decision":"complete"}'

    # Provider transport is the boundary; real gateway validation/cache are retained.
    monkeypatch.setattr("immich_memories.analysis.llm_query._dispatch", dispatch)
    config = LLMConfig(provider="openai-compatible", model="test-model")
    request = TextRequest(
        prompt="first question",
        llm_config=config,
        cache_path=tmp_path / "answers.sqlite",
        max_tokens=200,
        timeout_seconds=30,
        thinking=False,
    )
    requester = QueryTextRequester()
    with cancellation_scope(check):
        first = await requester.request(request)
        assert first.raw == '{"decision":"complete"}'
        with pytest.raises(PipelineCancelled):
            try:
                await query_llm("second question", config)
            except Exception:
                pytest.fail("A model fallback swallowed cancellation")
    assert requests == ["first question"]
    cache = JudgmentCache(request.cache_path)
    try:
        assert cache.answer_for(request.judgment_key) == first.raw
        assert cache.completion_failure_for(request.judgment_key) is None
    finally:
        cache.close()
    replay = await requester.request(request)
    assert replay.cache_hit
    assert requests == ["first question"]
