"""Tests for HDR detection and conversion utilities."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch


class TestDetectHdrType:
    """HDR detection must cross-check transfer function AND color primaries."""

    def _mock_ffprobe(self, color_transfer: str, color_primaries: str = "bt2020"):
        """Build a mock for ffprobe that returns both transfer and primaries."""
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = json.dumps(
            {
                "streams": [
                    {
                        "color_transfer": color_transfer,
                        "color_primaries": color_primaries,
                    }
                ]
            }
        )
        return mock

    def _make_fake_video(self, tmp_path):
        """Create an empty file that passes validate_video_path."""
        p = tmp_path / "test.mp4"
        p.write_bytes(b"\x00")
        return p

    def test_hlg_with_bt2020_primaries_is_hdr(self, tmp_path):
        """Real iPhone HDR: HLG transfer + bt2020 primaries = HDR."""
        from immich_memories.processing.hdr_utilities import _detect_hdr_type

        video = self._make_fake_video(tmp_path)
        # WHY: mock subprocess.run — ffprobe is an external binary
        with patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run:
            mock_run.return_value = self._mock_ffprobe("arib-std-b67", "bt2020")
            result = _detect_hdr_type(video)

        assert result == "hlg"

    def test_pq_with_bt2020_primaries_is_hdr(self, tmp_path):
        """Android HDR10: PQ transfer + bt2020 primaries = HDR."""
        from immich_memories.processing.hdr_utilities import _detect_hdr_type

        video = self._make_fake_video(tmp_path)
        with patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run:
            mock_run.return_value = self._mock_ffprobe("smpte2084", "bt2020")
            result = _detect_hdr_type(video)

        assert result == "pq"

    def test_hlg_transfer_with_bt709_primaries_is_sdr(self, tmp_path):
        """Apple Shared Album: HLG tag but bt709 primaries → actually SDR."""
        from immich_memories.processing.hdr_utilities import _detect_hdr_type

        video = self._make_fake_video(tmp_path)
        with patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run:
            mock_run.return_value = self._mock_ffprobe("arib-std-b67", "bt709")
            result = _detect_hdr_type(video)

        assert result is None  # Should be treated as SDR

    def test_bt2020_transfer_with_bt709_primaries_is_sdr(self, tmp_path):
        """bt2020-10 transfer but bt709 primaries → mislabeled, treat as SDR."""
        from immich_memories.processing.hdr_utilities import _detect_hdr_type

        video = self._make_fake_video(tmp_path)
        with patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run:
            mock_run.return_value = self._mock_ffprobe("bt2020-10", "bt709")
            result = _detect_hdr_type(video)

        assert result is None

    def test_bt709_transfer_is_always_sdr(self, tmp_path):
        """Standard bt709 transfer = always SDR regardless of primaries."""
        from immich_memories.processing.hdr_utilities import _detect_hdr_type

        video = self._make_fake_video(tmp_path)
        with patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run:
            mock_run.return_value = self._mock_ffprobe("bt709", "bt2020")
            result = _detect_hdr_type(video)

        assert result is None

    def test_missing_primaries_falls_back_to_sdr(self, tmp_path):
        """If primaries field is missing, don't assume HDR."""
        from immich_memories.processing.hdr_utilities import _detect_hdr_type

        video = self._make_fake_video(tmp_path)
        with patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run:
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = json.dumps({"streams": [{"color_transfer": "arib-std-b67"}]})
            mock_run.return_value = mock
            result = _detect_hdr_type(video)

        assert result is None

    def test_ffprobe_failure_returns_none(self, tmp_path):
        from immich_memories.processing.hdr_utilities import _detect_hdr_type

        video = self._make_fake_video(tmp_path)
        # WHY: mock subprocess.run raising — verify graceful fallback when ffprobe is missing
        with patch(
            "immich_memories.processing.hdr_utilities.subprocess.run",
            side_effect=FileNotFoundError("ffprobe not found"),
        ):
            result = _detect_hdr_type(video)

        assert result is None


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
            side_effect=FileNotFoundError("ffprobe not found"),
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
    """Test that SDR→HLG conversion uses correct zscale parameters."""

    def test_sdr_to_hlg_default_bt709_primaries(self):
        """Default SDR→HLG conversion should use bt709 primaries."""
        from immich_memories.processing.hdr_utilities import _get_hdr_conversion_filter

        # WHY: mock subprocess.run — filter builder checks zscale availability via ffmpeg
        with patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "zscale"
            result = _get_hdr_conversion_filter(None, "hlg")

        assert "pin=bt709" in result
        assert "t=arib-std-b67" in result
        assert "npl=203" in result
        assert "agamma=false" in result

    def test_sdr_to_hlg_with_p3_primaries(self):
        """SDR→HLG with Display P3 source should use smpte432 primariesin."""
        from immich_memories.processing.hdr_utilities import _get_hdr_conversion_filter

        with patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "zscale"
            result = _get_hdr_conversion_filter(None, "hlg", source_primaries="smpte432")

        assert "pin=smpte432" in result
        assert "pin=bt709" not in result

    def test_sdr_to_pq_with_p3_primaries(self):
        """SDR→PQ with Display P3 source should use smpte432 primariesin."""
        from immich_memories.processing.hdr_utilities import _get_hdr_conversion_filter

        with patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "zscale"
            result = _get_hdr_conversion_filter(None, "pq", source_primaries="smpte432")

        assert "pin=smpte432" in result
        assert "t=smpte2084" in result
        assert "npl=203" in result

    def test_hlg_to_pq_uses_bt2020_primaries(self):
        """HDR→HDR conversion already uses bt2020, source_primaries not needed."""
        from immich_memories.processing.hdr_utilities import _get_hdr_conversion_filter

        with patch("immich_memories.processing.hdr_utilities.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "zscale"
            result = _get_hdr_conversion_filter("hlg", "pq", source_primaries="smpte432")

        assert "pin=bt2020" in result
        assert "npl=203" in result
