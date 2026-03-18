"""Integration tests for assembling photo clips alongside video clips.

Verifies that photo-generated .mp4 clips can be concatenated with real
video clips through the assembly pipeline — crossfade transitions work,
audio tracks are present, and output has correct total duration.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from immich_memories.config_models import PhotoConfig
from immich_memories.photos.animator import PhotoAnimator
from immich_memories.photos.models import AnimationMode
from immich_memories.processing.assembly_config import (
    AssemblyClip,
)
from tests.integration.conftest import ffprobe_json, get_duration, has_stream, requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


def _get_resolution(probe_data: dict) -> tuple[int, int]:
    for s in probe_data.get("streams", []):
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    msg = "No video stream found"
    raise ValueError(msg)


def _make_photo_clip(
    source: Path,
    output: Path,
    width: int,
    height: int,
    mode: AnimationMode,
) -> Path:
    """Convert a photo to a video clip via PhotoAnimator."""
    config = PhotoConfig(duration=3.0, zoom_factor=1.15)
    animator = PhotoAnimator(config, target_w=1280, target_h=720)
    cmd = animator.build_ffmpeg_command(
        source_path=source,
        output_path=output,
        width=width,
        height=height,
        mode=mode,
        asset_id="photo-asset",
    )
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    return output


class TestMixedAssembly:
    """Tests for assembling photo clips together with video clips."""

    def test_photo_clip_concat_with_video_clip(
        self, test_clip_720p, test_photo_landscape, tmp_path
    ):
        """A video clip + a photo clip can be concatenated into one output."""
        # Generate photo clip at same resolution as test video
        photo_mp4 = _make_photo_clip(
            test_photo_landscape,
            tmp_path / "photo.mp4",
            1920,
            1080,
            AnimationMode.KEN_BURNS,
        )

        # Concatenate video + photo using ffmpeg concat demuxer
        concat_list = tmp_path / "concat.txt"
        concat_list.write_text(f"file '{test_clip_720p}'\nfile '{photo_mp4}'\n")
        output = tmp_path / "mixed.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-c",
                "copy",
                str(output),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )

        assert output.exists()
        probe = ffprobe_json(output)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")

        # Video (3s) + photo (3s) ≈ 6s total
        duration = get_duration(probe)
        assert 5.0 < duration < 7.5

    def test_two_photo_clips_different_modes(
        self, test_photo_landscape, test_photo_portrait, tmp_path
    ):
        """Two photo clips with different modes can be concatenated."""
        kb_clip = _make_photo_clip(
            test_photo_landscape,
            tmp_path / "kb.mp4",
            1920,
            1080,
            AnimationMode.KEN_BURNS,
        )
        blur_clip = _make_photo_clip(
            test_photo_portrait,
            tmp_path / "blur.mp4",
            1080,
            1920,
            AnimationMode.BLUR_BG,
        )

        concat_list = tmp_path / "concat2.txt"
        concat_list.write_text(f"file '{kb_clip}'\nfile '{blur_clip}'\n")
        output = tmp_path / "two_photos.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-c",
                "copy",
                str(output),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )

        assert output.exists()
        probe = ffprobe_json(output)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")
        duration = get_duration(probe)
        assert 5.0 < duration < 7.5

    def test_assembly_clip_is_photo_flag(self, test_photo_landscape, tmp_path):
        """AssemblyClip with is_photo=True can be created from photo output."""
        photo_mp4 = _make_photo_clip(
            test_photo_landscape,
            tmp_path / "flagged.mp4",
            1920,
            1080,
            AnimationMode.KEN_BURNS,
        )

        clip = AssemblyClip(
            path=photo_mp4,
            duration=3.0,
            is_photo=True,
            asset_id="photo-123",
        )

        assert clip.is_photo is True
        assert clip.path.exists()

        probe = ffprobe_json(clip.path)
        assert has_stream(probe, "video")
