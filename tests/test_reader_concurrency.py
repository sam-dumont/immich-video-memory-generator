"""Independent reads overlap without changing prompts, votes or their persistent bank."""

import asyncio
import logging
import threading

import pytest

from immich_memories.analysis.editorial_block_votes import judge_standing
from immich_memories.analysis.editorial_structure_io import StructureTextJudge
from immich_memories.config import Config
from immich_memories.config_models_llm import LLMConfig


def test_standing_overlaps_blocks_but_keeps_order_and_reuses_the_same_bank(tmp_path, monkeypatch):
    from immich_memories.analysis import editorial_text_gateway as gateway
    from immich_memories.analysis import llm_metrics

    active = maximum = 0
    lock = threading.Lock()
    overlap = threading.Event()
    sent = []

    async def completion(prompt, _config, **_kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            sent.append(prompt)
            if active == 2:
                overlap.set()
        overlap.wait(timeout=0.15)
        await asyncio.sleep(0.01)
        with lock:
            active -= 1
        llm_metrics.record_reply(prompt_tokens=3, completion_tokens=2)
        return '{"weak":{}}'

    # WHY: replace only the remote model; keep real request identities, audit files and SQLite.
    monkeypatch.setattr(gateway, "query_llm", completion)
    config = Config(llm=LLMConfig(model="test-reader", reader_concurrency=2))
    cold_out, warm_out = tmp_path / "cold", tmp_path / "warm"
    cold_out.mkdir()
    warm_out.mkdir()
    cold = StructureTextJudge(config, cold_out, cache_path=tmp_path / "judgments.sqlite")
    pictures = [f"asset-{i}" for i in range(36)]

    def read(judge):
        return judge_standing(
            judge,
            pictures=pictures,
            line_of=lambda a: f"People outdoors {a}",
            contract="A month",
            period_label="Spring",
        )

    with llm_metrics.collecting() as counters:
        first = read(cold)
    assert counters.calls == 6
    assert counters.prompt_tokens == 18
    assert maximum == 2
    assert list(first) == pictures
    assert all(score == 2 for score, _why in first.values())
    assert [c["stage"] for c in cold.calls] == [
        f"standing-{block}-{order}" for block in range(1, 4) for order in ("source", "hashed")
    ]
    assert len(list((cold_out / "calls").glob("*.request.private.txt"))) == 6
    warm = StructureTextJudge(
        config.model_copy(update={"llm": config.llm.model_copy(update={"reader_concurrency": 1})}),
        warm_out,
        cache_path=tmp_path / "judgments.sqlite",
    )
    assert read(warm) == first
    assert len(sent) == 6
    assert all(c["cache_hit"] for c in warm.calls)
    assert [c["judgment_key"] for c in cold.calls] == [c["judgment_key"] for c in warm.calls]


def test_cancellation_reaches_workers_before_they_send_a_request(tmp_path, monkeypatch):
    import pytest

    from immich_memories.analysis import editorial_text_gateway as gateway
    from immich_memories.operations.cancellation import PipelineCancelled, cancellation_scope

    sent = []

    async def completion(prompt, _config, **_kwargs):
        sent.append(prompt)
        return '{"weak":{}}'

    # WHY: capture paid provider requests; cancellation must prevent all of them.
    monkeypatch.setattr(gateway, "query_llm", completion)
    judge = StructureTextJudge(Config(llm=LLMConfig(model="test-reader")), tmp_path)
    stopped = False

    def check():
        if stopped:
            raise PipelineCancelled()

    with cancellation_scope(check), pytest.raises(PipelineCancelled):
        stopped = True
        judge_standing(
            judge,
            pictures=[str(i) for i in range(36)],
            line_of=lambda a: a,
            contract="month",
            period_label="spring",
        )
    assert sent == []
    assert judge.calls == []
    assert not list(tmp_path.glob(".reader-*"))


def test_the_endpoint_decides_how_many_jobs_overlap_when_the_config_names_no_number():
    """A model on this machine serves one request at a time; a hosted one is a fleet."""
    from immich_memories.analysis.llm_providers import reader_concurrency

    assert reader_concurrency(LLMConfig()) == 1
    assert reader_concurrency(LLMConfig(base_url="http://localhost:9999/v1")) == 1
    assert reader_concurrency(LLMConfig(base_url="http://192.168.1.40:8080/v1")) == 1
    assert reader_concurrency(LLMConfig(base_url="http://inference:8092/v1")) == 1
    assert reader_concurrency(LLMConfig(base_url="https://api.melious.ai/v1")) == 4
    assert reader_concurrency(LLMConfig(provider="openai")) == 4
    assert (
        reader_concurrency(LLMConfig(base_url="https://api.melious.ai/v1", reader_concurrency=1))
        == 1
    )
    assert reader_concurrency(LLMConfig(reader_concurrency=8)) == 8


def test_one_question_asked_by_two_jobs_at_once_is_paid_for_once(tmp_path, monkeypatch):
    """Serial reading deduplicated through the bank; overlapping reading must too."""
    from immich_memories.analysis import editorial_text_gateway as gateway
    from immich_memories.analysis.editorial_reader_concurrency import run_reader_jobs

    paid = []
    lock = threading.Lock()
    both_asking = threading.Barrier(2, timeout=5)

    async def completion(prompt, _config, **_kwargs):
        with lock:
            paid.append(prompt)
        return '{"weak":{}}'

    # WHY: replace only the remote model; keep the real judgment bank and recorder.
    monkeypatch.setattr(gateway, "query_llm", completion)
    config = Config(llm=LLMConfig(model="test-reader", reader_concurrency=2))
    (tmp_path / "out").mkdir()
    judge = StructureTextJudge(config, tmp_path / "out", cache_path=tmp_path / "judgments.sqlite")

    def ask_the_same_thing(child, _item):
        both_asking.wait()
        return child.ask("duplicate", "one identical question", max_tokens=50)

    answers = run_reader_jobs(judge, ask_the_same_thing, [0, 1])
    assert answers == ['{"weak":{}}', '{"weak":{}}']
    assert paid == ["one identical question"]
    assert sorted(c["cache_hit"] for c in judge.calls) == [False, True]


def test_a_failed_job_keeps_its_artifact_and_its_number_among_the_others(tmp_path, monkeypatch):
    """The parent's own earlier calls keep their numbers and the failure lands after them."""
    from immich_memories.analysis import editorial_text_gateway as gateway
    from immich_memories.analysis.editorial_reader_concurrency import run_reader_jobs

    async def completion(prompt, _config, **_kwargs):
        return "not an object at all" if "unreadable" in prompt else '{"ok": 1}'

    # WHY: replace only the remote model; the recorder and bank are the real ones.
    monkeypatch.setattr(gateway, "query_llm", completion)
    config = Config(llm=LLMConfig(model="test-reader", reader_concurrency=4))
    (tmp_path / "out").mkdir()
    judge = StructureTextJudge(config, tmp_path / "out", cache_path=tmp_path / "judgments.sqlite")
    judge.ask("opening", "a readable question", json_object=True)

    def read(child, item):
        return child.ask(f"page-{item}", f"question {item}", json_object=True)

    with pytest.raises(ValueError):
        run_reader_jobs(judge, read, ["first", "unreadable", "last"])

    numbered = sorted(p.name for p in (tmp_path / "out" / "calls").glob("*"))
    assert [n.split("-", 1)[0] for n in numbered] == sorted(n.split("-", 1)[0] for n in numbered)
    assert [c["stage"] for c in judge.calls] == [
        "opening",
        "page-first",
        "page-unreadable",
        "page-last",
    ]
    assert "03-page-unreadable.failure.private.json" in numbered
    assert judge.calls[2]["response_contract"] == "bounded failure"


def test_every_failing_job_names_its_own_cause(tmp_path, caplog):
    """A group that failed for two reasons used to report one and drop the other."""
    from immich_memories.analysis.editorial_reader_concurrency import run_reader_jobs

    both_running = threading.Barrier(2, timeout=5)

    def fail(_child, item):
        both_running.wait()
        raise RuntimeError(f"job {item} could not read")

    config = Config(llm=LLMConfig(model="test-reader", reader_concurrency=2))
    (tmp_path / "out").mkdir()
    judge = StructureTextJudge(config, tmp_path / "out", cache_path=tmp_path / "judgments.sqlite")
    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError) as caught:
        run_reader_jobs(judge, fail, ["one", "two"])

    assert "job one could not read" in str(caught.value)
    assert any("job two could not read" in note for note in caught.value.__notes__)
    assert "job two could not read" in caplog.text


def test_a_rate_limit_pauses_the_group_and_no_two_callers_wake_together():
    """Four throttled calls used to sleep the identical span and retry in lockstep."""
    import httpx

    from immich_memories.analysis.provider_failure import (
        THROTTLE,
        group_wait,
        provider_failure,
        retry_wait,
    )

    request = httpx.Request("POST", "http://reader.test/v1/chat/completions")
    throttled = provider_failure(
        httpx.HTTPStatusError(
            "Client error '429' - provider said too many requests",
            request=request,
            response=httpx.Response(429, request=request, headers={"retry-after": "10"}),
        ),
        images_attached=False,
    )
    spans = [retry_wait(throttled, 1) for _ in range(20)]
    assert len(set(spans)) == 20
    assert all(10.0 <= span <= 15.0 for span in spans)  # never under what the provider asked

    assert THROTTLE.pause() == 0.0
    assert 9.0 < group_wait(throttled, 10.0) <= 10.0
    assert 9.0 < THROTTLE.pause() <= 10.0  # a call not yet refused waits it out too
