"""Integration tests for photo-to-video animation via FFmpeg.

Each test converts a real JPEG to an .mp4 clip, then verifies the animation
ACTUALLY WORKS — not just that FFmpeg didn't crash. We extract frames at
different timestamps and compare them to verify zoom, blur, crop, etc.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from immich_memories.config_models import PhotoConfig
from immich_memories.photos.animator import PhotoAnimator
from immich_memories.photos.models import AnimationMode
from tests.integration.conftest import ffprobe_json, get_duration, has_stream, requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


def _get_resolution(probe_data: dict) -> tuple[int, int]:
    for s in probe_data.get("streams", []):
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    msg = "No video stream found"
    raise ValueError(msg)


def _run_animator(
    source: Path,
    output: Path,
    width: int,
    height: int,
    mode: AnimationMode,
    *,
    duration: float = 3.0,
    face_bbox: tuple[float, float, float, float] | None = None,
) -> Path:
    """Run PhotoAnimator and execute the FFmpeg command."""
    config = PhotoConfig(duration=duration, zoom_factor=1.15)
    animator = PhotoAnimator(config, target_w=1920, target_h=1080)
    cmd = animator.build_ffmpeg_command(
        source_path=source,
        output_path=output,
        width=width,
        height=height,
        mode=mode,
        face_bbox=face_bbox,
        asset_id="test-asset",
    )
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    return output


def _extract_frame_raw(video: Path, timestamp: str, out_path: Path) -> bytes:
    """Extract a single frame as raw RGB bytes at the given timestamp."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            timestamp,
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            str(out_path),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    return out_path.read_bytes()


def _frame_difference(frame_a: bytes, frame_b: bytes) -> float:
    """Calculate mean absolute difference between two raw frames (0.0 = identical)."""
    if len(frame_a) != len(frame_b):
        return 1.0
    total = sum(abs(a - b) for a, b in zip(frame_a, frame_b, strict=True))
    return total / len(frame_a) / 255.0


def _pixel_variance_in_region(
    frame: bytes,
    width: int,
    height: int,
    x: int,
    y: int,
    region_w: int,
    region_h: int,
) -> float:
    """Calculate pixel variance in a rectangular region (high = detailed, low = blurred/solid)."""
    values = []
    for row in range(y, min(y + region_h, height)):
        for col in range(x, min(x + region_w, width)):
            idx = (row * width + col) * 3
            if idx + 2 < len(frame):
                # Luminance approximation
                values.append(0.299 * frame[idx] + 0.587 * frame[idx + 1] + 0.114 * frame[idx + 2])
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


class TestKenBurnsAnimation:
    """Ken Burns zoom+pan produces valid video with actual zoom effect."""

    def test_landscape_produces_correct_format(self, test_photo_landscape, tmp_path):
        """Landscape photo → 1920x1080 mp4 with correct duration and streams."""
        output = tmp_path / "ken_burns.mp4"
        _run_animator(test_photo_landscape, output, 1920, 1080, AnimationMode.KEN_BURNS)

        probe = ffprobe_json(output)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")
        assert _get_resolution(probe) == (1920, 1080)
        assert 2.5 < get_duration(probe) < 4.0

    def test_first_and_last_frames_differ_from_zoom(self, test_photo_landscape, tmp_path):
        """Zoom means first and last frames show different crop regions."""
        output = tmp_path / "ken_burns_zoom.mp4"
        _run_animator(test_photo_landscape, output, 1920, 1080, AnimationMode.KEN_BURNS)

        frame_a = _extract_frame_raw(output, "0.1", tmp_path / "frame_start.raw")
        frame_b = _extract_frame_raw(output, "2.5", tmp_path / "frame_end.raw")

        diff = _frame_difference(frame_a, frame_b)
        # Zoom should cause visible pixel differences between start and end
        assert diff > 0.001, f"Frames are too similar (diff={diff:.6f}) — zoom may not be working"


