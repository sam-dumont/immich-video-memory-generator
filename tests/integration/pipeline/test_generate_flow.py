"""Behavioral tests for generate.py orchestration flows.

Tests the end-to-end pipeline with synthetic clips and mocked
external boundaries (Immich download/upload, music generation).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from immich_memories.config_loader import Config
from immich_memories.generate import GenerationError, GenerationParams, generate_memory
from immich_memories.processing.assembly_config import AssemblyClip
from tests.conftest import make_clip
from tests.integration.conftest import ffprobe_json, get_duration, has_stream, requires_ffmpeg


def _mock_extract(clips_with_paths: list[tuple[Path, str, float]]):
    """Build a patch for _extract_clips that returns AssemblyClips from local files."""

    def _extract(params, video_cache, output_dir):
        return [
            AssemblyClip(
                path=path,
                duration=dur,
                date="2025-06-15",
                asset_id=aid,
            )
            for path, aid, dur in clips_with_paths
        ]

    return patch("immich_memories.generate._extract_clips", side_effect=_extract)


@requires_ffmpeg
class TestGenerateFlowHappyPath:
    def test_single_clip_produces_valid_video(self, test_clip_720p, tmp_path):
        """One clip in -> valid video out with video stream and nonzero duration."""
        clip = make_clip("clip-a", width=1280, height=720, duration=3.0)
        config = Config()
        config.title_screens.enabled = False
        output = tmp_path / "output" / "memory.mp4"

        # WHY: mock _extract_clips to return our local test clip instead of hitting Immich
        with _mock_extract([(test_clip_720p, "clip-a", 3.0)]):
            params = GenerationParams(
                clips=[clip],
                output_path=output,
                config=config,
                no_music=True,
                transition="crossfade",
            )
            result = generate_memory(params)

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        assert get_duration(probe) > 1.0

    def test_multiple_clips_merged(self, test_clip_720p, test_clip_720p_b, tmp_path):
        """Two clips should produce output with duration roughly equal to sum minus crossfade."""
        clips = [
            make_clip("a", width=1280, height=720, duration=3.0),
            make_clip("b", width=1280, height=720, duration=3.0),
        ]
        config = Config()
        config.title_screens.enabled = False
        output = tmp_path / "output" / "merged.mp4"

        with _mock_extract([(test_clip_720p, "a", 3.0), (test_clip_720p_b, "b", 3.0)]):
            params = GenerationParams(
                clips=clips,
                output_path=output,
                config=config,
                no_music=True,
                transition="crossfade",
                transition_duration=0.5,
                output_resolution="720p",
            )
            result = generate_memory(params)

        duration = get_duration(ffprobe_json(result))
        # 3 + 3 - 0.5 crossfade = ~5.5s, allow tolerance
        assert 4.0 < duration < 7.0


@requires_ffmpeg
class TestGenerateFlowErrors:
    def test_no_clips_raises_error(self, tmp_path):
        """Empty clip list should raise GenerationError immediately."""
        config = Config()
        params = GenerationParams(
            clips=[],
            output_path=tmp_path / "nope.mp4",
            config=config,
        )

        with pytest.raises(GenerationError, match="No clips"):
            generate_memory(params)

    def test_all_clips_invalid_raises_error(self, tmp_path):
        """If all clips fail extraction, should raise GenerationError."""
        clip = make_clip("bad", width=1280, height=720, duration=3.0)
        config = Config()
        config.title_screens.enabled = False

        with patch("immich_memories.generate._extract_clips", return_value=[]):
            params = GenerationParams(
                clips=[clip],
                output_path=tmp_path / "output" / "fail.mp4",
                config=config,
                no_music=True,
            )

            with pytest.raises(GenerationError, match="No clips could be processed"):
                generate_memory(params)

    def test_unexpected_error_wrapped_as_generation_error(self, tmp_path):
        """Non-GenerationError exceptions should be wrapped and sanitized."""
        clip = make_clip("x", width=1280, height=720, duration=3.0)
        config = Config()
        config.title_screens.enabled = False

        with patch(
            "immich_memories.generate._extract_clips",
            side_effect=RuntimeError("internal kaboom"),
        ):
            params = GenerationParams(
                clips=[clip],
                output_path=tmp_path / "output" / "fail.mp4",
                config=config,
                no_music=True,
            )

            with pytest.raises(GenerationError, match="Generation failed"):
                generate_memory(params)


@requires_ffmpeg
class TestGenerateFlowProgress:
    def test_progress_is_monotonically_increasing(self, test_clip_720p, tmp_path):
        """All progress callback values should be >= the previous value."""
        clip = make_clip("a", width=1280, height=720, duration=3.0)
        config = Config()
        config.title_screens.enabled = False
        output = tmp_path / "output" / "progress.mp4"
        progress_values: list[float] = []

        def capture_progress(phase: str, progress: float, msg: str) -> None:
            progress_values.append(progress)

        with _mock_extract([(test_clip_720p, "a", 3.0)]):
            params = GenerationParams(
                clips=[clip],
                output_path=output,
                config=config,
                no_music=True,
                progress_callback=capture_progress,
            )
            generate_memory(params)

        assert len(progress_values) > 0
        for i in range(1, len(progress_values)):
            assert progress_values[i] >= progress_values[i - 1], (
                f"Progress went backward: {progress_values[i - 1]} -> {progress_values[i]}"
            )
