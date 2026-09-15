"""Export uses the owner's orientation and final manually kept pictures."""

import pytest

from immich_memories.api.models import AssetType
from immich_memories.config_loader import Config
from immich_memories.processing.output_canvas import resolve_generation_canvas
from immich_memories.ui.pages._step4_generate import _build_generation_params
from immich_memories.ui.state import AppState
from tests.conftest import make_clip


@pytest.mark.parametrize(
    "choice,dimensions",
    [
        ("Auto (detect from clips)", (1080, 1920)),
        ("Landscape (16:9)", (1920, 1080)),
        ("Portrait (9:16)", (1080, 1920)),
        ("Square (1:1)", (1080, 1080)),
    ],
)
def test_manual_export_honors_orientation_without_replacing_kept_photos(
    tmp_path, choice, dimensions
):
    portraits = [make_clip(f"portrait-{i}", width=1080, height=1920) for i in range(11)]
    landscapes = [make_clip(f"landscape-{i}", width=1920, height=1080) for i in range(24)]
    pool = portraits + landscapes
    for clip in pool:
        clip.asset.type = AssetType.IMAGE
    kept = portraits + landscapes[:4]
    state = AppState(
        config=Config(),
        immich_url="http://immich.invalid",
        immich_api_key="test-api-key",
        pipeline_selected_clips=pool,
        selected_clip_ids={clip.asset.id for clip in kept},
        generation_options={"orientation": choice, "resolution": "1080p"},
    )

    params = _build_generation_params(state, state.get_selected_clips(), tmp_path / "manual.mp4")
    canvas = resolve_generation_canvas(params)

    assert (canvas.width, canvas.height) == dimensions
    assert [clip.asset.id for clip in params.clips] == [clip.asset.id for clip in kept]
