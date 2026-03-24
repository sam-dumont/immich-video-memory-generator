"""Unit tests for AudioMixerService strategy selection and mixing logic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.processing.assembly_config import AssemblySettings
from immich_memories.processing.audio_mixer_service import AudioMixerService


def _make_service(**overrides: object) -> AudioMixerService:
    settings = AssemblySettings(**overrides)  # type: ignore[arg-type]
    return AudioMixerService(settings)


# ---------------------------------------------------------------------------
# Volume dB conversion
# ---------------------------------------------------------------------------


class TestVolumeDb:
    """Verify linear volume → dB conversion."""

    def test_unity_volume_is_zero_db(self) -> None:
        svc = _make_service(music_volume=1.0)
        assert svc._volume_db() == 0.0

    def test_half_volume_is_minus_six_db(self) -> None:
        svc = _make_service(music_volume=0.5)
        assert svc._volume_db() == pytest.approx(-6.0206, abs=0.01)

    def test_zero_volume_clamps_to_floor(self) -> None:
        svc = _make_service(music_volume=0.0)
        # floor is 0.01 → 20*log10(0.01) = -40 dB
        assert svc._volume_db() == pytest.approx(-40.0, abs=0.01)


# ---------------------------------------------------------------------------
# Strategy selection
# ---------------------------------------------------------------------------


class TestStrategySelection:
    """Verify add_music picks the right mixing strategy based on available stems."""

    @pytest.fixture()
    def stems_dir(self, tmp_path: Path) -> dict[str, Path]:
        """Create real stem files and return their paths."""
        paths: dict[str, Path] = {}
        for name in ("drums", "bass", "vocals", "other", "accompaniment"):
            p = tmp_path / f"{name}.wav"
            p.write_bytes(b"\x00" * 64)
            paths[name] = p
        return paths

    def test_four_stems_present_uses_4stem(
        self, stems_dir: dict[str, Path], tmp_path: Path
    ) -> None:
        svc = _make_service(
            music_path=tmp_path / "music.mp3",
            music_drums_path=stems_dir["drums"],
            music_bass_path=stems_dir["bass"],
            music_vocals_path=stems_dir["vocals"],
            music_other_path=stems_dir["other"],
        )
        with patch.object(svc, "_add_music_with_4stems", return_value=tmp_path / "out.mp4") as m4:
            svc.add_music(tmp_path / "video.mp4", tmp_path / "out.mp4")
        m4.assert_called_once()

    def test_two_stems_present_uses_2stem(self, stems_dir: dict[str, Path], tmp_path: Path) -> None:
        svc = _make_service(
            music_path=tmp_path / "music.mp3",
            music_vocals_path=stems_dir["vocals"],
            music_accompaniment_path=stems_dir["accompaniment"],
        )
        with patch.object(svc, "_add_music_with_stems", return_value=tmp_path / "out.mp4") as m2:
            svc.add_music(tmp_path / "video.mp4", tmp_path / "out.mp4")
        m2.assert_called_once()

    def test_no_stems_uses_simple(self, tmp_path: Path) -> None:
        svc = _make_service(music_path=tmp_path / "music.mp3")
        with patch.object(svc, "_add_music_simple", return_value=tmp_path / "out.mp4") as ms:
            svc.add_music(tmp_path / "video.mp4", tmp_path / "out.mp4")
        ms.assert_called_once()

    def test_four_stems_one_missing_file_skips_4stem(
        self, stems_dir: dict[str, Path], tmp_path: Path
    ) -> None:
        # Remove one stem file so it doesn't exist on disk
        stems_dir["bass"].unlink()

        svc = _make_service(
            music_path=tmp_path / "music.mp3",
            music_drums_path=stems_dir["drums"],
            music_bass_path=stems_dir["bass"],  # path set but file gone
            music_vocals_path=stems_dir["vocals"],
            music_other_path=stems_dir["other"],
            music_accompaniment_path=stems_dir["accompaniment"],
        )
        # Should fall through to 2-stem since vocals + accompaniment exist
        with patch.object(svc, "_add_music_with_stems", return_value=tmp_path / "out.mp4") as m2:
            svc.add_music(tmp_path / "video.mp4", tmp_path / "out.mp4")
        m2.assert_called_once()

    def test_stems_path_none_skips_that_strategy(self, tmp_path: Path) -> None:
        svc = _make_service(
            music_path=tmp_path / "music.mp3",
            music_vocals_path=None,
            music_accompaniment_path=None,
        )
        with patch.object(svc, "_add_music_simple", return_value=tmp_path / "out.mp4") as ms:
            svc.add_music(tmp_path / "video.mp4", tmp_path / "out.mp4")
        ms.assert_called_once()

    def test_four_stems_preferred_over_two_stems(
        self, stems_dir: dict[str, Path], tmp_path: Path
    ) -> None:
        """When both 4-stem and 2-stem are available, 4-stem wins."""
        svc = _make_service(
            music_path=tmp_path / "music.mp3",
            music_drums_path=stems_dir["drums"],
            music_bass_path=stems_dir["bass"],
            music_vocals_path=stems_dir["vocals"],
            music_other_path=stems_dir["other"],
            music_accompaniment_path=stems_dir["accompaniment"],
        )
        with (
            patch.object(svc, "_add_music_with_4stems", return_value=tmp_path / "out.mp4") as m4,
            patch.object(svc, "_add_music_with_stems", return_value=tmp_path / "out.mp4") as m2,
        ):
            svc.add_music(tmp_path / "video.mp4", tmp_path / "out.mp4")
        m4.assert_called_once()
        m2.assert_not_called()


# ---------------------------------------------------------------------------
# Fallback on failure
# ---------------------------------------------------------------------------


class TestFallbackBehavior:
    """Verify graceful degradation when stem-based mixing fails."""

    @pytest.fixture()
    def stems_dir(self, tmp_path: Path) -> dict[str, Path]:
        paths: dict[str, Path] = {}
        for name in ("drums", "bass", "vocals", "other", "accompaniment"):
            p = tmp_path / f"{name}.wav"
            p.write_bytes(b"\x00" * 64)
            paths[name] = p
        return paths

    def test_4stem_failure_falls_back_to_simple(
        self, stems_dir: dict[str, Path], tmp_path: Path
    ) -> None:
        svc = _make_service(
            music_path=tmp_path / "music.mp3",
            music_drums_path=stems_dir["drums"],
            music_bass_path=stems_dir["bass"],
            music_vocals_path=stems_dir["vocals"],
            music_other_path=stems_dir["other"],
        )
        with (
            patch(
                "immich_memories.processing.audio_mixer_service.AudioMixerService._add_music_simple",
                return_value=tmp_path / "out.mp4",
            ) as mock_simple,
            patch(
                "immich_memories.audio.mixer_helpers.mix_audio_with_4stem_ducking",
                side_effect=RuntimeError("FFmpeg failed"),
            ),
        ):
            result = svc.add_music(tmp_path / "video.mp4", tmp_path / "out.mp4")
        mock_simple.assert_called_once()
        assert result == tmp_path / "out.mp4"

    def test_2stem_failure_falls_back_to_simple(
        self, stems_dir: dict[str, Path], tmp_path: Path
    ) -> None:
        svc = _make_service(
            music_path=tmp_path / "music.mp3",
            music_vocals_path=stems_dir["vocals"],
            music_accompaniment_path=stems_dir["accompaniment"],
        )
        with (
            patch(
                "immich_memories.processing.audio_mixer_service.AudioMixerService._add_music_simple",
                return_value=tmp_path / "out.mp4",
            ) as mock_simple,
            patch(
                "immich_memories.audio.mixer_helpers.mix_audio_with_stem_ducking",
                side_effect=RuntimeError("FFmpeg failed"),
            ),
        ):
            result = svc.add_music(tmp_path / "video.mp4", tmp_path / "out.mp4")
        mock_simple.assert_called_once()
        assert result == tmp_path / "out.mp4"

    def test_simple_mix_ffmpeg_failure_returns_original(self, tmp_path: Path) -> None:
        video = tmp_path / "video.mp4"
        video.write_bytes(b"\x00" * 64)
        music = tmp_path / "music.mp3"
        music.write_bytes(b"\x00" * 64)

        svc = _make_service(music_path=music, music_volume=0.3)
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "some error"

        with patch("subprocess.run", return_value=mock_result):  # WHY: replaces FFmpeg subprocess
            result = svc._add_music_simple(video, tmp_path / "out.mp4")
        assert result == video


# ---------------------------------------------------------------------------
# Simple mixing FFmpeg command
# ---------------------------------------------------------------------------


class TestSimpleMixing:
    """Verify simple mixing builds correct FFmpeg commands."""

    @pytest.fixture()
    def music_path(self, tmp_path: Path) -> Path:
        p = tmp_path / "music.mp3"
        p.write_bytes(b"\x00" * 64)
        return p

    def test_builds_ffmpeg_command_with_volume_filter(
        self, tmp_path: Path, music_path: Path
    ) -> None:
        svc = _make_service(music_path=music_path, music_volume=0.4)
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:  # WHY: replaces FFmpeg
            svc._add_music_simple(tmp_path / "video.mp4", tmp_path / "out.mp4")

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "ffmpeg"
        assert "-filter_complex" in cmd
        filter_idx = cmd.index("-filter_complex") + 1
        assert "volume=0.4" in cmd[filter_idx]
        assert "amix=inputs=2" in cmd[filter_idx]

    def test_music_volume_flows_into_filter(self, tmp_path: Path, music_path: Path) -> None:
        svc = _make_service(music_path=music_path, music_volume=0.7)
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:  # WHY: replaces FFmpeg
            svc._add_music_simple(tmp_path / "video.mp4", tmp_path / "out.mp4")

        cmd = mock_run.call_args[0][0]
        filter_idx = cmd.index("-filter_complex") + 1
        assert "volume=0.7" in cmd[filter_idx]

    def test_same_input_output_uses_temp_file(self, tmp_path: Path, music_path: Path) -> None:
        video = tmp_path / "video.mp4"
        svc = _make_service(music_path=music_path, music_volume=0.3)
        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch("subprocess.run", return_value=mock_result) as mock_run,  # WHY: replaces FFmpeg
            patch("shutil.move") as mock_move,
        ):
            result = svc._add_music_simple(video, video)

        # FFmpeg should write to a temp file, not the input
        cmd = mock_run.call_args[0][0]
        output_arg = cmd[-1]
        assert output_arg != str(video)
        assert output_arg.endswith(".temp.mp4")
        # Then shutil.move copies temp → final
        mock_move.assert_called_once()
        assert result == video

    def test_returns_output_path_on_success(self, tmp_path: Path, music_path: Path) -> None:
        svc = _make_service(music_path=music_path, music_volume=0.3)
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):  # WHY: replaces FFmpeg subprocess
            result = svc._add_music_simple(tmp_path / "video.mp4", tmp_path / "out.mp4")
        assert result == tmp_path / "out.mp4"
