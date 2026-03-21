"""Integration tests for the frame-by-frame photo renderer.

Tests render_ken_burns, render_slide_in, and render_collage with
real FFmpeg encoding and output verification.
"""

from __future__ import annotations

import subprocess

import cv2
import numpy as np
import pytest

from immich_memories.photos.renderer import (
    KenBurnsParams,
    render_collage,
    render_ken_burns,
    render_slide_in,
)
from tests.integration.conftest import ffprobe_json, get_duration, has_stream, requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


def _encode_frames(
    frames: list[np.ndarray], output_path, tw: int = 1920, th: int = 1080, fps: int = 60
):
    """Encode frames to H.264 mp4 (SDR, fast, for testing)."""
    raw = b"".join((np.clip(f * 255, 0, 255).astype(np.uint8)).tobytes() for f in frames)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{tw}x{th}",
            "-r",
            str(fps),
            "-i",
            "pipe:0",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-t",
            str(len(frames) / fps),
            "-shortest",
            str(output_path),
        ],
        input=raw,
        check=True,
        capture_output=True,
        timeout=120,
    )


def _make_test_image(
    w: int, h: int, color: tuple[float, float, float] = (0.5, 0.3, 0.2)
) -> np.ndarray:
    """Create a test image with a gradient (so zoom/pan is visible)."""
    img = np.zeros((h, w, 3), dtype=np.float32)
    # Horizontal gradient
    for x in range(w):
        img[:, x, 0] = color[0] * (1 - x / w) + 0.1 * (x / w)
    # Vertical gradient
    for y in range(h):
        img[y, :, 1] = color[1] * (1 - y / h) + 0.1 * (y / h)
    img[:, :, 2] = color[2]
    # Add a bright spot in the center (visible landmark for zoom/pan)
    cy, cx = h // 2, w // 2
    cv2.circle(img, (cx, cy), min(w, h) // 8, (0.9, 0.9, 0.1), -1)
    return img


class TestRenderKenBurns:
    """Tests for Ken Burns renderer."""

    def test_landscape_in_landscape_produces_valid_video(self, tmp_path):
        """Landscape photo in landscape viewport → valid mp4."""
        src = _make_test_image(1600, 1200)  # 4:3
        frames = render_ken_burns(src, 1920, 1080, KenBurnsParams(duration=2.0))
        assert len(frames) == 120  # 60fps (default) * 2s
        assert frames[0].shape == (1080, 1920, 3)

        output = tmp_path / "kb_ls.mp4"
        _encode_frames(frames, output)  # both default to 30fps
        probe = ffprobe_json(output)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")
        assert 1.5 < get_duration(probe) < 2.5

    def test_portrait_in_landscape_has_blur_bg(self, tmp_path):
        """Portrait photo in landscape viewport → blur background visible."""
        src = _make_test_image(800, 1200)  # Portrait
        frames = render_ken_burns(src, 1920, 1080, KenBurnsParams(duration=2.0))

        # Check first frame: center column should differ from edge column
        f = frames[0]
        center_col = f[:, 960, :].mean()
        edge_col = f[:, 50, :].mean()
        # Center has the sharp photo, edge has blurred bg — they should differ
        assert abs(center_col - edge_col) > 0.01

    def test_zoom_changes_visible_area(self, tmp_path):
        """Zoom in means first and last frames show different crops."""
        src = _make_test_image(1600, 1200)
        frames = render_ken_burns(
            src,
            1920,
            1080,
            KenBurnsParams(
                zoom_start=1.0,
                zoom_end=1.2,
                duration=2.0,
            ),
        )
        # First and last frames should differ (zoom changes visible area)
        diff = np.abs(frames[0].astype(float) - frames[-1].astype(float)).mean()
        assert diff > 0.001, "Zoom should produce visibly different frames"

    def test_no_black_pixels_in_output(self, tmp_path):
        """Output frames should have NO black bars (image always covers viewport)."""
        src = _make_test_image(1600, 1200)
        frames = render_ken_burns(
            src,
            1920,
            1080,
            KenBurnsParams(
                zoom_start=1.0,
                zoom_end=1.15,
                pan_start=(0.2, 0.5),
                pan_end=(0.8, 0.5),
                duration=2.0,
            ),
        )
        for i in [0, len(frames) // 2, -1]:
            f = frames[i]
            # Check edges aren't all-black
            assert f[0, :, :].max() > 0.01, f"Top row is black on frame {i}"
            assert f[-1, :, :].max() > 0.01, f"Bottom row is black on frame {i}"
            assert f[:, 0, :].max() > 0.01, f"Left col is black on frame {i}"
            assert f[:, -1, :].max() > 0.01, f"Right col is black on frame {i}"


class TestRenderSlideIn:
    """Tests for slide-in effect."""

    def test_produces_correct_frame_count(self):
        """Frame count matches fps * duration."""
        src = _make_test_image(800, 1200)
        frames = render_slide_in(src, 1920, 1080, fps=30, duration=3.0)
        assert len(frames) == 90

    def test_first_frame_differs_from_last(self):
        """Photo is sliding — first and last frames should differ."""
        src = _make_test_image(800, 1200)
        frames = render_slide_in(src, 1920, 1080, direction="right", fps=30, duration=3.0)
        diff = np.abs(frames[0].astype(float) - frames[-1].astype(float)).mean()
        assert diff > 0.01, "Slide-in should produce different first and last frames"

    def test_last_frame_has_photo_centered(self):
        """After slide completes, photo should be centered in the frame."""
        src = _make_test_image(800, 1200)
        frames = render_slide_in(
            src, 1920, 1080, direction="right", hold_ratio=0.5, fps=30, duration=4.0
        )
        last = frames[-1]
        # Center pixel should be from the photo (brighter than blur bg edges)
        center_val = last[540, 960, :].mean()
        assert center_val > 0.05

    def test_encodes_to_valid_video(self, tmp_path):
        """Slide-in encodes to valid mp4."""
        src = _make_test_image(800, 1200)
        frames = render_slide_in(src, 1920, 1080, fps=30, duration=2.0)
        output = tmp_path / "slide.mp4"
        _encode_frames(frames, output, fps=30)
        probe = ffprobe_json(output)
        assert has_stream(probe, "video")


class TestRenderCollage:
    """Tests for collage renderer."""

    def test_horizontal_3_photos(self):
        """3 photos produce correct frame count."""
        photos = [
            _make_test_image(800, 1200, c)
            for c in [(0.8, 0.1, 0.1), (0.1, 0.8, 0.1), (0.1, 0.1, 0.8)]
        ]
        frames = render_collage(photos, 1920, 1080, orientation="horizontal", fps=30, duration=3.0)
        assert len(frames) == 90
        assert frames[0].shape == (1080, 1920, 3)

    def test_vertical_3_photos(self):
        """Vertical collage works for portrait output."""
        photos = [
            _make_test_image(1600, 1200, c)
            for c in [(0.8, 0.1, 0.1), (0.1, 0.8, 0.1), (0.1, 0.1, 0.8)]
        ]
        frames = render_collage(photos, 1080, 1920, orientation="vertical", fps=30, duration=3.0)
        assert frames[0].shape == (1920, 1080, 3)

    def test_slide_in_makes_first_frame_different(self):
        """With slide_in, first frame has fewer photos visible than last."""
        photos = [_make_test_image(800, 1200) for _ in range(3)]
        frames_slide = render_collage(photos, 1920, 1080, slide_in=True, fps=30, duration=3.0)
        frames_static = render_collage(photos, 1920, 1080, slide_in=False, fps=30, duration=3.0)
        diff = np.abs(frames_slide[0].astype(float) - frames_static[0].astype(float)).mean()
        assert diff > 0.01, "Slide-in first frame should differ from static"

    def test_background_is_not_black(self):
        """Gaps between photos should be blurred, not black."""
        photos = [_make_test_image(800, 1200, (0.5, 0.5, 0.5)) for _ in range(3)]
        frames = render_collage(photos, 1920, 1080, gap=50, slide_in=False, fps=30, duration=1.0)
        # Check the gap area
        gap_pixel = frames[0][540, 1920 // 3, :].mean()  # In the gap between photos
        assert gap_pixel > 0.05, "Gap should have blurred content, not black"

    def test_rejects_too_few_photos(self):
        """Collage requires at least 2 photos."""
        with pytest.raises(ValueError, match="2-4"):
            render_collage([_make_test_image(800, 600)], 1920, 1080)

    def test_encodes_to_valid_video(self, tmp_path):
        """Collage encodes to valid mp4."""
        photos = [_make_test_image(800, 1200) for _ in range(3)]
        frames = render_collage(photos, 1920, 1080, fps=30, duration=2.0)
        output = tmp_path / "collage.mp4"
        _encode_frames(frames, output, fps=30)
        probe = ffprobe_json(output)
        assert has_stream(probe, "video")
        assert 1.5 < get_duration(probe) < 2.5
