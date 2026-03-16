"""Tests for HDR detection and conversion utilities."""

from __future__ import annotations

from unittest.mock import patch


class TestDetectColorPrimaries:
    """Test detection of source color primaries."""

    def test_detects_bt709(self):
        from immich_memories.processing.hdr_utilities import _detect_color_primaries

        # WHY: mock subprocess.run — ffprobe is an external binary; tests verify JSON parsing logic
        with patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = '{"streams": [{"color_primaries": "bt709"}]}'
            result = _detect_color_primaries("/fake/path.mp4")

        assert result == "bt709"

    def test_detects_display_p3(self):
        from immich_memories.processing.hdr_utilities import _detect_color_primaries

        with patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = '{"streams": [{"color_primaries": "smpte432"}]}'
            result = _detect_color_primaries("/fake/path.mp4")

        assert result == "smpte432"

    def test_returns_none_on_failure(self):
        from immich_memories.processing.hdr_utilities import _detect_color_primaries

        # WHY: mock subprocess.run raising — verify graceful fallback when ffprobe is missing
        with patch(
            "immich_memories.processing.hdr_utilities.subprocess.run",
            side_effect=Exception("ffprobe not found"),
        ):
            result = _detect_color_primaries("/fake/path.mp4")

        assert result is None

    def test_returns_none_for_unknown(self):
        from immich_memories.processing.hdr_utilities import _detect_color_primaries

        with patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = '{"streams": [{"color_primaries": "unknown"}]}'
            result = _detect_color_primaries("/fake/path.mp4")

        assert result == "unknown"


class TestHdrConversionFilter:
    """Test that SDR→HLG conversion uses correct source primaries."""

    def test_sdr_to_hlg_default_bt709_primaries(self):
        """Default SDR→HLG conversion should use bt709 primaries."""
        from immich_memories.processing.hdr_utilities import _get_hdr_conversion_filter

        # WHY: mock subprocess.run — filter builder checks zscale availability via ffmpeg
        with patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "zscale"
            result = _get_hdr_conversion_filter(None, "hlg")

        assert "primariesin=bt709" in result
        assert "transfer=arib-std-b67" in result

    def test_sdr_to_hlg_with_p3_primaries(self):
        """SDR→HLG with Display P3 source should use smpte432 primariesin."""
        from immich_memories.processing.hdr_utilities import _get_hdr_conversion_filter

        with patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "zscale"
            result = _get_hdr_conversion_filter(None, "hlg", source_primaries="smpte432")

        assert "primariesin=smpte432" in result
        assert "primariesin=bt709" not in result

    def test_sdr_to_pq_with_p3_primaries(self):
        """SDR→PQ with Display P3 source should use smpte432 primariesin."""
        from immich_memories.processing.hdr_utilities import _get_hdr_conversion_filter

        with patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "zscale"
            result = _get_hdr_conversion_filter(None, "pq", source_primaries="smpte432")

        assert "primariesin=smpte432" in result
        assert "transfer=smpte2084" in result

    def test_hlg_to_pq_ignores_source_primaries(self):
        """HDR→HDR conversion already uses bt2020, source_primaries not needed."""
        from immich_memories.processing.hdr_utilities import _get_hdr_conversion_filter

        with patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "zscale"
            result = _get_hdr_conversion_filter("hlg", "pq", source_primaries="smpte432")

        # HDR→HDR uses bt2020 primaries on both sides
        assert "primariesin=bt2020" in result
