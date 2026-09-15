"""A short completed selection stays successful and reports its recorded gap."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.analysis.editorial_duration_advisory import editorial_duration_warning
from immich_memories.analysis.editorial_source_route import EditorialSourcePlan
from immich_memories.operations.editorial_attempt import EditorialAttempt, read_editorial_attempt
from tests.test_editorial_source_route_surfaces import (
    _config,
    _finished_selection,
    _source_pipeline,
)

SHORTFALL = {
    "requested_seconds": 90.0,
    "content_budget_seconds": 82.5,
    "selected_content_seconds": 32.0,
    "shortfall_seconds": 50.5,
    "status": "editorial_shortfall",
}
WARNING = (
    "Selected 32.0s of pictures and video for a 90.0s memory "
    "(82.5s available for content). More usable material may remain."
)


def test_recorded_shortfall_is_reported_without_reclassifying_available_material():
    assert editorial_duration_warning(SHORTFALL) == WARNING
    # Outcome is the planner's decision; the display must not run a new threshold.
    assert editorial_duration_warning(SHORTFALL | {"status": "near_target"}) is None
    assert editorial_duration_warning(None) is None
    assert editorial_duration_warning({}) is None
    assert (
        editorial_duration_warning(SHORTFALL | {"selected_content_seconds": float("nan")}) is None
    )


def test_search_limit_keeps_its_recorded_cause():
    message = editorial_duration_warning(SHORTFALL | {"status": "search_limited"})
    assert message == "Selection stopped early. " + WARNING


def test_shortfall_attempt_remains_complete_and_retains_original_duration_record(tmp_path):
    with EditorialAttempt(tmp_path, request={"target_seconds": 90}) as attempt:
        attempt.complete(selected=8, outcome="selected", duration_realization=SHORTFALL)
    saved = read_editorial_attempt(attempt.directory)
    assert saved["status"] == "complete"
    assert saved["outcome"] == "selected"
    assert saved["selected_carriers"] == 8
    assert saved["duration_realization"] == SHORTFALL
    from immich_memories.analysis.editorial_planner import EditorialPlan

    assert EditorialSourcePlan((), EditorialPlan()).duration_realization is None


@pytest.mark.parametrize("no_render", [False, True])
@pytest.mark.parametrize("status", ["editorial_shortfall", "near_target"])
def test_cli_reports_recorded_shortfall_before_both_render_boundaries(tmp_path, no_render, status):
    from immich_memories.cli._pipeline_runner import run_pipeline_and_generate
    from immich_memories.timeperiod import DateRange

    result = _finished_selection()
    record = SHORTFALL | {"status": status}
    result.stats["editorial_duration_realization"] = record
    pipeline = _source_pipeline(result)
    output = tmp_path / "people.mp4"
    window = DateRange(
        datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 6, 30, 23, 59, 59, tzinfo=UTC)
    )
    # WHY: swaps the selection pipeline, render step, and warning sink this call reaches.
    with (
        # WHY: the selection pipeline is a stand-in returning the pre-built shortfall result.
        patch(
            "immich_memories.analysis.editorial_runtime.build_smart_pipeline", return_value=pipeline
        ),
        patch("immich_memories.generate.generate_memory", return_value=output) as generate,
        patch("immich_memories.cli._pipeline_runner.print_warning") as warning,
    ):
        run_pipeline_and_generate(
            assets=[clip.asset for clip in result.selected_clips],
            include_photos=True,
            use_live_photos=True,
            client=MagicMock(),
            config=_config(tmp_path),
            progress=MagicMock(),
            duration=90.0,
            transition="cut",
            music=None,
            no_music=True,
            output_path=output,
            memory_type="multi_person",
            person_names=["Adult", "Child"],
            date_range=window,
            date_ranges=[window],
            upload_to_immich=False,
            album=None,
            no_render=no_render,
        )
    if status == "editorial_shortfall":
        warning.assert_called_once_with(WARNING)
    else:
        warning.assert_not_called()
    if no_render:
        generate.assert_not_called()
    else:
        assert generate.call_args.args[0].editorial_duration_realization == record
        assert generate.call_args.args[0].editorial_selections == result.editorial_selections


@pytest.mark.parametrize("status", ["editorial_shortfall", "near_target"])
def test_ui_completion_advisory_and_generation_handoff_share_recorded_outcome(tmp_path, status):
    from immich_memories.ui.pages import clip_pipeline
    from immich_memories.ui.pages._step4_generate import _build_generation_params
    from immich_memories.ui.state import AppState

    result = _finished_selection()
    record = SHORTFALL | {"status": status}
    result.stats["editorial_duration_realization"] = record
    state = AppState(
        config=_config(tmp_path),
        memory_type="multi_person",
        target_duration=1.5,
        pipeline_result={"stats": result.stats},
        pipeline_selected_clips=result.selected_clips,
        editorial_selections=result.editorial_selections,
        clip_segments=result.clip_segments,
    )
    with patch.object(clip_pipeline, "ui") as ui:
        clip_pipeline.render_pipeline_summary(state.pipeline_result)
    texts = [call.args[0] for call in ui.label.call_args_list]
    assert any("Pipeline complete" in str(text) for text in texts)
    assert (WARNING in texts) is (status == "editorial_shortfall")
    # WHY: avoids opening a real Immich connection; the test only checks the handed-off params.
    with patch("immich_memories.api.immich.SyncImmichClient"):
        params = _build_generation_params(state, result.selected_clips, tmp_path / "memory.mp4")
    assert params.editorial_duration_realization == record
    assert params.clips == result.selected_clips


def test_reopening_completed_run_keeps_shortfall_with_other_warnings(tmp_path):
    from types import SimpleNamespace

    from immich_memories.ui.pages._step4_generate import _restore_completed_ui_state

    state = SimpleNamespace()
    completed = SimpleNamespace(
        status="completed",
        output_path=str(tmp_path / "memory.mp4"),
        warnings=[WARNING, "Music was unavailable."],
        output_duration_seconds=0.0,
        delivery_status="not_requested",
    )
    _restore_completed_ui_state(state, completed)
    assert state.generation_warning == WARNING + "\nMusic was unavailable."


@pytest.mark.parametrize("status", ["near_target", "editorial_shortfall"])
def test_direct_generation_persists_advisory_without_changing_completed_artifact(
    tmp_path, monkeypatch, status
):
    import json
    import subprocess

    from immich_memories import generate as generate_module
    from immich_memories.generate import GenerationParams, generate_memory
    from immich_memories.processing import output_contract
    from immich_memories.processing.assembly_config import AssemblyClip, AssemblySettings
    from immich_memories.tracking import RunDatabase, RunTracker
    from tests.conftest import make_clip
    from tests.test_generate import _h264_output_plan

    config = _config(tmp_path)
    tracker = RunTracker("shortfall-test", db_path=config.cache.database_path, capture_system=False)
    clips = [make_clip(f"clip-{number}", duration=4.0) for number in range(8)]
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    assembly = [AssemblyClip(path=source, duration=4.0, asset_id=clip.asset.id) for clip in clips]
    params = GenerationParams(
        clips=clips,
        output_path=tmp_path / "memory.mp4",
        config=config,
        no_music=True,
        target_duration_seconds=90.0,
        editorial_duration_realization=SHORTFALL | {"status": status},
    )

    class Assembler:
        def assemble_with_titles(self, selected, output_path, _progress, **_kwargs):
            assert [clip.asset_id for clip in selected] == [clip.asset.id for clip in clips]
            output_path.write_bytes(b"validated-memory")
            return output_path

    payload = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "color_transfer": "bt709",
                "color_primaries": "bt709",
                "width": 1920,
                "height": 1080,
                "nb_read_frames": "1239",
            }
        ],
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "41.3", "size": "4096"},
    }

    def probe(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(output_contract.subprocess, "run", probe)
    # WHY: replaces the download cache and render internals `generate_memory` reaches directly.
    with (
        # WHY: VideoDownloadCache would otherwise fetch real Immich video bytes to assemble.
        patch("immich_memories.cache.video_cache.VideoDownloadCache", return_value=MagicMock()),
        patch.object(generate_module, "_extract_clips", return_value=assembly),
        patch.object(
            generate_module,
            "_build_assembly_settings",
            return_value=AssemblySettings(encoding_plan=_h264_output_plan()),
        ),
        patch.object(generate_module, "_create_assembler", return_value=Assembler()),
        patch.object(generate_module, "_cleanup_temp_clips"),
    ):
        path = generate_memory(params, run_tracker=tracker)

    saved = RunDatabase(config.cache.database_path).get_run(tracker.run_id)
    assert saved.status == "completed"
    assert saved.output_duration_seconds == 41.3
    assert saved.target_duration_seconds == 90
    assert saved.clips_selected == 8
    assert saved.warnings == ([WARNING] if status == "editorial_shortfall" else [])
    assert path.read_bytes() == b"validated-memory"
