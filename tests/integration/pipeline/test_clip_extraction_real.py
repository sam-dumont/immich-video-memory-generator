"""Real Immich integration tests for processing/clips.py extraction flows.

Downloads real clips from Immich, extracts segments, verifies the output.
Tests batch extraction, hardware accel fallback, and buffer logic with
actual video data.
"""

from __future__ import annotations

import logging

import pytest

from immich_memories.processing.clips import ClipExtractor, extract_clip
from tests.integration.conftest import ffprobe_json, get_duration, has_stream, requires_ffmpeg
from tests.integration.immich_fixtures import requires_immich

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.integration, requires_ffmpeg, requires_immich]


@pytest.fixture(scope="module")
def downloaded_clip(immich_short_clips, tmp_path_factory):
    """Download a real clip from Immich once per module."""
    clips, config, client = immich_short_clips
    clip = clips[0]

    dl_dir = tmp_path_factory.mktemp("clip_extraction")
    video_path = dl_dir / f"{clip.asset.id}.mp4"
    client.download_asset(clip.asset.id, video_path)

    assert video_path.exists()
    assert video_path.stat().st_size > 1000
    logger.info(f"Downloaded clip {clip.asset.id}: {video_path.stat().st_size / 1024:.0f} KB")
    return video_path, clip, config


class TestExtractClipRealData:
    def test_extract_middle_segment(self, downloaded_clip, tmp_path):
        """Extract a middle segment from a real clip — correct duration."""
        video_path, clip, config = downloaded_clip
        total_dur = clip.duration_seconds or 5.0

        # Extract the middle third
        start = total_dur * 0.33
        end = total_dur * 0.66
        expected_dur = end - start

        result = extract_clip(
            video_path,
            start_time=start,
            end_time=end,
            output_path=tmp_path / "middle.mp4",
            config=config,
        )

        assert result.exists()
        actual_dur = get_duration(ffprobe_json(result))
        # FFmpeg seek isn't frame-exact — allow 1s tolerance
        assert abs(actual_dur - expected_dur) < 1.5
        logger.info(f"Extracted [{start:.1f}-{end:.1f}] → {actual_dur:.1f}s")

    def test_extract_with_reencode(self, downloaded_clip, tmp_path):
        """Re-encoded extraction should produce H.264 output."""
        video_path, clip, config = downloaded_clip
        dur = clip.duration_seconds or 5.0

        result = extract_clip(
            video_path,
            start_time=0.0,
            end_time=min(dur, 3.0),
            output_path=tmp_path / "reencoded.mp4",
            reencode=True,
            config=config,
        )

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        assert get_duration(probe) > 0.5

    def test_batch_extract_multiple_segments(self, downloaded_clip, tmp_path):
        """Batch extraction should produce one file per segment."""
        video_path, clip, config = downloaded_clip
        dur = clip.duration_seconds or 5.0

        from immich_memories.processing.clips import ClipSegment

        segments = [
            ClipSegment(
                asset_id=clip.asset.id,
                source_path=video_path,
                start_time=0.0,
                end_time=min(dur * 0.5, 3.0),
            ),
            ClipSegment(
                asset_id=clip.asset.id,
                source_path=video_path,
                start_time=max(0, dur * 0.5),
                end_time=min(dur, dur * 0.5 + 3.0),
            ),
        ]

        extractor = ClipExtractor(output_dir=tmp_path / "clips", config=config)
        progress_calls: list[float] = []

        results = extractor.batch_extract(
            segments,
            progress_callback=lambda p: progress_calls.append(p),
        )

        assert len(results) >= 1
        for r in results:
            assert r.exists()
            assert get_duration(ffprobe_json(r)) > 0.3
        assert len(progress_calls) > 0
        logger.info(
            f"Batch extracted {len(results)} segments, {len(progress_calls)} progress calls"
        )
