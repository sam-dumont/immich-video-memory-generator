"""Auto orientation follows the cut, including a no-render preview."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.analysis.smart_pipeline import PipelineResult
from immich_memories.cli import main
from immich_memories.cli._pipeline_runner import run_pipeline_and_generate
from immich_memories.config_loader import Config
from immich_memories.timeperiod import DateRange
from tests.conftest import make_clip


def test_cli_offers_auto_but_still_defaults_to_landscape():
    option = next(p for p in main.commands["generate"].params if p.name == "orientation")
    assert "auto" in option.type.choices
    assert option.default == "landscape"


@pytest.mark.parametrize("no_render", [False, True])
@pytest.mark.parametrize(
    "orientation,width,height", [("auto", 1080, 1920), ("landscape", 1920, 1080)]
)
def test_canvas_uses_selected_portrait_in_a_landscape_pool(
    tmp_path, no_render, orientation, width, height
):
    selected = [make_clip("kept-portrait", duration=5, width=1080, height=1920)]
    candidates = [
        *selected,
        *(make_clip(f"dropped-{i}", width=1920, height=1080) for i in range(3)),
    ]
    result = PipelineResult(
        selected_clips=selected, clip_segments={"kept-portrait": (0, 4)}, errors=[]
    )
    config = Config(
        cache={"database": str(tmp_path / "analysis.db"), "directory": str(tmp_path / "cache")}
    )
    destination = tmp_path / "memory.mp4"
    # WHY: the selector and renderer are the two external-work boundaries under inspection.
    with (
        patch("immich_memories.analysis.editorial_runtime.build_smart_pipeline") as pipeline,
        patch("immich_memories.generate.generate_memory", return_value=destination) as generate,
        patch("immich_memories.cli._generation_preview.print_generation_preview") as preview,
    ):
        pipeline.return_value.run_editorial_source.return_value = (candidates, result)
        run_pipeline_and_generate(
            assets=[clip.asset for clip in candidates],
            client=MagicMock(),
            config=config,
            progress=MagicMock(),
            duration=60,
            transition="cut",
            music=None,
            no_music=True,
            output_path=destination,
            memory_type="year_in_review",
            person_names=[],
            date_range=DateRange(start=datetime(2026, 1, 1), end=datetime(2026, 12, 31)),
            upload_to_immich=False,
            album=None,
            output_orientation=orientation,
            output_resolution="1080p",
            no_render=no_render,
        )
    if no_render:
        generate.assert_not_called()
        canvas = preview.call_args.args[0].canvas
    else:
        canvas = generate.call_args.args[0].output_canvas
    assert (canvas.width, canvas.height) == (width, height)
