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
