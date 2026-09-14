"""Independent reads overlap without changing prompts, votes or their persistent bank."""

import asyncio
import threading

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


def test_event_inventories_overlap_without_changing_the_full_cut(tmp_path, monkeypatch):
    from immich_memories.analysis import editorial_text_gateway as gateway
    from tests.test_editorial_duration_planner_integration import run, semantic_plan
    from tests.test_editorial_story_first_planner import StoryJudge, make_source

    scripted = StoryJudge()
    source = make_source(tmp_path / "reference", pictures=20)
    reference = run(source, scripted)
    answers = {key[0]: value for key, value in scripted.bank.items()}
    active = maximum = 0
    lock = threading.Lock()
    overlap = threading.Event()

    async def completion(prompt, _config, **_kwargs):
        nonlocal active, maximum
        if prompt.startswith("Inventory distinct depicted moments"):
            with lock:
                active += 1
                maximum = max(maximum, active)
                if active == 2:
                    overlap.set()
            overlap.wait(timeout=0.15)
            await asyncio.sleep(0.01)
            with lock:
                active -= 1
        return answers[prompt]

    # WHY: replay exact scripted provider answers through the production recorder and bank.
    monkeypatch.setattr(gateway, "query_llm", completion)
    source = make_source(tmp_path / "concurrent", pictures=20)
    source.config.llm.model = "test-reader"
    source.config.llm.reader_concurrency = 2
    source.artifact_dir.mkdir(parents=True, exist_ok=True)
    judge = StructureTextJudge(
        source.config, source.artifact_dir, cache_path=tmp_path / "judgments.sqlite"
    )
    result = run(source, judge)
    assert maximum == 2
    assert semantic_plan(result) == semantic_plan(reference)
    assert [c["stage"] for c in judge.calls] == [c["stage"] for c in scripted.calls]


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
