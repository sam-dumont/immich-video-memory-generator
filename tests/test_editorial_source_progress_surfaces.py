"""Observed CLI/UI editorial progress without invented completion counts or ETA."""

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from tests.test_editorial_source_route_surfaces import (
    _WINDOW,
    _config,
    _finished_selection,
    _source_pipeline,
)


def _status(label="Editing the memory", status="running"):
    return {
        "indeterminate": True,
        "status": status,
        "phase_label": label,
        "current_phase": label,
        "started_at": 100.0,
        "elapsed_seconds": 8.25,
        "elapsed": "8s",
    }


@pytest.mark.parametrize("failed", [False, True])
def test_cli_real_display_enters_indeterminate_then_reports_actual_terminal_stage(tmp_path, failed):
    from immich_memories.cli._live_display import LiveDisplay
    from immich_memories.cli._pipeline_runner import run_pipeline_and_generate

    display = LiveDisplay(Console(file=StringIO(), force_terminal=False))
    result = _finished_selection()
    pipeline = _source_pipeline(result)
    observed = []

    def source(_sources, *, progress_callback, **_kwargs):
        for payload in [
            _status("Reading dates, places and people"),
            _status(),
            _status(
                "Editorial selection failed" if failed else "Editorial selection complete",
                "failed" if failed else "complete",
            ),
        ]:
            progress_callback(payload)
            task_id = display._active_task_id
            task = display._progress.tasks[task_id]
            observed.append(
                (payload["status"], display._tasks[task_id].total, task.total, task.description)
            )
        if failed:
            raise RuntimeError("editorial evidence unavailable")
        return result.selected_clips, result

    pipeline.run_editorial_source.side_effect = source
    # WHY: stubs the pipeline builder and renderer so this no-render run never reaches FFmpeg.
    with (
        # WHY: the collaborator under inspection; run_editorial_source calls are asserted below.
        patch(
            "immich_memories.analysis.editorial_runtime.build_smart_pipeline", return_value=pipeline
        ),
        patch(
            "immich_memories.generate.generate_memory",
            side_effect=AssertionError("no render requested"),
        ),
    ):

        def run():
            return run_pipeline_and_generate(
                assets=[result.selected_clips[0].asset, result.selected_clips[2].asset],
                client=MagicMock(),
                config=_config(tmp_path),
                progress=display,
                duration=60.0,
                transition="cut",
                music=None,
                no_music=True,
                output_path=tmp_path / "memory.mp4",
                memory_type="monthly_highlights",
                person_names=[],
                date_range=_WINDOW,
                upload_to_immich=False,
                album=None,
                no_render=True,
            )

        if failed:
            with pytest.raises(RuntimeError, match="editorial evidence unavailable"):
                run()
        else:
            run()
    # The route is proven by what the runner consumed: the editorial source ran
    # once over the raw assets, and the result it carried says so. The mock
    # pipeline raises on run_analysis/run_planning_analysis/run_selection.
    assert pipeline.run_editorial_source.call_count == 1
    assert pipeline.run_editorial_source.call_args.args[0] == [
        result.selected_clips[0].asset,
        result.selected_clips[2].asset,
    ]
    assert result.stats["selection_route"] == "editorial-source"
    assert observed[:2] == [
        ("running", None, None, "Reading dates, places and people"),
        ("running", None, None, "Editing the memory"),
    ]
    expected = (
        ("failed", None, None, "Editorial selection failed")
        if failed
        else ("complete", 100, 100, "Editorial selection complete")
    )
    assert observed[2] == expected


@pytest.mark.parametrize("status", ["running", "complete", "failed"])
def test_ui_callback_keeps_the_stage_and_elapsed_and_marks_the_display_indeterminate(status):
    from immich_memories.ui.pages.clip_pipeline import _make_progress_callback

    state: dict = {}
    _make_progress_callback(state)(_status(status=status))
    assert state["phase_label"] == "Editing the memory"
    assert state["status"] == status and state["indeterminate"] is True
    assert state["started_at"] == 100.0 and state["elapsed"] == "8s"


def test_ui_progress_callback_preserves_explicit_cancellation_contract():
    from immich_memories.ui.pages.clip_pipeline import PipelineCancelled, _make_progress_callback

    with pytest.raises(PipelineCancelled):
        _make_progress_callback({}, lambda: True)(_status())


def test_cached_asset_checks_do_not_repeat_logs_or_ui_writes_and_still_cancel(caplog):
    import logging

    from immich_memories.cli._live_display import QuietDisplay
    from immich_memories.ui.pages.clip_pipeline import PipelineCancelled, _make_progress_callback

    class CountingState(dict):
        writes = 0

        def __setitem__(self, key, value):
            self.writes += 1
            super().__setitem__(key, value)

    state = CountingState()
    cancel = {"requested": False}
    update_ui = _make_progress_callback(state, lambda: cancel["requested"])
    display = QuietDisplay()
    with caplog.at_level(logging.INFO, logger="immich_memories.progress"):
        task = display.add_task("Preparing cached previews", total=None)
        update_ui(_status("Preparing cached previews"))
        initial_writes = state.writes
        for _ in range(1500):
            display.update(task, description="Preparing cached previews")
            update_ui(_status("Preparing cached previews"))
        assert state.writes == initial_writes
        assert caplog.messages.count("Preparing cached previews") == 1
        display.update(task, description="Reading the period")
        update_ui(_status("Reading the period"))
        assert state["phase_label"] == "Reading the period"
        assert caplog.messages.count("Reading the period") == 1
        update_ui({**_status("Reading the period"), "current_index": 4, "total_items": 9})
        assert (state["current_index"], state["total_items"]) == (4, 9)
        update_ui(_status("Reading the period", status="complete"))
        assert state["status"] == "complete"
        cancel["requested"] = True
        with pytest.raises(PipelineCancelled):
            update_ui(_status("Reading the period"))


def test_identical_interactive_description_does_not_refresh_display():
    from immich_memories.cli._live_display import LiveDisplay

    display = LiveDisplay(Console(file=StringIO(), force_terminal=False))
    task = display.add_task("Reading the period", total=None)
    with patch.object(display, "_refresh") as refresh:
        for _ in range(1500):
            display.update(task, description="Reading the period")
        refresh.assert_not_called()
        display.update(task, description="Editorial selection complete")
        refresh.assert_called_once()
