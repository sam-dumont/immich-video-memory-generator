"""Pre-planner replies survive rejection without changing the request contract."""

import asyncio
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from immich_memories.analysis import editorial_runtime as runtime
from immich_memories.analysis import editorial_text_gateway as gateway
from immich_memories.analysis.selection_trace import Trace
from immich_memories.config_loader import Config
from immich_memories.config_models_llm import LLMConfig
from tests.test_editorial_runtime import _window


def requester(directory: Path):
    from immich_memories.analysis.editorial_text_artifacts import TextPromptArtifacts

    return gateway.SyncTextPromptRequester(
        LLMConfig(
            provider="openai-compatible",
            base_url="http://text.test/v1",
            model="test-model",
            api_key="private-test-credential",
        ),
        max_tokens=3000,
        timeout_seconds=45,
        artifacts=TextPromptArtifacts(lambda: directory, stage="period"),
    )


def records(directory):
    return sorted(
        [
            json.loads(path.read_text())
            for path in directory.glob("pre-planner-calls/*.outcome.private.json")
        ],
        key=lambda row: row["started_at"],
    )


def test_exact_complete_reply_is_retained_before_episode_parser_rejects_it(tmp_path, monkeypatch):
    from immich_memories.analysis.text_episode_answers import _read_response_result

    seen = []
    raw = '  {"schema_version":"period-insight-text-v1","thesis":"A day","evidence":[]}\n'

    async def query(prompt, config, **kwargs):
        seen.append((prompt, config.model, kwargs))
        # A crash while the provider runs still leaves the exact input on disk.
        assert [
            p.read_text() for p in tmp_path.glob("pre-planner-calls/*.request.private.txt")
        ] == ["Read every source.\n"]
        return raw

    monkeypatch.setattr(gateway, "query_llm", query)
    answer = requester(tmp_path)("Read every source.\n")
    assert answer == raw
    assert not _read_response_result(answer, ()).readings
    assert callable(seen[0][2].pop("transport_observer"))
    assert seen == [
        (
            "Read every source.\n",
            "test-model",
            {
                "temperature": 0.0,
                "max_tokens": 3000,
                "timeout_seconds": 45,
                "thinking": False,
                "cache_path": None,
                "require_complete": True,
                "response_format": None,
            },
        )
    ]
    assert [p.read_text() for p in tmp_path.glob("pre-planner-calls/*.response.private.txt")] == [
        raw
    ]
    [record] = records(tmp_path)
    assert record["status"] == "complete_transport"
    assert record["parser_validated"] is False
    assert record["request"]["max_tokens"] == 3000
    assert record["request"]["thinking"] is False
    assert record["response_chars"] == len(raw)
    files = list((tmp_path / "pre-planner-calls").iterdir())
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in files)
    assert stat.S_IMODE((tmp_path / "pre-planner-calls").stat().st_mode) == 0o700
    assert all("private-test-credential" not in p.read_text() for p in files)


def test_existing_double_budget_retry_retains_both_complete_and_partial_replies(
    tmp_path, monkeypatch
):
    budgets = []

    async def query(_prompt, _config, **kwargs):
        budgets.append(kwargs["max_tokens"])
        if len(budgets) == 1:
            raise gateway.LLMIncompleteResponse('{"partial":')
        return '{"complete":true}'

    monkeypatch.setattr(gateway, "query_llm", query)
    assert requester(tmp_path)("Exact original question") == '{"complete":true}'
    assert budgets == [3000, 6000]
    first, second = records(tmp_path)
    assert [first["request"]["max_tokens"], second["request"]["max_tokens"]] == budgets
    assert first["status"] == "raised"
    assert first["error_type"] == "LLMIncompleteResponse"
    assert second["status"] == "complete_transport"
    assert {p.read_text() for p in tmp_path.glob("pre-planner-calls/*.response.private.txt")} == {
        '{"partial":',
        '{"complete":true}',
    }


