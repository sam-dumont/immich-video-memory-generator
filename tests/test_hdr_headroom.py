"""Tests for Apple HDR headroom extraction from HEIC MakerNote."""

from __future__ import annotations

import struct
from unittest.mock import patch

import pytest

from immich_memories.photos.animator import _extract_apple_headroom, _headroom_from_stops


def _build_makernote(
    headroom_num: int, headroom_den: int, gain_num: int = 0, gain_den: int = 1
) -> bytes:
    """Apple MakerNote carrying both HDR tags: 0x0021 HDRHeadroom, 0x0030 HDRGain.

    Both are needed. Apple derives the headroom from the pair through a
    piecewise function, and 0x0021 alone does not determine it -- a file can
    read 1.01 there and still declare a 5.955x gain map.
    """
    header = b"Apple iOS\x00\x00\x01MM"
    entry_count = struct.pack(">H", 2)
    # WHY: values follow the IFD -- 14 (header) + 2 (count) + 2*12 (entries) = 40
    first_value = 40
    entries = struct.pack(">HHI", 0x0021, 10, 1) + struct.pack(">I", first_value)
    entries += struct.pack(">HHI", 0x0030, 10, 1) + struct.pack(">I", first_value + 8)
    values = struct.pack(">ii", headroom_num, headroom_den)
    values += struct.pack(">ii", gain_num, gain_den)
    return header + entry_count + entries + values


class TestExtractAppleHeadroom:
    """Tests for _extract_apple_headroom pure-Python MakerNote parser."""

    def test_the_headroom_needs_both_tags_not_just_the_first(self, tmp_path):
        """0x0021 reads 1.01 on a photograph whose gain map reaches 5.955x.

        Measured on a real iPhone 16 Pro file. Reading 0x0021 alone and calling
        it stops gave 2.01x -- about a third of the range the picture carries,
        which is why HDR photographs came out flat.
        """
        # maker33 = 1.01, maker48 = 0.00608 -> stops = -70*0.00608 + 3.0
        mn = _build_makernote(101, 100, 608, 100000)
        result = _extract_apple_headroom(mn, tmp_path / "test.heic")
        assert result == pytest.approx(5.956, abs=0.01)

    @pytest.mark.parametrize(
        ("maker33", "maker48", "expected"),
        [
            # Verified against each file's own XMP:HDRGainMapHeadroom, to four
            # decimal places, on 11 iPhone 16 Pro photographs.
            (1.01, 0.00608, 5.9562),
            (1.01, 0.63262, 4.3209),
            (1.01, 1.63486, 3.5007),
            (1.01, 0.00301, 6.9129),
            (1.01, 0.34346, 4.5915),
            # maker33 < 1.0 takes the other pair of branches. An older phone in
            # low light lands here, and it is the branch that can return a
            # headroom near 1.0 -- a photograph with almost nothing to expand.
            (0.74, 0.005, 3.2490),
            (0.74, 1.5, 2.7311),
        ],
    )
    def test_apple_piecewise_headroom(self, maker33, maker48, expected):
        """Both branches of Apple's published mapping, on real-shaped inputs."""
        assert _headroom_from_stops(maker33, maker48) == pytest.approx(expected, abs=0.01)

    def test_headroom_never_falls_below_one(self):
        """`max(stops, 0)` in the spec: a gain map may not darken the picture."""
        assert _headroom_from_stops(1.01, 100.0) == pytest.approx(1.0)
        assert _headroom_from_stops(0.5, 100.0) == pytest.approx(1.0)

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
        mn = _build_makernote(1058986, 0, 608, 100000)
        result = _extract_apple_headroom(mn, tmp_path / "test.heic")
        assert result == 2.3

    def test_a_negative_headroom_tag_still_computes(self, tmp_path):
        """A negative 0x0021 is meaningful now: it selects the maker33 < 1 branch.

        It used to be rejected, because the raw value was being returned AS the
        headroom and a negative one is nonsense. It is an input to the mapping
        now, and the mapping's own `max(stops, 0)` is what keeps the result
        sane, so there is nothing left to reject here.
        """
        mn = _build_makernote(-1058986, 1048501, 608, 100000)
        result = _extract_apple_headroom(mn, tmp_path / "test.heic")
        assert result == pytest.approx(_headroom_from_stops(-1.01, 0.00608), abs=0.01)

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
