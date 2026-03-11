"""Integration tests for the VideoAssembler."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
    JobCancelledException,
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

    with patch("immich_memories.processing.video_assembler.get_config", return_value=MagicMock()):
        return VideoAssembler(settings or AssemblySettings())


class TestVideoAssemblerIntegration:
    """Integration tests for VideoAssembler."""

    def test_assemble_empty_clips_raises(self):
        """assemble() with empty clips raises ValueError."""
        assembler = _make_assembler()
        with pytest.raises(ValueError, match="No clips provided"):
            assembler.assemble([], Path("/tmp/out.mp4"))

    def test_single_clip_copies_file(self, tmp_path):
        """Single clip without music just copies the file."""
        assembler = _make_assembler(AssemblySettings(music_path=None))
        clip = _make_assembly_clip(tmp_path, "input.mp4")
        output = tmp_path / "output.mp4"

        result = assembler.assemble([clip], output)
        assert result == output
        assert output.exists()
        assert output.read_bytes() == clip.path.read_bytes()

    def test_multiple_clips_calls_scalable_assembly(self, tmp_path):
        """Multiple clips invokes the scalable assembly path."""
        assembler = _make_assembler(AssemblySettings(music_path=None))
        clips = [_make_assembly_clip(tmp_path, f"clip{i}.mp4") for i in range(3)]
        output = tmp_path / "output.mp4"

        with patch.object(assembler, "_assemble_scalable", return_value=output) as mock_scalable:
            result = assembler.assemble(clips, output)

        assert result == output
        mock_scalable.assert_called_once()
        assert len(mock_scalable.call_args[0][0]) == 3

    def test_music_path_triggers_add_music(self, tmp_path):
        """When music_path is set and exists, _add_music is called."""
        music_file = tmp_path / "music.mp3"
        music_file.write_bytes(b"\x00" * 512)

        assembler = _make_assembler(AssemblySettings(music_path=music_file))
        clips = [_make_assembly_clip(tmp_path, f"clip{i}.mp4") for i in range(2)]
        output = tmp_path / "output.mp4"

        with (
            patch.object(assembler, "_assemble_scalable", return_value=output),
            patch.object(assembler, "_add_music", return_value=output) as mock_music,
        ):
            assembler.assemble(clips, output)

        mock_music.assert_called_once()

    def test_no_music_skips_add_music(self, tmp_path):
        """When no music_path, _add_music is not called."""
        assembler = _make_assembler(AssemblySettings(music_path=None))
        clips = [_make_assembly_clip(tmp_path, f"clip{i}.mp4") for i in range(2)]
        output = tmp_path / "output.mp4"

        with (
            patch.object(assembler, "_assemble_scalable", return_value=output),
            patch.object(assembler, "_add_music", return_value=output) as mock_music,
        ):
            assembler.assemble(clips, output)

        mock_music.assert_not_called()

    def test_cancellation_raises_exception(self):
        """Cancellation request raises JobCancelledException."""
        assembler = _make_assembler()
        assembler.run_id = "test-run-456"

        mock_run_db = MagicMock()
        mock_run_db.is_cancel_requested.return_value = True
        assembler._run_db = mock_run_db

        with pytest.raises(JobCancelledException):
            assembler._check_cancelled()

    def test_settings_propagate(self):
        """Assembly settings propagate to the assembler."""
        assembler = _make_assembler(
            AssemblySettings(
                transition=TransitionType.CUT,
                output_crf=22,
            )
        )
        assert assembler.settings.transition == TransitionType.CUT
        assert assembler.settings.output_crf == 22
