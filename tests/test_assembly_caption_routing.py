"""Date/place options reach rendering even for one clip or disabled titles."""

from unittest.mock import patch

import pytest

from immich_memories.config_loader import Config
from immich_memories.generate import GenerationParams
from immich_memories.generate_settings import _build_assembly_settings
from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
    TitleScreenSettings,
    standalone_assembly_encoding_plan,
)
from immich_memories.processing.clip_caption import timeline_captions
from immich_memories.processing.probe_cache import _parse_video_probe
from immich_memories.processing.streaming_frame_decoder import make_decoder
from immich_memories.processing.video_assembler import VideoAssembler


def _assemble_offline(settings, clips, output):
    for clip in clips:
        clip.path.touch()
    assembler = VideoAssembler(settings)
    probe = _parse_video_probe(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "30/1",
                }
            ]
        }
    )
    # WHY: replace external processes while exercising public assembly and captions.
    with (
        # WHY: supply metadata without probing dummy media with FFmpeg.
        patch.object(assembler.prober.probe_cache, "get", return_value=probe),
        # WHY: reject the captionless encoder shortcut without launching FFmpeg.
        patch.object(assembler.encoder, "encode_single_clip", side_effect=AssertionError),
        # WHY: capture the final render contract instead of encoding dummy media.
        patch("immich_memories.processing.assembly_engine.streaming_assemble_full") as render,
    ):
        assert assembler.assemble(clips, output) == output
    render.assert_called_once()
    return render.call_args.kwargs


def _caption_filters(render):
    captions, font = timeline_captions(
        render["clips"], render["date_overlay"], render["place_overlay"], render["caption_locale"]
    )
    assert captions is not None
    return make_decoder(
        render["clips"][0],
        0,
        render["width"],
        render["height"],
        render["fps"],
        ctx=render["ctx"],
        caption=captions[0],
        caption_font=font,
    )._build_vf()


@pytest.mark.parametrize("date_overlay,place_overlay", [(True, False), (False, True), (True, True)])
def test_single_clip_without_titles_keeps_requested_captions(tmp_path, date_overlay, place_overlay):
    settings = AssemblySettings(
        encoding_plan=standalone_assembly_encoding_plan(),
        target_resolution=(1920, 1080),
        add_date_overlay=date_overlay,
        add_place_overlay=place_overlay,
    )
    clips = [
        AssemblyClip(tmp_path / "clip.mp4", 4.0, date="2025-08-10", location_name="Nice, France")
    ]

    render = _assemble_offline(settings, clips, tmp_path / "memory.mp4")

    assert render["clips"] == clips
    assert render["transitions"] == []
    assert render["encoding_plan"] is settings.encoding_plan
    filters = _caption_filters(render)
    assert ("SUNDAY 10" in filters) is date_overlay
    assert ("NICE, FRANCE" in filters) is place_overlay


def test_generation_keeps_french_captions_when_titles_are_disabled(tmp_path):
    config = Config()
    config.hardware.enabled = False
    config.title_screens.enabled = False
    config.title_screens.locale = "fr"
    params = GenerationParams(
        clips=[],
        output_path=tmp_path / "memory.mp4",
        config=config,
        add_date_overlay=True,
        add_place_overlay=True,
    )
    settings = _build_assembly_settings(params, [])
    assert settings.title_screens is None
    clips = [
        AssemblyClip(tmp_path / "one.mp4", 4.0, date="2025-08-10", location_name="Nice, France"),
        AssemblyClip(tmp_path / "two.mp4", 4.0, date="2025-08-11", location_name="Nice, France"),
    ]

    render = _assemble_offline(settings, clips, params.output_path)

    filters = _caption_filters(render)
    assert "DIMANCHE 10" in filters
    assert "SUNDAY" not in filters
    assert "NICE, FRANCE" in filters


@pytest.mark.parametrize("caption_locale,expected", [(None, "DIMANCHE 10"), ("en", "SUNDAY 10")])
def test_standalone_title_locale_is_used_unless_caption_locale_is_explicit(
    tmp_path, caption_locale, expected
):
    settings = AssemblySettings(
        encoding_plan=standalone_assembly_encoding_plan(),
        target_resolution=(1920, 1080),
        add_date_overlay=True,
        title_screens=TitleScreenSettings(locale="fr"),
        caption_locale=caption_locale,
    )
    clips = [AssemblyClip(tmp_path / "clip.mp4", 4.0, date="2025-08-10")]

    render = _assemble_offline(settings, clips, tmp_path / "memory.mp4")

    assert expected in _caption_filters(render)
