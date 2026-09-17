"""Tests for ClipExtractor and ClipSegment."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.processing.clips import (
    ClipExtractor,
    ClipSegment,
    _build_clip_output_path,
)

# WHY: ClipExtractor requires a Config for hardware/encoder settings during re-encode.
# Tests don't exercise real encoding, so a mock is sufficient.
_MOCK_CONFIG = MagicMock()


class TestClipSegment:
    """Tests for ClipSegment dataclass."""

    def test_duration(self):
        """Duration is end - start."""
        seg = ClipSegment(source_path=Path("/v.mp4"), start_time=2.0, end_time=7.0, asset_id="a")
        assert seg.duration == 5.0

    def test_zero_duration(self):
        """Same start/end gives zero duration."""
        seg = ClipSegment(source_path=Path("/v.mp4"), start_time=3.0, end_time=3.0, asset_id="a")
        assert seg.duration == 0.0

    def test_to_dict_without_output(self):
        """to_dict with no output_path returns None for it."""
        seg = ClipSegment(source_path=Path("/v.mp4"), start_time=0, end_time=5, asset_id="a")
        d = seg.to_dict()
        assert d["asset_id"] == "a"
        assert d["output_path"] is None

    def test_to_dict_with_output(self):
        """to_dict includes output_path string when set."""
        seg = ClipSegment(
            source_path=Path("/v.mp4"),
            start_time=0,
            end_time=5,
            asset_id="a",
            output_path=Path("/out.mp4"),
        )
        assert seg.to_dict()["output_path"] == "/out.mp4"

    def test_default_score(self):
        """Default score is 0.0."""
        seg = ClipSegment(source_path=Path("/v.mp4"), start_time=0, end_time=1, asset_id="a")
        assert seg.score == 0.0


class TestClipExtractorInit:
    """ClipExtractor initialization."""

    def test_creates_output_dir(self, tmp_path):
        """Output dir is created if it doesn't exist."""
        out = tmp_path / "clips"
        extractor = ClipExtractor(output_dir=out, config=_MOCK_CONFIG)
        assert out.exists()
        assert extractor.output_dir == out

    def test_default_output_dir_is_private_to_this_user(self):
        """Clips are extracted to a scratch dir; shared /tmp would let another
        user on the host read them, or pre-place a symlink we write through."""
        import os
        import stat

        extractor = ClipExtractor(config=_MOCK_CONFIG)
        info = extractor.output_dir.stat()

        assert info.st_uid == os.getuid()
        assert stat.S_IMODE(info.st_mode) == 0o700


class TestClipExtractorExtract:
    """Tests for extract method."""

    def test_missing_source_raises(self, tmp_path):
        """FileNotFoundError for non-existent source."""
        extractor = ClipExtractor(output_dir=tmp_path, config=_MOCK_CONFIG)
        seg = ClipSegment(
            source_path=Path("/nonexistent/video.mp4"),
            start_time=0,
            end_time=5,
            asset_id="a",
        )
        with pytest.raises(FileNotFoundError, match="Source video not found"):
            extractor.extract(seg)

    def test_cached_clip_returned(self, tmp_path):
        """Existing output file is returned without re-extraction."""
        extractor = ClipExtractor(output_dir=tmp_path, config=_MOCK_CONFIG)
        source = tmp_path / "source.mp4"
        source.write_bytes(b"\x00" * 100)

        seg = ClipSegment(source_path=source, start_time=0, end_time=5, asset_id="test")
        # Pre-create the expected output
        expected = tmp_path / "test_0.0_5.0.mp4"
        expected.write_bytes(b"\x00" * 50)

        result = extractor.extract(seg)
        assert result == expected
        assert seg.output_path == expected

    # WHY: _extract_copy runs FFmpeg stream copy — avoid real encoding in unit tests
    @patch("immich_memories.processing.clips.ClipExtractor._extract_copy")
    def test_extract_calls_copy_by_default(self, mock_copy, tmp_path):
        """Default extraction uses stream copy."""
        extractor = ClipExtractor(output_dir=tmp_path, config=_MOCK_CONFIG)
        source = tmp_path / "source.mp4"
        source.write_bytes(b"\x00" * 100)

        seg = ClipSegment(source_path=source, start_time=0, end_time=5, asset_id="test")
        extractor.extract(seg)
        mock_copy.assert_called_once()

    # WHY: _extract_with_reencode runs FFmpeg re-encode — avoid real encoding in unit tests
    @patch("immich_memories.processing.clips.ClipExtractor._extract_with_reencode")
    def test_extract_with_reencode(self, mock_reencode, tmp_path):
        """reencode=True uses re-encode path."""
        extractor = ClipExtractor(output_dir=tmp_path, config=_MOCK_CONFIG)
        source = tmp_path / "source.mp4"
        source.write_bytes(b"\x00" * 100)

        seg = ClipSegment(source_path=source, start_time=0, end_time=5, asset_id="test")
        extractor.extract(seg, reencode=True)
        mock_reencode.assert_called_once()


