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

    def test_default_output_dir(self):
        """Default output dir uses tempdir."""
        extractor = ClipExtractor(config=_MOCK_CONFIG)
        assert "immich_memories" in str(extractor.output_dir)


class TestClipExtractorCleanup:
    """Tests for cleanup_old_clips."""

    def test_cleanup_removes_old_files(self, tmp_path):
        """Clips older than threshold are removed."""
        extractor = ClipExtractor(output_dir=tmp_path, config=_MOCK_CONFIG)
        old_file = tmp_path / "old.mp4"
        old_file.write_bytes(b"\x00" * 10)
        # Set mtime to 2 days ago
        import os

        old_mtime = old_file.stat().st_mtime - 48 * 3600
        os.utime(old_file, (old_mtime, old_mtime))

        removed = extractor.cleanup_old_clips(max_age_hours=24)
        assert removed == 1
        assert not old_file.exists()

    def test_cleanup_keeps_recent_files(self, tmp_path):
        """Recent clips are not removed."""
        extractor = ClipExtractor(output_dir=tmp_path, config=_MOCK_CONFIG)
        recent = tmp_path / "recent.mp4"
        recent.write_bytes(b"\x00" * 10)

        removed = extractor.cleanup_old_clips(max_age_hours=24)
        assert removed == 0
        assert recent.exists()

    def test_cleanup_ignores_non_mp4(self, tmp_path):
        """Only .mp4 files are cleaned up."""
        extractor = ClipExtractor(output_dir=tmp_path, config=_MOCK_CONFIG)
        txt = tmp_path / "notes.txt"
        txt.write_text("keep me")
        import os

        old_mtime = txt.stat().st_mtime - 48 * 3600
        os.utime(txt, (old_mtime, old_mtime))

        removed = extractor.cleanup_old_clips(max_age_hours=24)
        assert removed == 0
        assert txt.exists()

    def test_cleanup_nonexistent_dir_returns_zero(self, tmp_path):
        """Non-existent output_dir returns 0."""
        extractor = ClipExtractor(output_dir=tmp_path / "gone", config=_MOCK_CONFIG)
        extractor.output_dir = tmp_path / "gone"  # bypass mkdir
        assert extractor.cleanup_old_clips() == 0


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


class TestClipExtractorBatchExtract:
    """Tests for batch_extract method."""

    # WHY: extract() calls FFmpeg — isolate batch orchestration from real extraction
    @patch("immich_memories.processing.clips.ClipExtractor.extract")
    # WHY: cleanup_old_clips() deletes files on disk — prevent side effects in tests
    @patch("immich_memories.processing.clips.ClipExtractor.cleanup_old_clips")
    def test_batch_returns_all_successes(self, mock_cleanup, mock_extract, tmp_path):
        """All successful extracts are returned."""
        extractor = ClipExtractor(output_dir=tmp_path, config=_MOCK_CONFIG)
        source = tmp_path / "src.mp4"
        source.write_bytes(b"\x00")

        mock_extract.side_effect = [
            tmp_path / "a.mp4",
            tmp_path / "b.mp4",
        ]
        segs = [
            ClipSegment(source_path=source, start_time=0, end_time=5, asset_id="a"),
            ClipSegment(source_path=source, start_time=5, end_time=10, asset_id="b"),
        ]
        results = extractor.batch_extract(segs)
        assert len(results) == 2

    # WHY: extract() calls FFmpeg — simulate failure to test batch error handling
    @patch("immich_memories.processing.clips.ClipExtractor.extract")
    # WHY: cleanup_old_clips() deletes files on disk — prevent side effects in tests
    @patch("immich_memories.processing.clips.ClipExtractor.cleanup_old_clips")
    def test_batch_continues_on_failure(self, mock_cleanup, mock_extract, tmp_path):
        """Failures are logged and skipped; successes returned."""
        extractor = ClipExtractor(output_dir=tmp_path, config=_MOCK_CONFIG)
        source = tmp_path / "src.mp4"
        source.write_bytes(b"\x00")

        mock_extract.side_effect = [
            RuntimeError("ffmpeg crash"),
            tmp_path / "b.mp4",
        ]
        segs = [
            ClipSegment(source_path=source, start_time=0, end_time=5, asset_id="a"),
            ClipSegment(source_path=source, start_time=5, end_time=10, asset_id="b"),
        ]
        results = extractor.batch_extract(segs)
        assert len(results) == 1

    # WHY: extract() calls FFmpeg — simulate all-failures scenario
    @patch("immich_memories.processing.clips.ClipExtractor.extract")
    # WHY: cleanup_old_clips() deletes files on disk — prevent side effects in tests
    @patch("immich_memories.processing.clips.ClipExtractor.cleanup_old_clips")
    def test_batch_all_failures_returns_empty(self, mock_cleanup, mock_extract, tmp_path):
        """All failures returns empty list."""
        extractor = ClipExtractor(output_dir=tmp_path, config=_MOCK_CONFIG)
        source = tmp_path / "src.mp4"
        source.write_bytes(b"\x00")

        mock_extract.side_effect = RuntimeError("fail")
        segs = [
            ClipSegment(source_path=source, start_time=0, end_time=5, asset_id="a"),
        ]
        results = extractor.batch_extract(segs)
        assert not results

    # WHY: extract() calls FFmpeg — isolate progress callback logic from real extraction
    @patch("immich_memories.processing.clips.ClipExtractor.extract")
    # WHY: cleanup_old_clips() deletes files on disk — prevent side effects in tests
    @patch("immich_memories.processing.clips.ClipExtractor.cleanup_old_clips")
    def test_batch_progress_callback(self, mock_cleanup, mock_extract, tmp_path):
        """Progress callback called with (current, total)."""
        extractor = ClipExtractor(output_dir=tmp_path, config=_MOCK_CONFIG)
        source = tmp_path / "src.mp4"
        source.write_bytes(b"\x00")

        mock_extract.return_value = tmp_path / "a.mp4"
        segs = [
            ClipSegment(source_path=source, start_time=0, end_time=5, asset_id="a"),
        ]
        callback = MagicMock()
        extractor.batch_extract(segs, progress_callback=callback)
        callback.assert_called_once_with(1, 1)


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
