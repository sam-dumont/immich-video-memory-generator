"""Real FFmpeg integration tests for video transforms.

Tests transform_fit, transform_fill, add_date_overlay, get_video_dimensions,
and AspectRatioTransformer against actual FFmpeg on synthetic clips.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.conftest import ffprobe_json

pytestmark = [pytest.mark.integration, pytest.mark.xdist_group("ffmpeg")]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_dimensions(probe: dict) -> tuple[int, int]:
    for s in probe.get("streams", []):
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    raise ValueError("No video stream found")


def _sw_hw_config():
    """Software-only HardwareAccelConfig to keep tests deterministic."""
    from immich_memories.config_models import HardwareAccelConfig

    return HardwareAccelConfig(enabled=False)


# ---------------------------------------------------------------------------
# get_video_dimensions
# ---------------------------------------------------------------------------


class TestGetVideoDimensions:
    def test_landscape_clip(self, test_clip_720p: Path):
        from immich_memories.processing.transforms_ffmpeg import get_video_dimensions

        w, h = get_video_dimensions(test_clip_720p)
        assert (w, h) == (1280, 720)

    def test_portrait_clip(self, portrait_clip: Path):
        from immich_memories.processing.transforms_ffmpeg import get_video_dimensions

        w, h = get_video_dimensions(portrait_clip)
        assert (w, h) == (720, 1280)


# ---------------------------------------------------------------------------
# transform_fit
# ---------------------------------------------------------------------------


class TestTransformFit:
    def test_landscape_720p_to_1080p(self, test_clip_720p: Path, tmp_path: Path):
        from immich_memories.processing.transforms_ffmpeg import transform_fit

        out = tmp_path / "fit_1080p.mp4"
        result = transform_fit(test_clip_720p, out, (1920, 1080), _sw_hw_config(), output_crf=28)

        assert result == out
        assert out.exists()
        probe = ffprobe_json(out)
        w, h = _get_dimensions(probe)
        assert (w, h) == (1920, 1080)

    def test_portrait_to_landscape_fit(self, portrait_clip: Path, tmp_path: Path):
        """Portrait input fitted into landscape frame produces 1920x1080 with blur bars."""
        from immich_memories.processing.transforms_ffmpeg import transform_fit

        out = tmp_path / "portrait_fit.mp4"
        result = transform_fit(portrait_clip, out, (1920, 1080), _sw_hw_config(), output_crf=28)

        assert result == out
        probe = ffprobe_json(out)
        w, h = _get_dimensions(probe)
        assert (w, h) == (1920, 1080)


# ---------------------------------------------------------------------------
# transform_fill
# ---------------------------------------------------------------------------


class TestTransformFill:
    def test_fill_to_square(self, test_clip_720p: Path, tmp_path: Path):
        """Center-crop a 1280x720 clip to 1080x1080."""
        from immich_memories.processing.transforms_ffmpeg import transform_fill

        out = tmp_path / "fill_square.mp4"
        result = transform_fill(test_clip_720p, out, (1080, 1080), _sw_hw_config(), output_crf=28)

        assert result == out
        probe = ffprobe_json(out)
        w, h = _get_dimensions(probe)
        assert (w, h) == (1080, 1080)


# ---------------------------------------------------------------------------
# add_date_overlay
# ---------------------------------------------------------------------------


class TestAddDateOverlay:
    def test_produces_valid_output(self, test_clip_720p: Path, tmp_path: Path):
        from immich_memories.processing.transforms_ffmpeg import add_date_overlay

        out = tmp_path / "dated.mp4"
        result = add_date_overlay(test_clip_720p, out, "Jan 2025", output_crf=28)

        assert result == out
        assert out.exists()
        assert out.stat().st_size > 1000
        probe = ffprobe_json(out)
        w, h = _get_dimensions(probe)
        # Dimensions preserved
        assert (w, h) == (1280, 720)


# ---------------------------------------------------------------------------
# apply_aspect_ratio_transform (high-level API)
# ---------------------------------------------------------------------------


class TestApplyAspectRatioTransform:
    def test_fit_mode(self, test_clip_720p: Path, tmp_path: Path):
        from immich_memories.processing.transforms import apply_aspect_ratio_transform

        out = tmp_path / "ar_fit.mp4"
        result = apply_aspect_ratio_transform(
            test_clip_720p,
            out,
            orientation="landscape",
            scale_mode="fit",
            resolution=(1920, 1080),
            hardware_config=_sw_hw_config(),
            output_crf=28,
        )

        assert result == out
        probe = ffprobe_json(out)
        w, h = _get_dimensions(probe)
        assert (w, h) == (1920, 1080)

    def test_fill_mode(self, test_clip_720p: Path, tmp_path: Path):
        from immich_memories.processing.transforms import apply_aspect_ratio_transform

        out = tmp_path / "ar_fill.mp4"
        result = apply_aspect_ratio_transform(
            test_clip_720p,
            out,
            orientation="landscape",
            scale_mode="fill",
            resolution=(1920, 1080),
            hardware_config=_sw_hw_config(),
            output_crf=28,
        )

        assert result == out
        probe = ffprobe_json(out)
        w, h = _get_dimensions(probe)
        assert (w, h) == (1920, 1080)


# ---------------------------------------------------------------------------
# AspectRatioTransformer class
# ---------------------------------------------------------------------------


class TestAspectRatioTransformer:
    def test_transformer_fit_landscape(self, test_clip_720p: Path, tmp_path: Path):
        from immich_memories.processing.transforms import (
            AspectRatioTransformer,
            Orientation,
            ScaleMode,
        )

        transformer = AspectRatioTransformer(
            target_orientation=Orientation.LANDSCAPE,
            scale_mode=ScaleMode.FIT,
            target_resolution=(1920, 1080),
            hardware_config=_sw_hw_config(),
            output_crf=28,
        )

        out = tmp_path / "transformer_fit.mp4"
        result = transformer.transform(test_clip_720p, out)

        assert result == out
        probe = ffprobe_json(out)
        w, h = _get_dimensions(probe)
        assert (w, h) == (1920, 1080)

    def test_transformer_fill_square(self, test_clip_720p: Path, tmp_path: Path):
        from immich_memories.processing.transforms import (
            AspectRatioTransformer,
            Orientation,
            ScaleMode,
        )

        transformer = AspectRatioTransformer(
            target_orientation=Orientation.SQUARE,
            scale_mode=ScaleMode.FILL,
            target_resolution=(1920, 1080),
            hardware_config=_sw_hw_config(),
            output_crf=28,
        )

        out = tmp_path / "transformer_fill_sq.mp4"
        result = transformer.transform(test_clip_720p, out)

        assert result == out
        probe = ffprobe_json(out)
        w, h = _get_dimensions(probe)
        # Square orientation uses min(w, h) = 1080
        assert w == h
