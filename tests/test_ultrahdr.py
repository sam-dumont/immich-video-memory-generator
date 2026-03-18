"""Tests for Ultra HDR JPEG gain map extraction and application."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from immich_memories.photos.ultrahdr import (
    GainMapMetadata,
    apply_gain_map,
    extract_gain_map,
    is_ultra_hdr_jpeg,
    parse_hdrgm_metadata,
)

SAMPLE = Path("tests/fixtures/hdr_samples/ultrahdr_colorful_daisies.jpg")
SAMPLE_GRAY = Path("tests/fixtures/hdr_samples/ultrahdr_gray_chart.jpg")


def _lfs_available(path: Path) -> bool:
    """Check if a git LFS file is the real file (not a pointer)."""
    if not path.exists():
        return False
    # LFS pointers start with "version https://git-lfs.github.com"
    with path.open("rb") as f:
        header = f.read(20)
    return header[:2] == b"\xff\xd8"  # JPEG magic bytes


requires_lfs = pytest.mark.skipif(
    not _lfs_available(SAMPLE), reason="Git LFS files not checked out"
)


@requires_lfs
class TestIsUltraHdrJpeg:
    """Tests for Ultra HDR detection."""

    def test_detects_ultra_hdr_sample(self):
        """Ultra HDR JPEG with gain map is detected."""
        assert is_ultra_hdr_jpeg(SAMPLE) is True

    def test_rejects_regular_jpeg(self, tmp_path):
        """Regular JPEG without gain map is not detected."""
        from PIL import Image

        regular = tmp_path / "regular.jpg"
        Image.new("RGB", (100, 100), "red").save(regular)
        assert is_ultra_hdr_jpeg(regular) is False


@requires_lfs
class TestExtractGainMap:
    """Tests for MPF gain map extraction."""

    def test_extracts_primary_and_gain_map(self):
        """Extracts both primary SDR image and gain map from MPF container."""
        primary, gain_map = extract_gain_map(SAMPLE)
        assert primary.size[0] > 0
        assert primary.size[1] > 0
        assert gain_map.size[0] > 0
        assert gain_map.size[1] > 0

    def test_gain_map_is_smaller_than_primary(self):
        """Gain map is typically lower resolution than primary."""
        primary, gain_map = extract_gain_map(SAMPLE)
        primary_pixels = primary.size[0] * primary.size[1]
        gm_pixels = gain_map.size[0] * gain_map.size[1]
        assert gm_pixels <= primary_pixels

    def test_raises_for_regular_jpeg(self, tmp_path):
        """Regular JPEG without MPF secondary image raises ValueError."""
        from PIL import Image

        regular = tmp_path / "regular.jpg"
        Image.new("RGB", (100, 100), "red").save(regular)
        with pytest.raises(ValueError, match="No secondary image"):
            extract_gain_map(regular)


class TestParseMetadata:
    """Tests for hdrgm XMP metadata parsing."""

    def test_returns_defaults_when_fields_absent(self):
        """Samples with only Version=1.0 get default metadata values."""
        meta = parse_hdrgm_metadata(SAMPLE)
        # ISO 21496-1 defaults
        assert meta.gain_map_min == [0.0]
        assert meta.gamma == [1.0]
        assert meta.offset_sdr == [1 / 64]
        assert meta.offset_hdr == [1 / 64]
        assert meta.base_rendition_is_hdr is False

    def test_dataclass_defaults(self):
        """GainMapMetadata has correct ISO 21496-1 default values."""
        meta = GainMapMetadata()
        assert meta.hdr_capacity_min == 0.0
        assert meta.hdr_capacity_max == 1.0
        assert meta.gain_map_max == [1.0]


class TestApplyGainMap:
    """Tests for gain map application to reconstruct HDR."""

    def test_hdr_exceeds_sdr_range(self):
        """Reconstructed HDR values exceed 1.0 (headroom applied)."""
        sdr = np.full((10, 10, 3), 0.8, dtype=np.float32)
        gain_map = np.full((10, 10), 1.0, dtype=np.float32)
        meta = GainMapMetadata(hdr_capacity_max=1.0)
        hdr = apply_gain_map(sdr, gain_map, meta)
        assert hdr.max() > 1.0

    def test_zero_gain_map_preserves_sdr(self):
        """Gain map of all zeros produces values close to SDR."""
        sdr = np.full((10, 10, 3), 0.5, dtype=np.float32)
        gain_map = np.zeros((10, 10), dtype=np.float32)
        meta = GainMapMetadata(gain_map_min=[0.0], gain_map_max=[1.0])
        hdr = apply_gain_map(sdr, gain_map, meta)
        # With offset_sdr=1/64 and gain_min=0, min boost is 2^0=1.0
        # Result should be close to sdr + offset - offset = sdr
        assert abs(hdr.mean() - sdr.mean()) < 0.1

    def test_upsamples_gain_map(self):
        """Gain map at lower resolution is upsampled to match SDR."""
        sdr = np.full((100, 100, 3), 0.5, dtype=np.float32)
        gain_map = np.full((25, 25), 0.5, dtype=np.float32)
        meta = GainMapMetadata()
        hdr = apply_gain_map(sdr, gain_map, meta)
        assert hdr.shape == (100, 100, 3)

    def test_real_sample_produces_valid_output(self):
        """Real Ultra HDR sample produces sensible HDR output."""
        primary, gm = extract_gain_map(SAMPLE)
        sdr = np.array(primary.convert("RGB"), dtype=np.float32) / 255.0
        gm_arr = np.array(gm.convert("L"), dtype=np.float32) / 255.0
        meta = parse_hdrgm_metadata(SAMPLE)
        hdr = apply_gain_map(sdr, gm_arr, meta)
        assert hdr.shape[2] == 3
        assert hdr.min() >= 0.0
        assert hdr.max() > 0.5  # Not all-black
