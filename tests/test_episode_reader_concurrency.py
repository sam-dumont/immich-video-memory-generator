"""Episode provider calls overlap while the semantic bank stays in canonical order."""

import asyncio
import json
import re
import threading
from datetime import UTC, datetime, timedelta

import pytest

from immich_memories.analysis import editorial_text_gateway as gateway
from immich_memories.analysis import llm_metrics
from immich_memories.analysis.editorial_text_artifacts import TextPromptArtifacts
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.analysis.selection_source_groups import project_episode_groups
from immich_memories.analysis.text_episode_reader import (
    CachedTextEpisodeReader,
    TextEpisodeRequestLimits,
)
from immich_memories.config_models_llm import LLMConfig
from immich_memories.store.episode_readings import EpisodeReadingProducer, EpisodeReadingStore
from tests.conftest import make_asset
from tests.test_text_episode_reader import _AnnotationLines


def _reader(tmp_path, config):
    noon = datetime(2026, 2, 1, 12, tzinfo=UTC)
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: tuple(
                make_asset(f"event-{number}", file_created_at=noon + timedelta(days=number))
                for number in range(5)
            )
        ),
    )
    projections = project_episode_groups(prepared, prepared.candidate_ids)
    producer = EpisodeReadingProducer(
        model_id="test-reader",
        prompt_version="episode-prompt-v1",
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:student-v1",),
    )
    reader = CachedTextEpisodeReader(
        store=EpisodeReadingStore(tmp_path / "annotations.sqlite"),
        producer=producer,
        annotations=_AnnotationLines(
            {
                asset_id: f"{asset_id} | " + "A family sharing lunch outdoors. " * 12
                for asset_id in prepared.candidate_ids
            }
        ),
        requester=gateway.SyncTextPromptRequester(
            config,
            max_tokens=4000,
            timeout_seconds=30,
            artifacts=TextPromptArtifacts(lambda: tmp_path, "episode"),
        ),
        # One episode fits a request and two do not, so every page is its own call.
        limits=TextEpisodeRequestLimits(max_prompt_chars=2246),
    )
    return reader, projections


def _answer(number):
    return json.dumps(
        {
            "schema_version": "episode-reading-text-v1",
            "episodes": [
                {
                    "episode": 1,
                    "what_happened": f"Family event {number} happened.",
                    "representatives": [{"asset": 1, "reason": "Shows the family lunch."}],
                    "cull": [],
                }
            ],
        }
    )


def test_hosted_episodes_overlap_with_exact_usage_and_reusable_ordered_readings(
    tmp_path, monkeypatch
):
    active = maximum = 0
    lock = threading.Lock()
    overlap = threading.Event()
    requests = []
    finished = []

    async def completion(prompt, _config, **kwargs):
        nonlocal active, maximum
        number = int(re.search(r"event-(\d+)", prompt)[1])
        with lock:
            requests.append((prompt, kwargs["max_tokens"]))
            active += 1
            maximum = max(maximum, active)
            if active == 4:
                overlap.set()
        overlap.wait(timeout=0.1)
        await asyncio.sleep(0.04 if number == 0 else 0.001)
        with lock:
            active -= 1
            finished.append(number)
        llm_metrics.record_reply(prompt_tokens=3, completion_tokens=2)
        return _answer(number)

    # WHY: replace only the hosted completion; requests, metrics, artifacts and SQLite are real.
    monkeypatch.setattr(gateway, "query_llm", completion)
    config = LLMConfig(provider="openai", model="test-reader")
    reader, projections = _reader(tmp_path / "concurrent", config)
    with llm_metrics.collecting() as counters:
        result = reader.read(projections)

    assert maximum == 4
    assert finished[0] != 0
    assert result.actual_calls == counters.calls == 5
    assert (counters.prompt_tokens, counters.completion_tokens) == (15, 10)
    assert [episode.reading.what_happened for episode in result.episodes] == [
        f"Family event {number} happened." for number in range(5)
    ]
    assert (
        len(list((tmp_path / "concurrent" / "pre-planner-calls").glob("*.outcome.private.json")))
        == 5
    )
    warm = reader.read(projections)
    assert warm.actual_calls == 0
    assert all(episode.cache_hit for episode in warm.episodes)
    assert [episode.reading for episode in warm.episodes] == [
        episode.reading for episode in result.episodes
    ]
    parallel_requests = sorted(requests)
    requests.clear()
    active = maximum = 0
    serial_reader, serial_projections = _reader(
        tmp_path / "serial", config.model_copy(update={"reader_concurrency": 1})
    )
    serial = serial_reader.read(serial_projections)
    assert maximum == 1
    assert serial.episodes == result.episodes
    assert sorted(requests) == parallel_requests


