"""Unit tests for frame preview encoding."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image


class TestEncodePreviewJpeg:
    def test_sdr_frame_produces_valid_jpeg(self) -> None:
        """An RGB uint8 frame should encode to valid JPEG bytes."""
        from immich_memories.processing.frame_preview import _encode_preview_jpeg

        # Red/green/blue/white quadrants — known color pattern
        frame = np.zeros((200, 200, 3), dtype=np.uint8)
        frame[:100, :100] = [255, 0, 0]
        frame[:100, 100:] = [0, 255, 0]
        frame[100:, :100] = [0, 0, 255]
        frame[100:, 100:] = [255, 255, 255]

        jpeg_bytes = _encode_preview_jpeg(frame)

        # JPEG SOI marker
        assert jpeg_bytes[:2] == b"\xff\xd8"
        # Decodable
        img = Image.open(io.BytesIO(jpeg_bytes))
        assert img.size == (200, 200)
        # Pixel check — red quadrant center (JPEG lossy, allow ±10)
        r, g, b = img.getpixel((50, 50))
        assert r > 240 and g < 15 and b < 15

    def test_sdr_frame_pixel_accuracy_all_quadrants(self) -> None:
        """All four quadrant colors should survive JPEG round-trip."""
        from immich_memories.processing.frame_preview import _encode_preview_jpeg

        frame = np.zeros((200, 200, 3), dtype=np.uint8)
        frame[:100, :100] = [255, 0, 0]
        frame[:100, 100:] = [0, 255, 0]
        frame[100:, :100] = [0, 0, 255]
        frame[100:, 100:] = [255, 255, 255]

        jpeg_bytes = _encode_preview_jpeg(frame)
        img = Image.open(io.BytesIO(jpeg_bytes))

        # Check center of each quadrant (avoid edges where JPEG bleeds)
        centers = {
            "red": (50, 50, 255, 0, 0),
            "green": (150, 50, 0, 255, 0),
            "blue": (50, 150, 0, 0, 255),
            "white": (150, 150, 255, 255, 255),
        }
        for name, (x, y, er, eg, eb) in centers.items():
            r, g, b = img.getpixel((x, y))
            assert abs(r - er) < 20, f"{name} red channel: {r} vs {er}"
            assert abs(g - eg) < 20, f"{name} green channel: {g} vs {eg}"
            assert abs(b - eb) < 20, f"{name} blue channel: {b} vs {eb}"

    def test_downscales_4k_to_720p(self) -> None:
        """A 4K frame should be downscaled to 720p height, preserving aspect ratio."""
        from immich_memories.processing.frame_preview import _encode_preview_jpeg

        frame = np.zeros((2160, 3840, 3), dtype=np.uint8)
        frame[:] = [128, 64, 192]

        jpeg_bytes = _encode_preview_jpeg(frame)
        img = Image.open(io.BytesIO(jpeg_bytes))

        assert img.size == (1280, 720)

    def test_no_upscale_small_frame(self) -> None:
        """A frame smaller than max_height should NOT be upscaled."""
        from immich_memories.processing.frame_preview import _encode_preview_jpeg

        frame = np.full((360, 640, 3), 100, dtype=np.uint8)

        jpeg_bytes = _encode_preview_jpeg(frame)
        img = Image.open(io.BytesIO(jpeg_bytes))

        assert img.size == (640, 360)


def _make_yuv420p10le_buffer(
    y_val: int, u_val: int, v_val: int, width: int, height: int
) -> np.ndarray:
    """Create a flat YUV420p10le buffer with uniform color.

    Layout: Y plane (width*height) + U plane (width/2 * height/2) + V plane (width/2 * height/2).
    All values are 10-bit (0-1023) stored as uint16.
    """
    y_size = width * height
    uv_size = (width // 2) * (height // 2)
    buf = np.empty(y_size + 2 * uv_size, dtype=np.uint16)
    buf[:y_size] = y_val
    buf[y_size : y_size + uv_size] = u_val
    buf[y_size + uv_size :] = v_val
    return buf


class TestToRgb8:
    def test_pure_white_converts_to_near_white_rgb(self) -> None:
        """BT.2020 Y=940 U=512 V=512 (10-bit white) -> near-white RGB."""
        from immich_memories.processing.frame_preview import _to_rgb8

        buf = _make_yuv420p10le_buffer(940, 512, 512, 160, 120)
        rgb = _to_rgb8(buf, height=120, width=160)

        assert rgb.shape == (120, 160, 3)
        assert rgb.dtype == np.uint8
        center = rgb[60, 80]
        assert all(c > 200 for c in center), f"Expected near-white, got {center}"

    def test_pure_black_converts_to_near_black_rgb(self) -> None:
        """BT.2020 Y=64 U=512 V=512 (10-bit black) -> near-black RGB."""
        from immich_memories.processing.frame_preview import _to_rgb8

        buf = _make_yuv420p10le_buffer(64, 512, 512, 160, 120)
        rgb = _to_rgb8(buf, height=120, width=160)

        center = rgb[60, 80]
        assert all(c < 30 for c in center), f"Expected near-black, got {center}"

    def test_chroma_upsampling_spatial_correctness(self) -> None:
        """U/V planes are half-resolution — verify upsampling fills all pixels."""
        from immich_memories.processing.frame_preview import _to_rgb8

        width, height = 160, 120
        y_size = width * height
        uv_w, uv_h = width // 2, height // 2
        uv_size = uv_w * uv_h

        buf = np.empty(y_size + 2 * uv_size, dtype=np.uint16)
        buf[:y_size] = 502  # mid-gray Y

        # Split U plane: left half = 300 (blue-ish), right half = 700 (red-ish)
        u_plane = buf[y_size : y_size + uv_size].reshape(uv_h, uv_w)
        u_plane[:, : uv_w // 2] = 300
        u_plane[:, uv_w // 2 :] = 700
        buf[y_size + uv_size :] = 512  # neutral V

        rgb = _to_rgb8(buf, height=height, width=width)

        # Left quarter and right quarter should have visibly different colors
        left_avg = rgb[:, :40, :].mean(axis=(0, 1))
        right_avg = rgb[:, 120:, :].mean(axis=(0, 1))
        diff = np.abs(left_avg - right_avg).max()
        assert diff > 20, f"Chroma split not visible after upsampling: diff={diff}"


class TestHdrJpegRoundTrip:
    def test_hdr_frame_produces_valid_jpeg(self) -> None:
        """Full pipeline: synthetic HDR buffer -> _to_rgb8 -> _encode_preview_jpeg -> valid JPEG."""
        from immich_memories.processing.frame_preview import (
            _encode_preview_jpeg,
            _to_rgb8,
        )

        width, height = 160, 120
        buf = _make_yuv420p10le_buffer(700, 512, 512, width, height)

        rgb = _to_rgb8(buf, height=height, width=width)
        jpeg_bytes = _encode_preview_jpeg(rgb)

        assert jpeg_bytes[:2] == b"\xff\xd8"
        img = Image.open(io.BytesIO(jpeg_bytes))
        assert img.size == (width, height)
        # Should be a grayish-bright pixel (neutral chroma)
        r, g, b = img.getpixel((80, 60))
        assert r > 100 and g > 100 and b > 100