class TestFaceZoomAnimation:
    """Face zoom crops to face region — verifiable by comparing with full-frame."""

    def test_produces_correct_format(self, test_photo_landscape, tmp_path):
        """Face zoom → valid 1080p output with audio."""
        output = tmp_path / "face_zoom.mp4"
        _run_animator(
            test_photo_landscape,
            output,
            1920,
            1080,
            AnimationMode.FACE_ZOOM,
            face_bbox=(0.3, 0.2, 0.4, 0.5),
        )

        probe = ffprobe_json(output)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")
        assert _get_resolution(probe) == (1920, 1080)

    def test_face_zoom_differs_from_ken_burns(self, test_photo_4k, tmp_path):
        """Face zoom and Ken Burns should produce different frames (different crop)."""
        kb_output = tmp_path / "kb.mp4"
        fz_output = tmp_path / "fz.mp4"

        _run_animator(test_photo_4k, kb_output, 3840, 2160, AnimationMode.KEN_BURNS)
        _run_animator(
            test_photo_4k,
            fz_output,
            3840,
            2160,
            AnimationMode.FACE_ZOOM,
            face_bbox=(0.1, 0.1, 0.2, 0.2),
        )

        kb_frame = _extract_frame_raw(kb_output, "1.0", tmp_path / "kb_frame.raw")
        fz_frame = _extract_frame_raw(fz_output, "1.0", tmp_path / "fz_frame.raw")

        diff = _frame_difference(kb_frame, fz_frame)
        # Face zoom crops to a different region, so frames must differ
        assert diff > 0.01, f"Face zoom looks identical to Ken Burns (diff={diff:.6f})"

    def test_corner_face_produces_valid_output(self, test_photo_4k, tmp_path):
        """Edge face (top-left corner) doesn't produce broken output."""
        output = tmp_path / "face_corner.mp4"
        _run_animator(
            test_photo_4k,
            output,
            3840,
            2160,
            AnimationMode.FACE_ZOOM,
            face_bbox=(0.0, 0.0, 0.1, 0.1),
        )

        probe = ffprobe_json(output)
        assert has_stream(probe, "video")
        assert _get_resolution(probe) == (1920, 1080)


class TestBlurBgAnimation:
    """Blur background puts blurred image behind sharp foreground."""

    def test_portrait_produces_correct_format(self, test_photo_portrait, tmp_path):
        """Portrait photo → landscape output with correct dimensions."""
        output = tmp_path / "blur_bg.mp4"
        _run_animator(test_photo_portrait, output, 1080, 1920, AnimationMode.BLUR_BG)

        probe = ffprobe_json(output)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")
        assert _get_resolution(probe) == (1920, 1080)
        assert 2.5 < get_duration(probe) < 4.0

    def test_edges_are_blurred(self, test_photo_portrait, tmp_path):
        """The left/right edges (blur region) have lower variance than center."""
        output = tmp_path / "blur_bg_check.mp4"
        _run_animator(test_photo_portrait, output, 1080, 1920, AnimationMode.BLUR_BG)

        frame = _extract_frame_raw(output, "1.0", tmp_path / "blur_frame.raw")
        w, h = 1920, 1080

        # Left edge (blur region) — should have low variance
        edge_var = _pixel_variance_in_region(frame, w, h, 0, 200, 100, 200)
        # Center (sharp foreground) — should have higher variance
        center_var = _pixel_variance_in_region(frame, w, h, 800, 200, 100, 200)

        # Center should be sharper (higher variance) than blurred edges
        assert center_var > edge_var, (
            f"Center variance ({center_var:.1f}) should exceed edge ({edge_var:.1f}) — "
            f"blur may not be working"
        )

    def test_square_photo(self, test_photo_square, tmp_path):
        """Square photo also works with blur background."""
        output = tmp_path / "blur_bg_square.mp4"
        _run_animator(test_photo_square, output, 1080, 1080, AnimationMode.BLUR_BG)

        probe = ffprobe_json(output)
        assert has_stream(probe, "video")
        assert _get_resolution(probe) == (1920, 1080)


class TestAutoModeSelection:
    """AUTO mode selects correct animation based on photo properties."""

    def test_landscape_auto_uses_ken_burns(self, test_photo_landscape, tmp_path):
        """AUTO on landscape (no faces) → Ken Burns behavior (zoom visible)."""
        output = tmp_path / "auto_landscape.mp4"
        _run_animator(test_photo_landscape, output, 1920, 1080, AnimationMode.AUTO)

        probe = ffprobe_json(output)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")

        # Verify zoom is happening (frames differ)
        frame_a = _extract_frame_raw(output, "0.1", tmp_path / "auto_start.raw")
        frame_b = _extract_frame_raw(output, "2.5", tmp_path / "auto_end.raw")
        diff = _frame_difference(frame_a, frame_b)
        assert diff > 0.001, "AUTO landscape should produce zoom effect"

    def test_portrait_auto_uses_blur_bg(self, test_photo_portrait, tmp_path):
        """AUTO on portrait → blur background behavior (1920x1080 output)."""
        output = tmp_path / "auto_portrait.mp4"
        _run_animator(test_photo_portrait, output, 1080, 1920, AnimationMode.AUTO)

        probe = ffprobe_json(output)
        assert _get_resolution(probe) == (1920, 1080)

        # Verify blur is present (edges have low variance)
        frame = _extract_frame_raw(output, "1.0", tmp_path / "auto_portrait_frame.raw")
        edge_var = _pixel_variance_in_region(frame, 1920, 1080, 0, 200, 100, 200)
        center_var = _pixel_variance_in_region(frame, 1920, 1080, 800, 200, 100, 200)
        assert center_var > edge_var, "AUTO portrait should use blur background"