class TestCopyRefusingContainer:
    """A cut only copies streams the container it picks can actually carry."""

    # WHY: ffprobe reads the source codec; WHY: the route runs FFmpeg for real.
    @patch("immich_memories.processing.clips.ClipExtractor._extract_with_reencode")
    @patch("immich_memories.processing.clips.ClipExtractor._extract_copy")
    @patch("immich_memories.processing.clips.get_video_codec", return_value="vp9")
    def test_quicktime_refusing_codec_goes_through_the_encoder(
        self, _codec, mock_copy, mock_reencode, tmp_path
    ):
        """VP9 cannot be muxed into .mov, so the clip is re-encoded into .mp4 instead."""
        source = tmp_path / "phone.MOV"
        source.write_bytes(b"\x00" * 100)
        extractor = ClipExtractor(output_dir=tmp_path, config=_MOCK_CONFIG)

        seg = ClipSegment(source_path=source, start_time=1.0, end_time=4.0, asset_id="test")
        extractor.extract(seg)

        mock_copy.assert_not_called()
        mock_reencode.assert_called_once()
        planned_segment, planned_output, _progress = mock_reencode.call_args.args
        assert planned_output.suffix == ".mp4"
        assert (planned_segment.start_time, planned_segment.end_time) == (1.0, 4.0)

    # WHY: ffprobe reads the source codec; WHY: the route runs FFmpeg for real.
    @patch("immich_memories.processing.clips.ClipExtractor._extract_copy")
    @patch("immich_memories.processing.clips.get_video_codec", return_value="hevc")
    def test_a_copyable_mov_codec_still_copies_into_quicktime(self, _codec, mock_copy, tmp_path):
        """HEVC, H.264 and ProRes sources keep the lossless .mov cut they had."""
        source = tmp_path / "camera.MOV"
        source.write_bytes(b"\x00" * 100)
        extractor = ClipExtractor(output_dir=tmp_path, config=_MOCK_CONFIG)

        extractor.extract(ClipSegment(source, 1.0, 4.0, "test"))

        mock_copy.assert_called_once()
        assert mock_copy.call_args.args[1].suffix == ".mov"


class TestBuildClipOutputPath:
    """Tests for _build_clip_output_path."""

    def test_no_buffer_no_reencode(self):
        """Output path has no buffer/encode suffix."""
        path = _build_clip_output_path(Path("/v.mp4"), 0.0, 5.0, False, False, False)
        assert "_b" not in path.name
        assert "_enc" not in path.name
        assert path.suffix == ".mp4"

    def test_buffer_suffix(self):
        """Buffer flags appear in filename."""
        path = _build_clip_output_path(Path("/v.mp4"), 0.0, 5.0, True, True, False)
        assert "_b11" in path.name

    def test_reencode_suffix(self):
        """Reencode flag appears in filename."""
        path = _build_clip_output_path(Path("/v.mp4"), 0.0, 5.0, False, False, True)
        assert "_enc" in path.name

    def test_different_sources_different_paths(self):
        """Different source paths produce different output paths."""
        p1 = _build_clip_output_path(Path("/a.mp4"), 0.0, 5.0, False, False, False)
        p2 = _build_clip_output_path(Path("/b.mp4"), 0.0, 5.0, False, False, False)
        assert p1 != p2
