"""CLI handoff of the finished editorial plan into generation."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from immich_memories.analysis.editorial_planner import EditorialSelection
from immich_memories.analysis.smart_pipeline import PipelineResult
from immich_memories.config_loader import Config
from immich_memories.timeperiod import DateRange
from tests.conftest import make_clip


def test_cli_passes_exact_selected_carriers_and_editorial_decisions_to_generation(
    tmp_path,
) -> None:
    from immich_memories.cli._pipeline_runner import run_pipeline_and_generate

    clip = make_clip("editorial-cli", duration=5.0)
    selected_clips = [clip]
    decisions = (EditorialSelection(asset_id=clip.asset.id, render_mode="motion"),)
    result = PipelineResult(
        selected_clips=selected_clips,
        clip_segments={clip.asset.id: (0.0, 4.0)},
        errors=[],
        editorial_selections=decisions,
    )
    config = Config(
        cache={
            "database": str(tmp_path / "analysis.db"),
            "directory": str(tmp_path / "cache"),
        }
    )
    output_path = tmp_path / "memory.mp4"

    # WHY: replaces clip conversion, the selection pipeline, and the render step reached here.
    with (
        # WHY: forces the exact `clip` object through so identity checks on params.clips hold.
        patch("immich_memories.generate.assets_to_clips", return_value=[clip]),
        # WHY: the pipeline is a stand-in configured to hand back the pre-built result.
        patch("immich_memories.analysis.editorial_runtime.build_smart_pipeline") as pipeline_type,
        # WHY: replaces the FFmpeg render; the test inspects the params it was called with.
        patch("immich_memories.generate.generate_memory", return_value=output_path) as generate,
    ):
        pipeline_type.return_value.run_editorial_source.return_value = (selected_clips, result)
        pipeline_type.return_value.last_deep_analysis_count = 0
        actual, _, _ = run_pipeline_and_generate(
            assets=[clip.asset],
            client=MagicMock(),
            config=config,
            progress=MagicMock(),
            duration=60.0,
            transition="cut",
            music=None,
            no_music=True,
            output_path=output_path,
            memory_type="year_in_review",
            person_names=[],
            date_range=DateRange(
                start=datetime(2026, 1, 1),
                end=datetime(2026, 12, 31, 23, 59, 59),
            ),
            upload_to_immich=False,
            album=None,
        )

    params = generate.call_args.args[0]
    assert actual == output_path
    assert params.clips is selected_clips
    assert params.clips[0] is clip
    assert params.editorial_selections is decisions


def test_cli_recipe_identity_includes_ordered_render_mode_and_frame() -> None:
    from immich_memories.cli._pipeline_runner import _name_after_recipe

    first = make_clip("recipe-first", duration=5.0)
    second = make_clip("recipe-second", duration=5.0)
    clips = [first, second]
    segments = {first.asset.id: (0.0, 4.0), second.asset.id: (0.0, 4.0)}
    base = (
        EditorialSelection(
            asset_id=first.asset.id,
            render_mode="still",
            render_frame_seconds=1.25,
        ),
        EditorialSelection(asset_id=second.asset.id, render_mode="motion"),
    )
    common = {
        "selected_clips": clips,
        "clip_segments": segments,
        "memory_type": "year_in_review",
        "date_range": DateRange(
            start=datetime(2026, 1, 1),
            end=datetime(2026, 12, 31, 23, 59, 59),
        ),
        "target_duration": 60.0,
    }

    original = _name_after_recipe(
        output_path=Path("memory.mp4"),
        editorial_selections=base,
        **common,
    )
    changed_mode = _name_after_recipe(
        output_path=Path("memory.mp4"),
        editorial_selections=(base[0], EditorialSelection(asset_id=second.asset.id)),
        **common,
    )
    changed_frame = _name_after_recipe(
        output_path=Path("memory.mp4"),
        editorial_selections=(
            EditorialSelection(
                asset_id=first.asset.id,
                render_mode="still",
                render_frame_seconds=1.5,
            ),
            base[1],
        ),
        **common,
    )
    changed_order = _name_after_recipe(
        output_path=Path("memory.mp4"),
        editorial_selections=tuple(reversed(base)),
        **common,
    )

    assert len({original, changed_mode, changed_frame, changed_order}) == 4