def test_transport_failure_is_retained_and_not_retried(tmp_path, monkeypatch):
    failure = RuntimeError("the original transport failure")
    calls = []

    async def query(*args, **kwargs):
        calls.append(kwargs)
        raise failure

    monkeypatch.setattr(gateway, "query_llm", query)
    with pytest.raises(RuntimeError) as caught:
        requester(tmp_path)("Keep this failed input")
    assert caught.value is failure
    assert len(calls) == 1
    [record] = records(tmp_path)
    assert record["status"] == "raised" and record["error_type"] == "RuntimeError"
    assert not list(tmp_path.glob("pre-planner-calls/*.response.private.txt"))


def test_cancellation_identity_is_preserved_without_retry(tmp_path, monkeypatch):
    cancellation = asyncio.CancelledError()
    calls = []

    async def query(*args, **kwargs):
        calls.append(kwargs)
        raise cancellation

    monkeypatch.setattr(gateway, "query_llm", query)
    with pytest.raises(asyncio.CancelledError) as caught:
        requester(tmp_path)("Stop without retrying")
    assert caught.value is cancellation and len(calls) == 1
    [record] = records(tmp_path)
    assert record["status"] == "raised" and record["error_type"] == "CancelledError"


def test_artifact_write_failure_does_not_change_reply_or_trigger_a_retry(
    tmp_path, monkeypatch, caplog
):
    from immich_memories.analysis import editorial_text_artifacts as artifacts

    calls = []

    async def query(*args, **kwargs):
        calls.append(kwargs)
        return "unchanged reply"

    def cannot_write(*args):
        raise OSError("private diagnostic path unavailable")

    monkeypatch.setattr(gateway, "query_llm", query)
    monkeypatch.setattr(artifacts, "write_secret_file", cannot_write)
    assert requester(tmp_path)("unchanged input") == "unchanged reply"
    assert len(calls) == 1
    assert "Could not record private text request" in caplog.text


@pytest.mark.parametrize("provider_fails", [False, True])
def test_finish_write_failure_preserves_original_reply_or_exception(
    tmp_path, monkeypatch, caplog, provider_fails
):
    from immich_memories.analysis import editorial_text_artifacts as artifacts

    failure = RuntimeError("original failure")
    calls = []
    write = artifacts.write_secret_file

    async def query(*args, **kwargs):
        calls.append(kwargs)
        if provider_fails:
            raise failure
        return "original reply"

    def fail_finish(path, text):
        if path.name.endswith(".outcome.private.json") and json.loads(text)["status"] != "started":
            raise OSError("diagnostic finalization failed")
        write(path, text)

    monkeypatch.setattr(gateway, "query_llm", query)
    monkeypatch.setattr(artifacts, "write_secret_file", fail_finish)
    if provider_fails:
        with pytest.raises(RuntimeError) as caught:
            requester(tmp_path)("unchanged input")
        assert caught.value is failure
    else:
        assert requester(tmp_path)("unchanged input") == "original reply"
    assert len(calls) == 1
    assert records(tmp_path)[0]["status"] == "started"
    assert "Could not record private text response" in caplog.text


def test_production_episode_recording_follows_each_active_attempt_even_on_failure(
    tmp_path, monkeypatch
):
    context = runtime.EditorialRunContext(
        "episode-control",
        "A period",
        "monthly_highlights",
        (_window(2024, 7, 12),),
        30,
        tmp_path / "artifacts",
    )
    # The whole-film planner's up-front episode stage; the polish route reads on demand.
    config = Config(
        llm={"model": "test-model"},
        cache={"directory": str(tmp_path / "cache")},
        editorial={"thin_model_layer": False},
    )
    planner = runtime.build_editorial_planner(
        client=object(),
        thumbnail_cache=object(),
        context=context,
        config=config,
        ports=runtime.EditorialRuntimePorts(load_people=lambda: {}),
    )
    calls = []

    async def query(prompt, *_args, **kwargs):
        calls.append((prompt, kwargs))
        return "exact invalid episode reply"

    def plan_source(*args, **kwargs):
        reader = planner._planner._episode_reader_factory(SimpleNamespace(candidates=()))
        assert reader._requester("exact episode question") == "exact invalid episode reply"
        raise RuntimeError("episode parser refused")

    monkeypatch.setattr(gateway, "query_llm", query)
    monkeypatch.setattr(planner, "_plan_source", plan_source)
    attempts = []
    for _ in range(2):
        with pytest.raises(RuntimeError, match="episode parser refused"):
            planner.plan_source([], trace=Trace())
        attempts.append(planner.last_attempt_directory)
    assert len(calls) == 2 and len(set(attempts)) == 2
    for attempt in attempts:
        assert len(records(attempt)) == 1
        assert [
            p.read_text() for p in attempt.glob("pre-planner-calls/*.response.private.txt")
        ] == ["exact invalid episode reply"]
        assert json.loads((attempt / "status.private.json").read_text())["status"] == "failed"
    assert not (context.artifact_dir / "pre-planner-calls").exists()
    planner.close()


