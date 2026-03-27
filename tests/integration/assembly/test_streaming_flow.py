"""Behavioral tests for streaming_assembler.py flows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from immich_memories.processing.streaming_assembler import streaming_assemble_full
from tests.integration.conftest import ffprobe_json, get_duration, has_stream, requires_ffmpeg


@dataclass
class _FakeClip:
    """Minimal clip matching the streaming assembler's duck-typed interface."""

    path: Path
    duration: float
    is_title_screen: bool = False
    rotation_override: int | None = None
    is_hdr: bool = False
    color_transfer: str | None = None
    input_seek: float = 0.0


@requires_ffmpeg
class TestStreamingAssembleFull:
    def test_single_clip_valid_output(self, test_clip_720p, tmp_path):
        """One clip should produce a valid video with audio."""
        clip = _FakeClip(test_clip_720p, 3.0)
        output = tmp_path / "single.mp4"

        result = streaming_assemble_full(
            clips=[clip],
            transitions=[],
            output_path=output,
            width=1280,
            height=720,
            fps=30,
        )

        assert result.exists()
        probe = ffprobe_json(result)
        assert has_stream(probe, "video")
        assert has_stream(probe, "audio")
        assert get_duration(probe) > 1.0

    def test_crossfade_shortens_output(self, test_clip_720p, test_clip_720p_b, tmp_path):
        """Two clips with crossfade should produce shorter output than sum."""
        clip_a = _FakeClip(test_clip_720p, 3.0)
        clip_b = _FakeClip(test_clip_720p_b, 3.0)
        output = tmp_path / "crossfade.mp4"

        result = streaming_assemble_full(
            clips=[clip_a, clip_b],
            transitions=["crossfade"],
            output_path=output,
            width=1280,
            height=720,
            fps=30,
            fade_duration=0.5,
        )

        duration = get_duration(ffprobe_json(result))
        # 3 + 3 - 0.5 = 5.5s expected, with tolerance
        assert 4.0 < duration < 6.5

    def test_progress_callback_fires(self, test_clip_720p, tmp_path):
        """Progress callback should receive increasing values."""
        clip = _FakeClip(test_clip_720p, 3.0)
        output = tmp_path / "progress.mp4"
        values: list[float] = []

        def capture(pct: float, msg: str) -> None:
            values.append(pct)

        streaming_assemble_full(
            clips=[clip],
            transitions=[],
            output_path=output,
            width=1280,
            height=720,
            fps=30,
            progress_callback=capture,
        )

        assert len(values) > 0
        assert values[-1] > values[0]
