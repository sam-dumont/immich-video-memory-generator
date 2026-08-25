"""A judgement about an identical selection should not be paid for twice.

The refinement loop asks the same question repeatedly: a stabilize round that
changes nothing re-presents the same clips in the same order, and a second run
of the same memory presents them again. In reasoning mode each of those costs
5-10x the latency and 10-20x the tokens of a fast call, and the overnight sweep
measured ~15 minutes a memory spent almost entirely here.
"""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from immich_memories.analysis.selection_review import review_selection

_VERDICT = '{"drop": []}'


async def _always(*_args, **_kwargs):
    return _VERDICT


def _member(asset_id: str) -> SimpleNamespace:
    asset = SimpleNamespace(
        id=asset_id,
        is_favorite=False,
        file_created_at=datetime(2021, 4, 4, 10, tzinfo=UTC),
        exif_info=None,
        people=[],
    )
    return SimpleNamespace(
        clip=SimpleNamespace(asset=asset, llm_description=f"a picture of {asset_id}"),
        start_time=0.0,
        end_time=4.0,
        score=0.5,
        analyzed=True,
    )


def _selection(n: int = 4) -> list:
    return [_member(f"asset-{i}") for i in range(n)]


def _config(model: str = "qwen"):
    from immich_memories.config_models_llm import LLMConfig

    return LLMConfig(model=model, thinking=True)


def test_an_unchanged_selection_is_judged_once(tmp_path) -> None:
    """Acceptance (a): two identical rounds inside one generation, one call."""
    calls: list = []

    async def _answer(prompt, *_args, **_kwargs):
        calls.append(prompt)
        return _VERDICT

    # WHY: the LLM server is the external boundary; counting calls is the point.
    with patch("immich_memories.analysis.llm_query._dispatch", new=_answer):
        review_selection(_selection(), _config(), cache_path=tmp_path / "judgments.db")
        review_selection(_selection(), _config(), cache_path=tmp_path / "judgments.db")

    assert len(calls) == 1, f"an identical selection was judged {len(calls)} times"


def test_a_second_run_of_the_same_memory_judges_nothing(tmp_path) -> None:
    """Acceptance (b): the answer outlives the process that paid for it."""
    db = tmp_path / "judgments.db"

    # WHY: the LLM server is the external boundary; the first run pays.
    with patch("immich_memories.analysis.llm_query._dispatch", new=_always):
        review_selection(_selection(), _config(), cache_path=db)

    calls: list = []

    async def _answer(prompt, *_args, **_kwargs):
        calls.append(prompt)
        return _VERDICT

    # WHY: same boundary; a later run must not reach it at all.
    with patch("immich_memories.analysis.llm_query._dispatch", new=_answer):
        review_selection(_selection(), _config(), cache_path=db)

    assert calls == [], "a second run re-paid for a verdict it already had"


def test_a_different_selection_is_judged_again(tmp_path) -> None:
    """The key is what was asked, so changing the clips has to miss."""
    calls: list = []

    async def _answer(prompt, *_args, **_kwargs):
        calls.append(prompt)
        return _VERDICT

    # WHY: the LLM server is the external boundary.
    with patch("immich_memories.analysis.llm_query._dispatch", new=_answer):
        review_selection(_selection(4), _config(), cache_path=tmp_path / "judgments.db")
        review_selection(_selection(5), _config(), cache_path=tmp_path / "judgments.db")

    assert len(calls) == 2, "a changed selection reused a verdict about a different one"


def test_a_different_model_is_judged_again(tmp_path) -> None:
    """A verdict belongs to the model that gave it."""
    calls: list = []

    async def _answer(prompt, *_args, **_kwargs):
        calls.append(prompt)
        return _VERDICT

    # WHY: the LLM server is the external boundary.
    with patch("immich_memories.analysis.llm_query._dispatch", new=_answer):
        review_selection(
            _selection(),
            _config("qwen"),
            cache_path=tmp_path / "judgments.db",
        )
        review_selection(
            _selection(),
            _config("llama"),
            cache_path=tmp_path / "judgments.db",
        )

    assert len(calls) == 2, "one model's judgement was served for another's question"


def test_a_cache_that_cannot_be_opened_costs_calls_not_the_run(tmp_path) -> None:
    """Losing the cache is a cost, never a failure — the project's rule."""
    unwritable = tmp_path / "no-such-dir" / "judgments.db"

    # WHY: the LLM server is the external boundary.
    with patch("immich_memories.analysis.llm_query._dispatch", new=_always):
        drops = review_selection(_selection(), _config(), cache_path=unwritable).drops

    assert drops == []


def test_the_pipeline_keeps_its_verdicts_beside_the_other_caches(tmp_path) -> None:
    """Wired to the configured cache directory, not to a temp dir or the cwd."""
    from unittest.mock import MagicMock

    from immich_memories.analysis.selection_quality import SelectionQuality
    from immich_memories.config_loader import Config

    config = Config(cache={"directory": str(tmp_path / "cache")})
    quality = SelectionQuality(
        config=MagicMock(),
        app_config=config,
        analyzer=MagicMock(),
        refiner=MagicMock(),
        tracker=MagicMock(),
        client=MagicMock(),
        provider_circuit=MagicMock(),
    )

    assert quality._verdicts.parent == config.cache.cache_path


def test_without_a_cache_path_nothing_is_remembered(tmp_path) -> None:
    """The default stays uncached, so no caller gets caching by accident."""
    calls: list = []

    async def _answer(prompt, *_args, **_kwargs):
        calls.append(prompt)
        return _VERDICT

    # WHY: the LLM server is the external boundary.
    with patch("immich_memories.analysis.llm_query._dispatch", new=_answer):
        review_selection(_selection(), _config())
        review_selection(_selection(), _config())

    assert len(calls) == 2


def test_visual_identity_changes_for_each_piece_of_visual_evidence() -> None:
    from immich_memories.cache.judgment_cache import VisualJudgmentIdentity

    base = VisualJudgmentIdentity(
        page_bytes=(b"first", b"second"),
        ordered_input_ids=("a", "b"),
        ordered_group_ids=("g",),
        annotations=("known place",),
        model="vision-a",
        thinking=True,
        image_detail="low",
        pass_name="cull",  # noqa: S106 - test-only pass identity
        prompt_version="p1",
        schema_version="s1",
        render_version="r1",
        layout_versions=("l1", "l1"),
        upstream_material=("insight-v1",),
        request_limits=("pages=1",),
        continuation_identity=(1, 1),
    )

    assert base.key() != replace(base, page_bytes=(b"changed", b"second")).key()
    assert base.key() != replace(base, page_bytes=(b"second", b"first")).key()
    assert base.key() != replace(base, annotations=("other place",)).key()
    assert base.key() != replace(base, model="vision-b").key()
    assert base.key() != replace(base, prompt_version="p2").key()
    assert base.key() != replace(base, schema_version="s2").key()
    assert base.key() != replace(base, render_version="r2").key()
    assert base.key() != replace(base, layout_versions=("l2", "l1")).key()
    assert base.key() != replace(base, upstream_material=("insight-v2",)).key()


def test_visual_cache_keeps_original_provenance_when_reused(tmp_path) -> None:
    from immich_memories.cache.judgment_cache import VisualJudgmentCache

    cache = VisualJudgmentCache(tmp_path / "judgments.db")
    cache.remember("visual-key", "raw answer", '{"request": "original"}')

    assert cache.answer_for("visual-key") == ("raw answer", '{"request": "original"}')
