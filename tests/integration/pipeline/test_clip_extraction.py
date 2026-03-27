"""Behavioral tests for processing/clips.py extraction flows."""

from __future__ import annotations

import pytest

from immich_memories.config_loader import Config
from immich_memories.processing.clips import extract_clip
from tests.integration.conftest import ffprobe_json, get_duration, requires_ffmpeg

pytestmark = [pytest.mark.integration, requires_ffmpeg]


class TestExtractClipBehavior:
    def test_extract_segment_has_correct_duration(self, test_clip_720p, tmp_path):
        """Extracting [0.5, 2.5] from a 3s clip should produce ~2s output."""
        config = Config()
        result = extract_clip(
            test_clip_720p,
            start_time=0.5,
            end_time=2.5,
            output_path=tmp_path / "segment.mp4",
            config=config,
        )

        assert result.exists()
        duration = get_duration(ffprobe_json(result))
        assert 1.5 < duration < 2.8  # FFmpeg seek tolerance

    def test_buffer_extends_segment(self, test_clip_720p, tmp_path):
        """Buffer should extend the extracted segment beyond the requested range."""
        config = Config()
        no_buf = extract_clip(
            test_clip_720p,
            start_time=1.0,
            end_time=2.0,
            output_path=tmp_path / "nobuf.mp4",
            config=config,
        )
        with_buf = extract_clip(
            test_clip_720p,
            start_time=1.0,
            end_time=2.0,
            buffer_start=True,
            buffer_end=True,
            buffer_seconds=0.5,
            output_path=tmp_path / "withbuf.mp4",
            config=config,
        )

        dur_no = get_duration(ffprobe_json(no_buf))
        dur_buf = get_duration(ffprobe_json(with_buf))
        assert dur_buf > dur_no

    def test_buffer_clamps_at_zero(self, test_clip_720p, tmp_path):
        """Buffer at start_time=0 should not produce negative seek."""
        config = Config()
        result = extract_clip(
            test_clip_720p,
            start_time=0.0,
            end_time=1.0,
            buffer_start=True,
            buffer_seconds=2.0,
            output_path=tmp_path / "clamped.mp4",
            config=config,
        )

        assert result.exists()
        duration = get_duration(ffprobe_json(result))
        assert duration > 0.5
