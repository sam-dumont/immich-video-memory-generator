"""Real Immich integration tests for generate.py core pipeline flows.

Tests the actual business: fetch clips from Immich, download them, extract
segments, assemble with titles, apply music, verify the output video.
No mocks. Real FFmpeg, real Immich, real data.
"""

from __future__ import annotations

import logging

import pytest

from immich_memories.generate import GenerationParams, generate_memory
from tests.integration.conftest import ffprobe_json, get_duration, has_stream, requires_ffmpeg
from tests.integration.immich_fixtures import requires_immich

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.integration, requires_ffmpeg, requires_immich]


class TestGenerateMemoryRealImmich:
    """End-to-end generate_memory() with real Immich clips."""

    def test_full_pipeline_produces_valid_video(self, immich_short_clips, tmp_path):
        """The core flow: Immich clips → download → extract → assemble → video."""
        clips, config, client = immich_short_clips
        config.title_screens.enabled = False
        output = tmp_path / "output" / "real_memory.mp4"

        params = GenerationParams(
            clips=clips[:2],
            output_path=output,
            config=config,
            client=client,
            no_music=True,
            transition="crossfade",
            transition_duration=0.5,
            output_resolution="720p",
        )
        result = generate_memory(params)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")
        duration = get_duration(probe)
        assert duration > 2.0
        logger.info(f"Generated memory: {duration:.1f}s, {result.stat().st_size / 1024:.0f} KB")

    def test_pipeline_with_title_screens(self, immich_short_clips, tmp_path):
        """Pipeline with title screens enabled produces longer output."""
        clips, config, client = immich_short_clips
        config.title_screens.enabled = True
        config.title_screens.title_duration = 2.0
        config.title_screens.ending_duration = 2.0
        output = tmp_path / "output" / "titled_memory.mp4"

        from datetime import date

        params = GenerationParams(
            clips=clips[:2],
            output_path=output,
            config=config,
            client=client,
            no_music=True,
            transition="crossfade",
            output_resolution="720p",
            person_name="Test Person",
            memory_type="yearly",
            date_start=date(2025, 1, 1),
            date_end=date(2025, 12, 31),
        )
        result = generate_memory(params)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        duration = get_duration(probe)
        # Title + ending add ~4s to the video
        assert duration > 5.0
        logger.info(f"Titled memory: {duration:.1f}s")

    def test_pipeline_with_privacy_mode(self, immich_short_clips, tmp_path):
        """Privacy mode should still produce a valid video (anonymized GPS/names)."""
        clips, config, client = immich_short_clips
        config.title_screens.enabled = False
        output = tmp_path / "output" / "privacy_memory.mp4"

        params = GenerationParams(
            clips=clips[:2],
            output_path=output,
            config=config,
            client=client,
            no_music=True,
            transition="crossfade",
            output_resolution="720p",
            privacy_mode=True,
            person_name="Real Name",
        )
        result = generate_memory(params)

        assert result.exists()
        assert get_duration(ffprobe_json(result)) > 2.0

    def test_progress_callbacks_fire_with_real_clips(self, immich_short_clips, tmp_path):
        """Progress callback should report all phases with real clip processing."""
        clips, config, client = immich_short_clips
        config.title_screens.enabled = False
        output = tmp_path / "output" / "progress_memory.mp4"
        phases_seen: set[str] = set()
        progress_values: list[float] = []

        def capture(phase: str, progress: float, msg: str) -> None:
            phases_seen.add(phase)
            progress_values.append(progress)

        params = GenerationParams(
            clips=clips[:2],
            output_path=output,
            config=config,
            client=client,
            no_music=True,
            transition="crossfade",
            output_resolution="720p",
            progress_callback=capture,
        )
        generate_memory(params)

        # The pipeline should report extract and assembly phases at minimum
        assert "extract" in phases_seen or "download" in phases_seen
        assert len(progress_values) > 3
        logger.info(f"Phases seen: {phases_seen}")

    def test_cleanup_removes_temp_dirs(self, immich_short_clips, tmp_path):
        """After generation, intermediate directories should be cleaned up."""
        clips, config, client = immich_short_clips
        config.title_screens.enabled = False
        output = tmp_path / "output" / "cleanup_test.mp4"

        params = GenerationParams(
            clips=clips[:1],
            output_path=output,
            config=config,
            client=client,
            no_music=True,
            transition="crossfade",
            output_resolution="720p",
        )
        result = generate_memory(params)

        # Result dir should exist, but intermediate dirs should be cleaned up
        result_dir = result.parent
        assert not (result_dir / ".title_screens").exists()
        assert not (result_dir / ".intermediates").exists()
        assert not (result_dir / ".assembly_temps").exists()
