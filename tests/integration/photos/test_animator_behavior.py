"""Behavioral tests for photos/animator.py — photo-to-video conversion."""

from __future__ import annotations

import subprocess

import pytest

from immich_memories.config_models import PhotoConfig
from immich_memories.photos.animator import PhotoAnimator, prepare_photo_source
from immich_memories.photos.models import AnimationMode
from tests.integration.conftest import ffprobe_json, get_duration, has_stream, requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


class TestPreparePhotoSource:
    def test_jpeg_returns_path_and_dimensions(self, test_photo_landscape, tmp_path):
        """JPEG input should return a PreparedPhoto with correct dimensions."""
        result = prepare_photo_source(test_photo_landscape, tmp_path)

        assert result.path.exists()
        assert result.width == 1920
        assert result.height == 1080
        assert result.has_gain_map is False


class TestAutoModeSelection:
    def test_no_face_landscape_returns_ken_burns(self):
        config = PhotoConfig()
        animator = PhotoAnimator(config, target_w=1920, target_h=1080)

        mode = animator.resolve_auto_mode(width=1920, height=1080, face_bbox=None)

        assert mode == AnimationMode.KEN_BURNS

    def test_with_face_returns_face_zoom(self):
        config = PhotoConfig()
        animator = PhotoAnimator(config, target_w=1920, target_h=1080)

        mode = animator.resolve_auto_mode(
            width=1920,
            height=1080,
            face_bbox=(0.3, 0.2, 0.5, 0.6),
        )

        assert mode == AnimationMode.FACE_ZOOM

    def test_portrait_no_face_returns_blur_bg(self):
        config = PhotoConfig()
        animator = PhotoAnimator(config, target_w=1920, target_h=1080)

        mode = animator.resolve_auto_mode(width=1080, height=1920, face_bbox=None)

        assert mode == AnimationMode.BLUR_BG


class TestKenBurnsAnimation:
    def test_produces_valid_video(self, test_photo_landscape, tmp_path):
        """Ken Burns should produce a video with correct resolution and duration."""
        config = PhotoConfig()
        config.duration = 3.0
        animator = PhotoAnimator(config, target_w=1280, target_h=720)
        output = tmp_path / "ken_burns.mp4"

        cmd = animator.build_ffmpeg_command(
            source_path=test_photo_landscape,
            output_path=output,
            width=1920,
            height=1080,
            mode=AnimationMode.KEN_BURNS,
            asset_id="test-asset",
        )

        subprocess.run(cmd, check=True, capture_output=True, timeout=30)

        assert output.exists()
        probe = ffprobe_json(output)
        assert has_stream(probe, "video")
        duration = get_duration(probe)
        assert 2.0 < duration < 4.0
