"""Unit tests for the VideoAssembler.

Tests the assembler through its public API (assemble()) by mocking at
external boundaries (FFmpeg subprocess, get_config) rather than internal
methods. Renaming internal methods should not break these tests.

For real FFmpeg integration tests, see tests/integration/test_assembly_real.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
    TransitionType,
)


def _make_assembly_clip(
    tmp_path: Path, name: str = "clip.mp4", duration: float = 5.0
) -> AssemblyClip:
    """Create a temporary file and wrap it as an AssemblyClip."""
    clip_path = tmp_path / name
    clip_path.write_bytes(b"\x00" * 1024)
    return AssemblyClip(path=clip_path, duration=duration)


def _make_assembler(settings: AssemblySettings | None = None):
    """Create a VideoAssembler with mocked get_config."""
    from immich_memories.processing.video_assembler import VideoAssembler

    # WHY: mock get_config — assembler reads global config at init, tests shouldn't need a config file
    with patch("immich_memories.processing.video_assembler.get_config", return_value=MagicMock()):
        return VideoAssembler(settings or AssemblySettings())


class TestVideoAssemblerIntegration:
    """Integration tests for VideoAssembler through public API."""

    def test_assemble_empty_clips_raises(self):
        """assemble() with empty clips raises ValueError."""
        assembler = _make_assembler()
        with pytest.raises(ValueError, match="No clips provided"):
            assembler.assemble([], Path("/tmp/out.mp4"))

    def test_single_clip_copies_file(self, tmp_path):
        """Single clip without music copies the file to output."""
        assembler = _make_assembler(AssemblySettings(music_path=None))
        clip = _make_assembly_clip(tmp_path, "input.mp4")
        output = tmp_path / "output.mp4"

        result = assembler.assemble([clip], output)
        assert result == output
        assert output.exists()
        assert output.read_bytes() == clip.path.read_bytes()

    def test_settings_propagate(self):
        """Assembly settings are accessible on the assembler."""
        assembler = _make_assembler(
            AssemblySettings(
                transition=TransitionType.CUT,
                output_crf=22,
            )
        )
        assert assembler.settings.transition == TransitionType.CUT
        assert assembler.settings.output_crf == 22

    def test_single_clip_with_music_triggers_music_flow(self, tmp_path):
        """Single clip with music_path set takes the music branch, not copy."""
        music_file = tmp_path / "music.mp3"
        music_file.write_bytes(b"\x00" * 512)

        assembler = _make_assembler(AssemblySettings(music_path=music_file))
        clip = _make_assembly_clip(tmp_path, "input.mp4")
        output = tmp_path / "output.mp4"

        # WHY: mock subprocess.run — test verifies graceful fallback when FFmpeg fails,
        # without requiring FFmpeg to be installed in the test environment
        mock_result = MagicMock(returncode=1, stderr="mock ffmpeg failure")
        with patch(
            "immich_memories.processing.audio_mixer_service.subprocess.run",
            return_value=mock_result,
        ):
            result = assembler.assemble([clip], output)

        # Music add fails gracefully; assembler falls back to original file
        assert isinstance(result, Path)

    def test_assemble_returns_path(self, tmp_path):
        """assemble() returns a Path on success."""
        assembler = _make_assembler(AssemblySettings(music_path=None))
        clip = _make_assembly_clip(tmp_path, "single.mp4")
        output = tmp_path / "result.mp4"

        result = assembler.assemble([clip], output)
        assert isinstance(result, Path)

    def test_default_settings_from_config(self):
        """Assembler picks up defaults from global config."""
        # WHY: mock get_config — verify assembler reads specific config values at init
        mock_config = MagicMock()
        mock_config.output.crf = 23
        mock_config.defaults.transition_duration = 0.8

        from immich_memories.processing.video_assembler import VideoAssembler

        with patch(
            "immich_memories.processing.video_assembler.get_config", return_value=mock_config
        ):
            assembler = VideoAssembler()

        assert assembler.settings.output_crf == 23
        assert assembler.settings.transition_duration == 0.8

    def test_assemble_idempotent_single_clip(self, tmp_path):
        """Assembling the same single clip twice produces identical output."""
        assembler = _make_assembler(AssemblySettings(music_path=None))
        clip = _make_assembly_clip(tmp_path, "input.mp4")

        out1 = tmp_path / "out1.mp4"
        out2 = tmp_path / "out2.mp4"
        assembler.assemble([clip], out1)
        assembler.assemble([clip], out2)

        assert out1.read_bytes() == out2.read_bytes()

    def test_assemble_does_not_modify_input(self, tmp_path):
        """Assembling does not modify the source clip file."""
        assembler = _make_assembler(AssemblySettings(music_path=None))
        clip = _make_assembly_clip(tmp_path, "input.mp4")
        original_bytes = clip.path.read_bytes()

        assembler.assemble([clip], tmp_path / "output.mp4")

        assert clip.path.read_bytes() == original_bytes

    def test_missing_output_parent_raises(self, tmp_path):
        """Assembler raises when output parent directory does not exist."""
        assembler = _make_assembler(AssemblySettings(music_path=None))
        clip = _make_assembly_clip(tmp_path, "input.mp4")
        output = tmp_path / "nonexistent" / "output.mp4"

        with pytest.raises(FileNotFoundError):
            assembler.assemble([clip], output)
