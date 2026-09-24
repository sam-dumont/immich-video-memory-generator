"""Actual source/card/native-planner route, with only external judgments replaced."""

import json
from datetime import timedelta
from pathlib import Path

import pytest

from immich_memories.analysis.editorial_preparation import PreparationResult
from immich_memories.analysis.editorial_runtime import (
    EditorialRunContext,
    build_editorial_planner,
)
from immich_memories.analysis.editorial_runtime_evidence import EditorialInputsRequired
from immich_memories.analysis.editorial_runtime_ports import EditorialRuntimePorts
from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
from immich_memories.analysis.selection_trace import Trace
from immich_memories.analysis.smart_pipeline import PipelineConfig, SmartPipeline
from immich_memories.config_loader import Config
from tests.editorial_story_fixtures import ControlledStoryJudge
from tests.test_editorial_duration_planner_integration import semantic_plan
from tests.test_editorial_runtime import _create_annotation_store, _window
from tests.test_editorial_source_route import photo


def refuse_pictures(monkeypatch) -> list[int]:
    """Every model request the run makes passes this one dispatch; a picture in one fails.

    Pictures are read once, at ingest. A film-time request carrying one is a defect whatever
    stage sent it, so the guard sits where every stage's request meets the wire. The list
    holds how many pictures each request carried.
    """
    from immich_memories.analysis import llm_query

    sent: list[int] = []
    real = llm_query._dispatch

    async def dispatch(prompt, llm_config, temperature, max_tokens, timeout, thinking, images, *a):
        sent.append(len(images))
        if images:
            pytest.fail(f"a film-time request sent {len(images)} picture(s) to the reader")
        return await real(
            prompt, llm_config, temperature, max_tokens, timeout, thinking, images, *a
        )

    # WHY: the provider wire; this sees what would leave the machine and refuses pictures.
    monkeypatch.setattr(llm_query, "_dispatch", dispatch)
    return sent


def setup_runtime(
    tmp_path,
    monkeypatch,
    *,
    missing_store=False,
    default_structure=None,
    thumbnail_cache=None,
    fetch_preview=None,
):
    window = _window(2020, 5, 2)
    sources = [
        photo(f"p-{n:02}", at=window.start + timedelta(hours=9, minutes=n * 10)) for n in range(22)
    ]
    store = tmp_path / "annotations.sqlite"
    _create_annotation_store(
        store,
        {}
        if missing_store
        else {a.id: "A clothed person carries furniture during a move." for a in sources},
    )
    config = Config(
        llm={"model": "text-model", "base_url": "http://localhost:9999/v1"},
        editorial={
            "enabled": True,
            "annotation_database": str(store),
            "description_model": "student-v1",
            # This file tests the whole-film model planner; the one-window polish route
            # reads on demand and has its own tests (test_editorial_thin_account_fallback).
            "thin_model_layer": False,
        },
        analysis={"min_source_short_side": 0},
    )
    context = EditorialRunContext(
        "moving", "A move", "monthly_highlights", (window,), 60, tmp_path / "artifacts"
    )
    calls = {"episode": [], "period": [], "acquire": []}
    judgments = {}
    captures = []
    image_calls = refuse_pictures(monkeypatch)
    warm = [False]

    def episode(prompt):
        assert not warm[0], "warm episode request escaped exact bank"
        calls["episode"].append(prompt)
        return json.dumps(
            {
                "schema_version": "episode-reading-text-v1",
                "episodes": [
                    {
                        "episode": 1,
                        "what_happened": "People move furniture into a home.",
                        "representatives": [{"asset": 1, "reason": "Shows the move."}],
                        "cull": [],
                    }
                ],
            }
        )

    def acquire(_client, scope):
        calls["acquire"].append(scope)
        return sources

    def effects(source):
        captures.append(source)
        return StructurePlannerPorts(
            judge=ControlledStoryJudge(judgments, require_hits=warm[0]),
            thumbnail_hash=lambda _: None,
        )

    def build():
        options = {"structure_ports_factory": effects}
        if default_structure is not None:
            options = {"structure_planner": default_structure, "fetch_preview": fetch_preview}
        return build_editorial_planner(
            client=object(),
            config=config,
            thumbnail_cache=thumbnail_cache or object(),
            context=context,
            ports=EditorialRuntimePorts(
                load_people=lambda: {},
                fetch_full_source=acquire,
                # This fixture starts with synthetic student-v1 annotations;
                # producer acquisition has separate integration coverage.
                prepare_annotations=lambda *, assets, **_: PreparationResult(
                    requested=len(assets), missing_by_producer={}, failures={}
                ),
                episode_requester_factory=lambda _: episode,
                **options,
            ),
        )

    return sources, config, build, calls, captures, image_calls, warm


