"""Certified Live material cannot be dropped or shortened at assembly handoffs."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from immich_memories import generate
from immich_memories.api.models import AssetType, VideoClipInfo
from immich_memories.config_loader import Config
from immich_memories.generate import GenerationError, GenerationParams
from immich_memories.generate_timeline import apply_final_content_budget, validate_certified_content
from immich_memories.processing.assembly_config import AssemblyClip
from immich_memories.processing.live_material import LiveRenderMaterial, LiveSourceEntry
from tests.conftest import make_asset


@pytest.fixture(autouse=True)
def no_external_work(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("post-extraction guards must not run media tools or services")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)


@pytest.fixture
def content(tmp_path):
    material = LiveRenderMaterial(
        (
            LiveSourceEntry("alias", "video", 0.0, 1.5, 1.5),
            LiveSourceEntry("still", "video", 0.0, 1.5, 4.0),
        )
    )
    asset = make_asset("still", duration=None)
    asset.type = AssetType.IMAGE
    asset.live_photo_video_id = "video"
    source = VideoClipInfo(
        asset=asset,
        duration_seconds=material.duration_seconds,
        live_burst_still_ids=list(material.still_ids),
        live_burst_video_ids=list(material.video_ids),
        live_burst_trim_points=list(material.trim_points),
        live_burst_shutter_timestamps=list(material.shutter_timestamps),
        live_burst_material=material.as_dict(),
        editorial_live_manifest={
            "version": "editorial-live-render-v1",
            "material": material.as_dict(),
            "selected_interval": [0.25, 1.75],
        },
    )
    config = Config(
        cache={
            "directory": str(tmp_path / "cache"),
            "database": str(tmp_path / "runs.db"),
            "video_cache_enabled": False,
        }
    )
    params = GenerationParams(
        clips=[source],
        output_path=tmp_path / "memory.mp4",
        config=config,
        no_music=True,
        target_duration_seconds=60.0,
    )
    params.timeline_plan = SimpleNamespace(content_budget=2.0)
    path = tmp_path / "certified.mp4"
    path.write_bytes(b"verified-extraction")
    return params, AssemblyClip(path=path, duration=1.5, asset_id="still")


def test_exact_content_and_all_source_aliases_survive_budget(content):
    params, rendered = content
    originals = [rendered]
    assert validate_certified_content(params, originals) == {"still"}
    assert apply_final_content_budget(params, originals) is originals
    assert params.clips[0].live_burst_still_ids == ["alias", "still"]


@pytest.mark.parametrize("change", ["missing", "duplicate", "identity", "short", "long", "seek"])
def test_lost_or_changed_certified_content_fails_even_without_target(content, change):
    params, rendered = content
    params.target_duration_seconds = None
    altered = {
        "missing": [],
        "duplicate": [rendered, rendered],
        "identity": [replace(rendered, asset_id="different")],
        "short": [replace(rendered, duration=1.4)],
        "long": [replace(rendered, duration=1.6)],
        "seek": [replace(rendered, input_seek=0.1)],
    }[change]
    with pytest.raises(ValueError, match="Certified editorial Live"):
        apply_final_content_budget(params, altered)


def test_material_alias_cannot_disappear_after_extraction(content):
    params, rendered = content
    params.clips[0].live_burst_still_ids = ["still"]
    with pytest.raises(ValueError, match="arrays disagree"):
        validate_certified_content(params, [rendered])


def test_repeated_certified_source_is_not_collapsed(content):
    params, rendered = content
    params.clips.append(params.clips[0])
    with pytest.raises(ValueError, match="duplicated in the render request"):
        validate_certified_content(params, [rendered])


def test_late_timeline_conflict_fails_before_sampling_or_shortening(content):
    params, rendered = content
    params.timeline_plan = SimpleNamespace(content_budget=1.0)
    with pytest.raises(ValueError, match="timeline budget contradicts certified"):
        apply_final_content_budget(params, [rendered])
    assert rendered.duration == 1.5


def test_uncertified_content_keeps_existing_budget_behavior(content):
    params, rendered = content
    params.clips[0].editorial_live_manifest = None
    params.timeline_plan = SimpleNamespace(content_budget=1.0)
    result = apply_final_content_budget(params, [rendered])
    assert result[0].asset_id == "still" and result[0].duration == 1.0


@pytest.mark.parametrize("boundary", ["validation", "budget"])
def test_actual_generation_fails_before_assembly_on_lost_certified_content(
    content,
    monkeypatch,
    boundary,
):
    params, rendered = content
    tracker = MagicMock()
    if boundary == "validation":
        rendered.path.unlink()  # Real validate_clips will drop this missing file.
    else:
        monkeypatch.setattr(generate, "_apply_final_content_budget", lambda *_args: [])
    monkeypatch.setattr(
        generate, "_extract_clips_with_optional_prefetch", lambda *_args, **_kw: [rendered]
    )
    assembly = MagicMock(side_effect=AssertionError("changed certified content reached assembly"))
    monkeypatch.setattr(generate, "_create_assembler", assembly)
    monkeypatch.setattr(generate, "_fail_run_if_running", lambda *args: tracker.fail_run(args[1]))
    with pytest.raises(GenerationError, match="Certified editorial Live source was lost"):
        generate.generate_memory(params, run_tracker=tracker)
    assembly.assert_not_called()
    tracker.fail_run.assert_called_once()
