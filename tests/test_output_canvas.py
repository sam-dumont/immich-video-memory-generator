"""Behavioral tests for resolving one output canvas per generation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from immich_memories.config_loader import Config
from immich_memories.generate import GenerationParams
from immich_memories.generate_photos import _detect_photo_resolution
from immich_memories.generate_settings import _build_assembly_settings
from immich_memories.processing.output_canvas import OutputCanvas, resolve_output_canvas


def _clip(width: int, height: int) -> SimpleNamespace:
    return SimpleNamespace(width=width, height=height)


def test_explicit_landscape_canvas_overrides_config_and_portrait_majority() -> None:
    """A command-level canvas must win over both config and source orientation."""
    clips = [_clip(1080, 1920), _clip(1080, 1920)]

    canvas = resolve_output_canvas(
        resolution="1080p",
        orientation="landscape",
        configured_resolution=(3840, 2160),
        clips=clips,
    )

    assert canvas == OutputCanvas(width=1920, height=1080, orientation="landscape")


def test_auto_resolution_uses_source_tier_and_explicit_orientation() -> None:
    """Auto chooses the source tier but does not override an explicit orientation."""
    clips = [_clip(3840, 2160), _clip(2160, 3840), _clip(1920, 1080)]

    canvas = resolve_output_canvas(
        resolution="auto",
        orientation="portrait",
        configured_resolution=(1920, 1080),
        clips=clips,
    )

    assert canvas == OutputCanvas(width=2160, height=3840, orientation="portrait")


def test_implicit_canvas_uses_configured_tier_and_source_orientation() -> None:
    """Without a command orientation, portrait source majority rotates the config tier once."""
    clips = [_clip(1080, 1920), _clip(1080, 1920), _clip(1920, 1080)]

    canvas = resolve_output_canvas(
        resolution=None,
        orientation=None,
        configured_resolution=(1280, 720),
        clips=clips,
    )

    assert canvas == OutputCanvas(width=720, height=1280, orientation="portrait")


def test_square_canvas_uses_short_edge_of_resolution_tier() -> None:
    """Square output is a real canvas, not a landscape canvas with a later crop."""
    canvas = resolve_output_canvas(
        resolution="1080p",
        orientation="square",
        configured_resolution=(3840, 2160),
        clips=[],
    )

    assert canvas == OutputCanvas(width=1080, height=1080, orientation="square")


def test_photo_and_assembly_consume_the_same_explicit_canvas() -> None:
    """Photo intermediates cannot silently use config/source geometry."""
    params = GenerationParams(
        clips=[_clip(1080, 1920), _clip(1080, 1920)],
        output_path=Path("/tmp/out.mp4"),
        config=Config(output={"resolution": "4k"}),
        output_resolution="1080p",
        output_orientation="landscape",
    )

    settings = _build_assembly_settings(params, [])

    assert _detect_photo_resolution(params) == (1920, 1080)
    assert settings.target_resolution == (1920, 1080)
    assert params.output_canvas == OutputCanvas(1920, 1080, "landscape")