def test_full_runtime_story_first_and_exact_warm_without_legacy_calls(
    tmp_path, monkeypatch, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
):
    sources, config, build, calls, captures, image_calls, warm = setup_runtime(
        tmp_path, monkeypatch
    )
    native_plans = []

    def run():
        planner = build()
        pipeline = SmartPipeline(config=PipelineConfig(), planner=planner)

        candidates, result = pipeline.run_editorial_source(sources)
        native_plans.append(planner._backend.last_structure_result.plan)
        return candidates, result

    cold_candidates, cold = run()
    assert len(cold_candidates) == 22
    assert all(row.analyzed is False and row.score == 0 for row in cold_candidates)
    assert 0 < len(cold.selected_clips) <= 15
    assert native_plans[0]["schema_version"] == "structure-plan-v88-story-first"
    assert "story" in native_plans[0]
    assert all(source.audience == "family" for source in captures)
    assert cold.clip_segments == {
        row["asset_id"]: (row.get("start_time", 0.0), row.get("end_time", row["seconds"]))
        for row in native_plans[0]["carriers"]
    }
    assert cold.stats["selection_route"] == "editorial-source"
    assert cold.stats["legacy_deep_analysis_count"] == cold.stats["total_analyzed"] == 0
    assert cold.stats["source_candidate_count"] == 22
    assert cold.stats["editorial_duration_realization"] == native_plans[0]["duration_realization"]
    attempt_status = json.loads(
        (Path(cold.stats["editorial_attempt_directory"]) / "status.private.json").read_text()
    )
    assert attempt_status["duration_realization"] == native_plans[0]["duration_realization"]
    assert attempt_status["calls_by_stage"] == native_plans[0]["calls_by_stage"]
    assert not any(image_calls), "no film-time request carries a picture"
    assert "picture_facts" not in native_plans[0]
    warm[0] = True
    _, replay = run()
    assert semantic_plan(native_plans[1]) == semantic_plan(native_plans[0])
    assert replay.editorial_selections == cold.editorial_selections
    assert replay.clip_segments == cold.clip_segments
    assert not any(image_calls)
    assert len(calls["episode"]) == 1
    assert not calls["period"]
    assert all(source.allow_live_motion for source in captures)


def test_requested_subset_uses_full_canonical_context_without_widening_selection(
    tmp_path, monkeypatch
):
    sources, _config, build, calls, captures, _images, _warm = setup_runtime(tmp_path, monkeypatch)
    requested = sources[::2]
    result = build().plan_source(requested, trace=Trace(), include_live_photos=False)
    assert {row.clip.asset.id for row in result.candidates} == {a.id for a in requested}
    assert set(result.plan.selected_asset_ids).issubset(a.id for a in requested)
    assert set(captures[0].assets) == {a.id for a in sources}
    assert {a for ids in captures[0].moment_asset_ids.values() for a in ids} == {
        a.id for a in requested
    }
    assert not captures[0].allow_live_motion
    assert captures[0].lineage["render_policy"] == {"allow_live_motion": False}
    assert len(calls["acquire"]) == 1


def test_empty_native_cut_does_not_claim_the_library_was_exhausted(tmp_path, monkeypatch):
    from immich_memories.analysis.editorial_structure_contract import StructurePlanningResult
    from immich_memories.operations.editorial_attempt import read_editorial_attempt
    from tests.test_editorial_duration_advisory import SHORTFALL

    realization = SHORTFALL | {"selected_content_seconds": 0.0, "shortfall_seconds": 82.5}

    def no_cut(_source, _ports):
        return StructurePlanningResult(
            {
                "carriers": [],
                "status": "insufficient_material",
                "duration_realization": realization,
            },
            "contract",
            "diagnostic sheet",
            {},
        )

    sources, _config, build, *_ = setup_runtime(tmp_path, monkeypatch, default_structure=no_cut)
    runtime = build()
    result = runtime.plan_source(sources, trace=Trace())
    saved = read_editorial_attempt(runtime.last_attempt_directory)

    assert result.plan.selections == ()
    assert result.duration_realization == realization
    assert saved["status"] == "complete"
    assert saved["outcome"] == "no_selection"
    assert saved["selected_carriers"] == 0
    assert saved["duration_realization"] == realization


def test_unavailable_canonical_evidence_does_not_trigger_legacy_selection(tmp_path, monkeypatch):
    sources, _config, build, calls, _captures, _images, _warm = setup_runtime(tmp_path, monkeypatch)
    planner = build()
    # A missing required table is a real unavailable native fact snapshot.
    import sqlite3

    with sqlite3.connect(tmp_path / "annotations.sqlite") as connection:
        connection.execute("drop table descriptions")
    with pytest.raises(
        EditorialInputsRequired, match="unreadable annotation lines.*fact store unavailable"
    ):
        planner.plan_source(sources, trace=Trace())
    assert planner._backend.last_structure_result is None
    assert not calls["episode"] and not calls["period"]


def test_the_picture_guard_refuses_a_request_that_carries_one(monkeypatch):
    """The guard the film tests stand on trips on a picture, so their silence means something."""
    import asyncio

    from immich_memories.analysis.llm_query import query_llm
    from immich_memories.config_models_llm import LLMConfig

    sent = refuse_pictures(monkeypatch)
    config = LLMConfig(model="reader", base_url="http://127.0.0.1:9/v1")

    with pytest.raises(pytest.fail.Exception, match="sent 1 picture"):
        asyncio.run(query_llm("Describe this.", config, images=(b"\xff\xd8\xff",)))
    assert sent == [1]
