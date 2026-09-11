"""A rendered editorial film retains every selected carrier in its bound order."""

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from immich_memories.analysis.editorial_planner import EditorialSelection
from immich_memories.api.models import AssetType
from immich_memories.config_loader import Config
from immich_memories.generate import GenerationParams
from immich_memories.generate_clips import _extract_clips
from immich_memories.generate_timeline import apply_final_content_budget, validate_certified_content
from immich_memories.processing.assembly_config import AssemblyClip
from immich_memories.processing.editorial_timing import (
    bind_editorial_timeline,
    prepare_certified_timeline,
    timing_policy_for_params,
)
from immich_memories.processing.live_material import LiveRenderMaterial, LiveSourceEntry
from tests.conftest import make_clip


def _bound_params(tmp_path, kinds=("still", "live-still", "video", "live-motion")):
    clips = []
    for index, kind in enumerate(kinds):
        clip = make_clip(f"source-{index}", duration=3.5)
        if kind != "video":
            clip.asset.type = AssetType.IMAGE
        if kind.startswith("live-"):
            clip.asset.live_photo_video_id = f"companion-{index}"
        if kind == "live-motion":
            material = LiveRenderMaterial(
                (
                    LiveSourceEntry(
                        clip.asset.id,
                        clip.asset.live_photo_video_id,
                        0.0,
                        0.0,
                        3.5,
                    ),
                )
            )
            clip.live_burst_still_ids = list(material.still_ids)
            clip.live_burst_video_ids = list(material.video_ids)
            clip.live_burst_trim_points = list(material.trim_points)
            clip.live_burst_shutter_timestamps = list(material.shutter_timestamps)
            clip.live_burst_material = material.as_dict()
            clip.editorial_live_manifest = {
                "version": "editorial-live-render-v1",
                "material": material.as_dict(),
                "selected_interval": [0.0, 3.5],
            }
        clips.append(clip)
    params = GenerationParams(
        clips=clips,
        output_path=tmp_path / "out.mp4",
        config=Config(),
        client=MagicMock(),
        target_duration_seconds=60,
        memory_type="custom",
        clip_segments={clip.asset.id: (0.0, 3.5) for clip in clips},
        editorial_selections=tuple(
            EditorialSelection(
                clip.asset.id, 0.0, 3.5, "motion" if kind in {"video", "live-motion"} else "still"
            )
            for clip, kind in zip(clips, kinds, strict=True)
        ),
    )
    policy = timing_policy_for_params(params)
    carriers = [{"asset_id": clip.asset.id, "seconds": 3.5} for clip in clips]
    timeline = policy.resolve(carriers, {clip.asset.id: clip.asset for clip in clips})
    params.editorial_render_timing = bind_editorial_timeline(
        policy,
        timeline,
        [clip.asset.id for clip in clips],
    )
    return params


def _rendered(params, tmp_path):
    return [
        AssemblyClip(path=tmp_path / (clip.asset.id + ".mp4"), duration=3.5, asset_id=clip.asset.id)
        for clip in params.clips
    ]


def test_complete_mixed_selection_preserves_live_validation_contract(tmp_path):
    params = _bound_params(tmp_path)
    rendered = _rendered(params, tmp_path)
    assert validate_certified_content(params, rendered) == {"source-3"}
    assert apply_final_content_budget(params, rendered) == rendered
    rendered[-1] = replace(rendered[-1], duration=3.0)
    with pytest.raises(ValueError, match="Certified editorial Live interval changed"):
        validate_certified_content(params, rendered)


@pytest.mark.parametrize("change", ["missing", "extra", "duplicated", "order"])
def test_bound_render_rejects_membership_changes_with_a_specific_reason(tmp_path, change):
    params = _bound_params(tmp_path)
    rendered = _rendered(params, tmp_path)
    if change == "missing":
        rendered.pop(0)
        reason = "missing=\\['source-0'\\]"
    elif change == "extra":
        rendered.append(replace(rendered[0], asset_id="unselected"))
        reason = "extra=\\['unselected'\\]"
    elif change == "duplicated":
        rendered.append(rendered[0])
        reason = "duplicated=\\['source-0'\\]"
    else:
        rendered[0], rendered[1] = rendered[1], rendered[0]
        reason = "order_changed=True"
    with pytest.raises(ValueError, match=reason):
        apply_final_content_budget(params, rendered)


@pytest.mark.parametrize("lost_kind", ["still", "video"])
def test_actual_extraction_failure_cannot_publish_a_smaller_selected_film(
    tmp_path, monkeypatch, lost_kind
):
    params = _bound_params(tmp_path, (lost_kind, "still"))
    prepare_certified_timeline(params)

    def render(**kwargs):
        if kwargs["asset"].id == "source-0":
            return None
        return AssemblyClip(
            path=tmp_path / "rendered.mp4",
            duration=kwargs["config"].duration,
            asset_id=kwargs["asset"].id,
            is_photo=True,
        )

    monkeypatch.setattr("immich_memories.photos.photo_pipeline._render_single_photo", render)
    monkeypatch.setattr(
        "immich_memories.generate_photos._detect_photo_resolution", lambda *_: (1920, 1080)
    )
    monkeypatch.setattr("immich_memories.generate_clips._download_video_path", lambda *_: None)
    rendered = _extract_clips(params, None, tmp_path)
    assert [clip.asset_id for clip in rendered] == ["source-1"]
    with pytest.raises(ValueError, match="missing=\\['source-0'\\]"):
        apply_final_content_budget(params, rendered)


def test_standalone_unbound_generation_keeps_its_partial_source_behavior(tmp_path):
    params = _bound_params(tmp_path, ("still", "video"))
    params.editorial_render_timing = None
    rendered = _rendered(params, tmp_path)[1:]
    assert validate_certified_content(params, rendered) == set()