def test_the_episode_stage_keeps_its_own_prompt_transcript(tmp_path, monkeypatch):
    context = runtime.EditorialRunContext(
        "episode-control",
        "A period",
        "monthly_highlights",
        (_window(2024, 7, 12),),
        30,
        tmp_path / "artifacts",
    )
    # The whole-film planner's up-front episode stage; the polish route reads on demand.
    config = Config(
        llm={"model": "test-model"},
        cache={"directory": str(tmp_path / "cache")},
        editorial={"thin_model_layer": False},
    )
    planner = runtime.build_editorial_planner(
        client=object(),
        thumbnail_cache=object(),
        context=context,
        config=config,
        ports=runtime.EditorialRuntimePorts(load_people=lambda: {}),
    )

    async def query(_prompt, *_args, **_kwargs):
        return "exact invalid episode reply"

    monkeypatch.setattr(gateway, "query_llm", query)
    reader = planner._planner._episode_reader_factory(SimpleNamespace(candidates=()))
    try:
        assert reader._requester("Read these episodes.") == "exact invalid episode reply"
    finally:
        planner.close()
    [record] = records(context.artifact_dir)
    assert record["stage"] == "episodes"
    assert [
        p.read_text() for p in context.artifact_dir.glob("pre-planner-calls/*.request.private.txt")
    ] == ["Read these episodes."]


def _billing(finish_reason, completion_tokens, reasoning_tokens):
    from immich_memories.analysis.llm_wire import LLMTransportAttempt

    return LLMTransportAttempt(
        1,
        "response" if finish_reason == "stop" else "incomplete",
        200,
        None,
        finish_reason=finish_reason,
        completion_tokens=completion_tokens,
        reasoning_tokens=reasoning_tokens,
    )


def test_the_outcome_record_names_what_the_provider_billed(tmp_path, monkeypatch):
    async def query(_prompt, _config, **kwargs):
        kwargs["transport_observer"](_billing("stop", 2005, 622))
        return '{"schema_version":"period-insight-text-v1"}'

    monkeypatch.setattr(gateway, "query_llm", query)
    requester(tmp_path)("Read every source.\n")

    [record] = records(tmp_path)
    assert record["reply"] == {
        "finish_reason": "stop",
        "completion_tokens": 2005,
        "reasoning_tokens": 622,
    }


def test_a_starved_outcome_record_names_the_reasoning_that_took_the_budget(tmp_path, monkeypatch):
    from immich_memories.analysis.llm_wire import LLMIncompleteResponse

    async def query(_prompt, _config, **kwargs):
        kwargs["transport_observer"](_billing("length", 8492, 8492))
        raise LLMIncompleteResponse(
            "",
            finish_reason="length",
            completion_tokens=8492,
            reasoning_tokens=8492,
        )

    monkeypatch.setattr(gateway, "query_llm", query)
    with pytest.raises(LLMIncompleteResponse):
        requester(tmp_path)("Read every source.\n")

    starved = records(tmp_path)[0]
    assert starved["reply"]["reasoning_tokens"] == 8492
    assert starved["reply"]["finish_reason"] == "length"
    assert "reasoning used 8492 of 8492 tokens, no answer" in starved["error"]