def test_cancellation_banks_already_paid_sibling_answers_before_stopping(tmp_path, monkeypatch):
    from immich_memories.operations.cancellation import PipelineCancelled, cancellation_scope

    started = threading.Barrier(4, timeout=5)
    stopped = threading.Event()
    sent = []

    def check():
        if stopped.is_set():
            raise PipelineCancelled()

    async def completion(prompt, _config, **_kwargs):
        number = int(re.search(r"event-(\d+)", prompt)[1])
        sent.append(number)
        started.wait()
        if number == 1:
            stopped.set()
            raise PipelineCancelled()
        await asyncio.sleep(0.04 if number == 0 else 0.01)
        return _answer(number)

    # WHY: cancel at the remote boundary with other billed requests already in flight.
    monkeypatch.setattr(gateway, "query_llm", completion)
    reader, projections = _reader(tmp_path, LLMConfig(provider="openai", model="test-reader"))
    with cancellation_scope(check), pytest.raises(PipelineCancelled):
        reader.read(projections)
    assert sorted(sent) == [0, 1, 2, 3]

    async def remaining(prompt, _config, **_kwargs):
        return _answer(int(re.search(r"event-(\d+)", prompt)[1]))

    monkeypatch.setattr(gateway, "query_llm", remaining)
    resumed = reader.read(projections)
    assert [episode.cache_hit for episode in resumed.episodes] == [True, False, True, True, False]
    assert resumed.actual_calls == 2


def test_a_failed_pack_does_not_drop_siblings_and_only_unread_answers_are_retried(
    tmp_path, monkeypatch
):
    attempts = {}
    lock = threading.Lock()

    async def completion(prompt, _config, **_kwargs):
        number = int(re.search(r"event-(\d+)", prompt)[1])
        with lock:
            attempts[number] = attempts.get(number, 0) + 1
            attempt = attempts[number]
        if number == 1:
            raise RuntimeError("provider unavailable")
        if number == 2 and attempt == 1:
            return '{"episodes":[]}'
        return _answer(number)

    # WHY: mix a refused request with one valid transport answer that needs semantic recovery.
    monkeypatch.setattr(gateway, "query_llm", completion)
    reader, projections = _reader(tmp_path, LLMConfig(provider="openai", model="test-reader"))
    result = reader.read(projections)
    assert attempts == {0: 1, 1: 1, 2: 2, 3: 1, 4: 1}
    assert result.actual_calls == 6
    assert result.episodes[1].reading is None
    assert "provider unavailable" in result.episodes[1].unavailable_reason
    assert all(episode.reading is not None for i, episode in enumerate(result.episodes) if i != 1)
    resumed = reader.read(projections)
    assert [episode.cache_hit for episode in resumed.episodes] == [True, False, True, True, True]
    assert resumed.actual_calls == 1


@pytest.mark.parametrize("config", [LLMConfig(), LLMConfig(provider="openai")])
def test_cancellation_before_dispatch_sends_no_episode_requests(tmp_path, monkeypatch, config):
    from immich_memories.operations.cancellation import PipelineCancelled, cancellation_scope

    sent = []

    async def completion(prompt, _config, **_kwargs):
        sent.append(prompt)
        return _answer(0)

    monkeypatch.setattr(gateway, "query_llm", completion)
    reader, projections = _reader(tmp_path, config)
    stopped = False

    def check():
        if stopped:
            raise PipelineCancelled()

    with cancellation_scope(check), pytest.raises(PipelineCancelled):
        stopped = True
        reader.read(projections)
    assert sent == []
    assert not list(tmp_path.glob("pre-planner-calls/*"))
