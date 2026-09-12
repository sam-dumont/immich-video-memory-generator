"""Title composition must preserve the complete certified content interval."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from immich_memories.api.models import AssetType, VideoClipInfo
from immich_memories.config_loader import Config
from immich_memories.generate import GenerationParams
from immich_memories.generate_settings import _build_assembly_settings
from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
    TitleScreenSettings,
    standalone_assembly_encoding_plan,
)
from immich_memories.processing.encoding_plan import HdrTransfer
from immich_memories.processing.live_material import LiveRenderMaterial, LiveSourceEntry
from immich_memories.processing.title_inserter import TitleInserter
from tests.conftest import make_asset


@pytest.fixture(autouse=True)
def no_external_work(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("title agreement tests must not run media tools or services")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)


@pytest.fixture
def title_path(tmp_path, monkeypatch):
    """Run the real inserter and divider paths with only media generation faked."""
    settings = AssemblySettings(
        encoding_plan=standalone_assembly_encoding_plan(),
        title_screens=TitleScreenSettings(divider_mode="none"),
    )
    generator = MagicMock()
    generator.generate_title_screen.return_value = SimpleNamespace(
        path=tmp_path / "title.mp4", duration=3.5
    )
    generator.generate_ending_screen.return_value = SimpleNamespace(
        path=tmp_path / "ending.mp4", duration=7.0
    )
    monkeypatch.setattr("immich_memories.titles.TitleScreenGenerator", lambda **_kw: generator)

    def build(certified, clips, mutate=None):
        settings.certified_content_intervals = certified
        inserter = TitleInserter(settings, MagicMock())
        monkeypatch.setattr(
            inserter, "_resolve_assembly_params", lambda _clips: (320, 180, 30, "sdr")
        )
        monkeypatch.setattr(
            inserter.background_renderer,
            "render_first_clip",
            lambda *_a, **_kw: tmp_path / "head.mp4",
        )
        monkeypatch.setattr(
            inserter.background_renderer,
            "render_last_clip",
            lambda *_a, **_kw: tmp_path / "tail.mp4",
        )
        if mutate is not None:

            def transitions(final):
                mutate(final)
                return ["cut"] * (len(final) - 1)

            monkeypatch.setattr(inserter, "_decide_transitions_for_final_clips", transitions)
        delivered = []

        def assemble(final, output_path, _progress):
            delivered.extend(final)
            return output_path

        return inserter, clips, assemble, delivered

    return settings, build, generator


def content(name):
    return AssemblyClip(path=Path(f"/{name}.mp4"), asset_id=name, duration=4.0)


@pytest.mark.parametrize("certified_ids", [(), ("first",), ("last",), ("first", "last")])
def test_actual_title_flow_preserves_only_certified_intervals(title_path, tmp_path, certified_ids):
    _, build, generator = title_path
    certified = dict.fromkeys(certified_ids, (0.25, 4.25))
    inserter, clips, assemble, delivered = build(certified, [content("first"), content("last")])
    assert (
        inserter.assemble_with_titles(clips, tmp_path / "out.mp4", assemble) == tmp_path / "out.mp4"
    )
    by_id = {clip.asset_id: clip for clip in delivered}
    assert by_id["first"].duration == (4.0 if "first" in certified_ids else 3.5)
    assert by_id["first"].input_seek == (0.0 if "first" in certified_ids else 0.5)
    assert by_id["last"].duration == (4.0 if "last" in certified_ids else 3.5)
    assert by_id["last"].input_seek == 0.0
    assert [clip.asset_id for clip in delivered] == [
        "title_screen",
        "first",
        "last",
        "ending_screen",
    ]
    assert (
        generator.generate_title_screen.call_args.kwargs["content_clip_path"]
        == tmp_path / "head.mp4"
    )
    assert (
        generator.generate_ending_screen.call_args.kwargs["content_clip_path"]
        == tmp_path / "tail.mp4"
    )


def test_single_certified_clip_survives_both_title_consumption_paths(title_path, tmp_path):
    _, build, _ = title_path
    inserter, clips, assemble, delivered = build({"only": (1.0, 5.0)}, [content("only")])
    inserter.assemble_with_titles(clips, tmp_path / "out.mp4", assemble)
    selected = [clip for clip in delivered if clip.asset_id == "only"]
    assert len(selected) == 1 and selected[0].duration == 4.0 and selected[0].input_seek == 0.0


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "identity", "short", "long", "seek", "title"]
)
def test_final_title_handoff_rejects_changed_certified_content(title_path, tmp_path, mutation):
    _, build, _ = title_path

    def mutate(final):
        position = next(i for i, clip in enumerate(final) if clip.asset_id == "certified")
        clip = final[position]
        if mutation == "missing":
            final.pop(position)
        elif mutation == "duplicate":
            final.insert(position, clip)
        else:
            changes = {
                "identity": {"asset_id": "other"},
                "short": {"duration": 3.5},
                "long": {"duration": 4.5},
                "seek": {"input_seek": 0.5},
                "title": {"is_title_screen": True},
            }
            final[position] = replace(clip, **changes[mutation])

    inserter, clips, assemble, delivered = build(
        {"certified": (0.0, 4.0)}, [content("certified")], mutate
    )
    with pytest.raises(ValueError, match="Certified editorial Live"):
        inserter.assemble_with_titles(clips, tmp_path / "out.mp4", assemble)
    assert delivered == []


def test_disabling_titles_cannot_bypass_certificate_validation(title_path, tmp_path):
    settings, build, _ = title_path
    settings.title_screens = None
    inserter, clips, assemble, delivered = build({"certified": (0.0, 4.0)}, [content("other")])
    with pytest.raises(ValueError, match="Certified editorial Live source"):
        inserter.assemble_with_titles(clips, tmp_path / "out.mp4", assemble)
    assert delivered == []


def test_settings_mutation_during_titles_cannot_remove_protection(title_path, tmp_path):
    settings, build, _ = title_path

    def mutate(final):
        settings.certified_content_intervals.clear()
        final[1] = replace(final[1], duration=3.5)

    inserter, clips, assemble, delivered = build(
        {"certified": (0.0, 4.0)}, [content("certified")], mutate
    )
    with pytest.raises(ValueError, match="Certified editorial Live interval changed"):
        inserter.assemble_with_titles(clips, tmp_path / "out.mp4", assemble)
    assert delivered == []


def test_generation_settings_bind_actual_source_certificate(tmp_path, monkeypatch):
    material = LiveRenderMaterial((LiveSourceEntry("still", "video", 1.0, 0.0, 5.0),))
    asset = make_asset("still", duration=None)
    asset.type = AssetType.IMAGE
    asset.live_photo_video_id = "video"
    source = VideoClipInfo(
        asset=asset,
        duration_seconds=5.0,
        live_burst_still_ids=list(material.still_ids),
        live_burst_video_ids=list(material.video_ids),
        live_burst_trim_points=list(material.trim_points),
        live_burst_shutter_timestamps=list(material.shutter_timestamps),
        live_burst_material=material.as_dict(),
        editorial_live_manifest={
            "version": "editorial-live-render-v1",
            "material": material.as_dict(),
            "selected_interval": [0.25, 4.25],
        },
    )
    params = GenerationParams(
        clips=[source], output_path=tmp_path / "out.mp4", config=Config(hardware={"enabled": False})
    )
    monkeypatch.setattr(
        "immich_memories.generate_settings.detect_dominant_hdr_transfer",
        lambda *_a, **_kw: HdrTransfer.NONE,
    )
    rendered = content("still")
    settings = _build_assembly_settings(params, [rendered])
    assert settings.certified_content_intervals == {"still": (0.25, 4.25)}
    with pytest.raises(ValueError, match="Certified editorial Live interval changed"):
        _build_assembly_settings(params, [replace(rendered, duration=3.5)])
    source.editorial_live_manifest = None
    assert _build_assembly_settings(params, [rendered]).certified_content_intervals == {}
