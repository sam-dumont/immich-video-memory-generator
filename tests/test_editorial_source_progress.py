"""Real elapsed time and observational source-stage forwarding, without inference.

The cases entering the route through the public ``SmartPipeline.run``, and the one
that builds a real runtime, wait for the slice that ports the editorial runtime.
"""

from types import SimpleNamespace

import pytest

from immich_memories.analysis.editorial_planner import EditorialPlan, EditorialSelection
from immich_memories.analysis.editorial_source_route import EditorialSourcePlan
from immich_memories.analysis.progress import PipelinePhase
from immich_memories.analysis.smart_pipeline import PipelineConfig, SmartPipeline
from immich_memories.config_loader import Config
from tests.test_editorial_source_route import demand, photo


def pipeline_for(planner, client, cache, thumbnails):
    config = Config()
    return SmartPipeline(
        client=client,
        analysis_cache=cache,
        thumbnail_cache=thumbnails,
        config=PipelineConfig(),
        analysis_config=config.analysis,
        app_config=config,
        planner=planner,
    )


@pytest.mark.parametrize("failure", [False, True])
def test_source_timer_includes_work_and_finishes_with_truthful_outcome(
    monkeypatch, mock_immich_client, mock_analysis_cache, mock_thumbnail_cache, failure
):
    clock = [100.0]
    monkeypatch.setattr("immich_memories.analysis.progress.time.time", lambda: clock[0])
    sources = [photo("picture")]
    _, rows = demand(sources)
    events = []

    def plan_source(_sources, *, on_stage, **_kwargs):
        assert events[0]["elapsed_seconds"] == 0
        assert events[0]["status"] == "running"
        clock[0] += 7.5
        on_stage("Editing the memory")
        clock[0] += 11.25
        if failure:
            raise RuntimeError("required source evidence unavailable")
        return EditorialSourcePlan(
            rows, EditorialPlan((EditorialSelection("picture", render_mode="still"),))
        )

    pipeline = pipeline_for(
        SimpleNamespace(plan_source=plan_source),
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
    )
    if failure:
        with pytest.raises(RuntimeError, match="required source evidence unavailable"):
            pipeline.run_editorial_source(sources, events.append)
    else:
        _, result = pipeline.run_editorial_source(sources, events.append)
        assert result.stats["elapsed_seconds"] == 18.75
        assert result.stats["total_analyzed"] == 0
        assert result.stats["source_candidate_count"] == 1
        assert [clip.asset.id for clip in result.selected_clips] == ["picture"]
    assert [event["elapsed_seconds"] for event in events] == [0, 7.5, 18.75]
    assert events[-1]["status"] == ("failed" if failure else "complete")
    assert all(event["indeterminate"] is True for event in events)
    assert all(event["started_at"] == 100 for event in events)
    assert not any(
        key in event
        for event in events
        for key in ("progress_fraction", "total_items", "completed_count", "phase_number", "eta")
    )
    assert pipeline.tracker.progress.start_time == 100
    assert pipeline.tracker.progress.phase is (
        PipelinePhase.NOT_STARTED if failure else PipelinePhase.COMPLETE
    )
    if failure:
        assert pipeline.tracker.progress.operational_event is None


def test_display_callback_failure_does_not_change_selection(
    mock_immich_client, mock_analysis_cache, mock_thumbnail_cache
):
    sources = [photo("picture")]
    _, rows = demand(sources)
    expected = EditorialPlan((EditorialSelection("picture", render_mode="still"),))

    def plan_source(_sources, *, on_stage, **_kwargs):
        on_stage("Reading event evidence")
        return EditorialSourcePlan(rows, expected)

    def broken_display(_event):
        raise ValueError("display disconnected")

    pipeline = pipeline_for(
        SimpleNamespace(plan_source=plan_source),
        mock_immich_client,
        mock_analysis_cache,
        mock_thumbnail_cache,
    )
    _, result = pipeline.run_editorial_source(sources, broken_display)
    assert result.editorial_selections == expected.selections
