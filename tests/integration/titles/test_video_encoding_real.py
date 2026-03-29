"""Real video encoding integration tests for title screens.

Creates actual title videos via PIL → FFmpeg pipe. Verifies output
is a valid MP4 with correct resolution, duration, and visual content.

Run: make test-integration-titles
"""

from __future__ import annotations

from pathlib import Path

import pytest

from immich_memories.titles.styles import TitleStyle
from immich_memories.titles.video_encoding import create_title_video
from tests.integration.conftest import requires_ffmpeg
from tests.integration.titles.conftest import (
    TITLE_H,
    TITLE_W,
    extract_frame_rgb,
    ffprobe_stream,
    has_audio_stream,
)

pytestmark = [pytest.mark.integration, requires_ffmpeg, pytest.mark.xdist_group("ffmpeg")]

_STYLE = TitleStyle(
    name="test_encode",
    background_colors=["#1A1A2E", "#16213E"],
    background_type="soft_gradient",
    text_color="#FFFFFF",
    animation_preset="fade_up",
    font_weight="semibold",
)


class TestCreateTitleVideo:
    def test_valid_mp4_with_video_stream(self, tmp_path: Path):
        out = tmp_path / "title.mp4"
        create_title_video(
            "Integration Test",
            "Subtitle",
            _STYLE,
            out,
            width=TITLE_W,
            height=TITLE_H,
            duration=2.0,
            fps=10.0,
            animated_background=False,
            hdr=False,
        )
        assert out.exists()
        assert out.stat().st_size > 0

        stream = ffprobe_stream(out)
        assert stream["codec_type"] == "video"
        # Duration should be approximately 2 seconds
        dur = float(stream.get("duration", 0))
        assert 1.5 < dur < 3.0, f"Unexpected duration: {dur}"

    def test_has_audio_stream(self, tmp_path: Path):
        """create_title_video adds a silent audio track (anullsrc)."""
        out = tmp_path / "title_audio.mp4"
        create_title_video(
            "Audio Check",
            None,
            _STYLE,
            out,
            width=TITLE_W,
            height=TITLE_H,
            duration=1.5,
            fps=10.0,
            animated_background=False,
            hdr=False,
        )
        assert has_audio_stream(out)

    def test_frame_not_blank(self, tmp_path: Path):
        out = tmp_path / "title_content.mp4"
        create_title_video(
            "Not Blank",
            "Check",
            _STYLE,
            out,
            width=TITLE_W,
            height=TITLE_H,
            duration=1.5,
            fps=10.0,
            animated_background=False,
            hdr=False,
        )
        # Extract a mid-video frame and check for visual content
        mid_frame = extract_frame_rgb(out, frame_num=5, width=TITLE_W, height=TITLE_H)
        assert mid_frame.std() > 0, "Encoded frame is blank"


class TestFadeFromWhite:
    def test_first_frame_bright(self, tmp_path: Path):
        """fade_from_white=True should make the first frame near-white."""
        out = tmp_path / "title_fade_white.mp4"
        create_title_video(
            "Fade White",
            None,
            _STYLE,
            out,
            width=TITLE_W,
            height=TITLE_H,
            duration=2.0,
            fps=10.0,
            animated_background=False,
            fade_from_white=True,
            hdr=False,
        )
        first = extract_frame_rgb(out, frame_num=0, width=TITLE_W, height=TITLE_H)
        mean_brightness = first.mean()
        assert mean_brightness > 180, (
            f"First frame should be near-white, got mean={mean_brightness}"
        )


class TestOutputResolution:
    def test_matches_requested_size(self, tmp_path: Path):
        w, h = 640, 360
        out = tmp_path / "title_res.mp4"
        create_title_video(
            "Resolution",
            None,
            _STYLE,
            out,
            width=w,
            height=h,
            duration=1.0,
            fps=10.0,
            animated_background=False,
            hdr=False,
        )
        stream = ffprobe_stream(out)
        assert int(stream["width"]) == w
        assert int(stream["height"]) == h
