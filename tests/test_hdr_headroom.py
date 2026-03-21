"""Tests for Apple HDR headroom extraction from HEIC MakerNote."""

from __future__ import annotations

import struct
from unittest.mock import patch

import pytest

from immich_memories.photos.animator import _extract_apple_headroom


def _build_makernote(headroom_num: int, headroom_den: int) -> bytes:
    """Build a minimal Apple MakerNote with just the HDRHeadroom tag (0x0021)."""
    header = b"Apple iOS\x00\x00\x01MM"
    entry_count = struct.pack(">H", 1)
    # WHY: value lives at byte 28 = 14 (header) + 2 (count) + 12 (entry)
    value_offset = 28
    ifd_entry = struct.pack(">HHI", 0x0021, 10, 1) + struct.pack(">I", value_offset)
    value = struct.pack(">ii", headroom_num, headroom_den)
    return header + entry_count + ifd_entry + value


class TestExtractAppleHeadroom:
    """Tests for _extract_apple_headroom pure-Python MakerNote parser."""

    def test_iphone_16_pro_headroom(self, tmp_path):
        """iPhone 16 Pro indoor photo: headroom ≈ 1.01."""
        mn = _build_makernote(1058986, 1048501)
        result = _extract_apple_headroom(mn, tmp_path / "test.heic")
        assert result == pytest.approx(1.01, abs=0.01)

    def test_iphone_13_pro_headroom(self, tmp_path):
        """iPhone 13 Pro outdoor photo: headroom ≈ 1.41."""
        mn = _build_makernote(8867, 6294)
        result = _extract_apple_headroom(mn, tmp_path / "test.heic")
        assert result == pytest.approx(1.41, abs=0.01)

    def test_iphone_13_pro_sunny_headroom(self, tmp_path):
        """iPhone 13 Pro sunny photo: headroom ≈ 1.69."""
        mn = _build_makernote(63809, 37837)
        result = _extract_apple_headroom(mn, tmp_path / "test.heic")
        assert result == pytest.approx(1.69, abs=0.01)

    def test_iphone_11_headroom(self, tmp_path):
        """iPhone 11 low-light photo: headroom ≈ 0.74."""
        mn = _build_makernote(48400, 65123)
        result = _extract_apple_headroom(mn, tmp_path / "test.heic")
        assert result == pytest.approx(0.74, abs=0.01)

    def test_non_apple_makernote_returns_default(self, tmp_path):
        """Non-Apple MakerNote returns the default headroom."""
        result = _extract_apple_headroom(b"Samsung\x00\x00\x01MM", tmp_path / "test.heic")
        assert result == 2.3

    def test_no_makernote_returns_default(self, tmp_path):
        """None MakerNote returns the default headroom."""
        result = _extract_apple_headroom(None, tmp_path / "test.heic")
        assert result == 2.3

    def test_truncated_makernote_returns_default(self, tmp_path):
        """Truncated MakerNote doesn't crash, returns default."""
        result = _extract_apple_headroom(b"Apple iOS\x00\x00\x01MM\x00", tmp_path / "test.heic")
        assert result == 2.3

    def test_zero_denominator_returns_default(self, tmp_path):
        """Zero denominator in SRATIONAL doesn't crash."""
        mn = _build_makernote(1058986, 0)
        result = _extract_apple_headroom(mn, tmp_path / "test.heic")
        assert result == 2.3

    def test_negative_headroom_returns_default(self, tmp_path):
        """Negative SRATIONAL headroom is rejected."""
        mn = _build_makernote(-1058986, 1048501)
        result = _extract_apple_headroom(mn, tmp_path / "test.heic")
        assert result == 2.3

    def test_makernote_without_tag_0x0021_falls_back_to_exiftool(self, tmp_path):
        """MakerNote missing tag 0x0021 tries exiftool fallback."""
        header = b"Apple iOS\x00\x00\x01MM"
        entry_count = struct.pack(">H", 1)
        # Tag 0x0001 instead of 0x0021
        ifd_entry = struct.pack(">HHI", 0x0001, 9, 1) + struct.pack(">I", 42)
        mn = header + entry_count + ifd_entry

        source = tmp_path / "test.heic"
        source.touch()

        # WHY: subprocess.run is the external boundary for exiftool
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "HDR Headroom                    : 1.5\n"
            result = _extract_apple_headroom(mn, source)

        assert result == pytest.approx(1.5, abs=0.01)

    def test_exiftool_not_found_returns_default(self, tmp_path):
        """When both MakerNote parsing and exiftool fail, returns default."""
        header = b"Apple iOS\x00\x00\x01MM"
        entry_count = struct.pack(">H", 1)
        ifd_entry = struct.pack(">HHI", 0x0001, 9, 1) + struct.pack(">I", 42)
        mn = header + entry_count + ifd_entry

        source = tmp_path / "test.heic"
        source.touch()

        # WHY: subprocess.run is the external boundary for exiftool
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = _extract_apple_headroom(mn, source)

        assert result == 2.3
