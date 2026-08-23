"""Regression tests for deterministic music-stage lifecycle."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from immich_memories.filename_builder import build_music_output_path
from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec


def _h264_plan() -> EncodingPlan:
    return EncodingPlan(
        codec=OutputCodec.H264,
        encoder="libx264",
        encoder_args=("-c:v", "libx264"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv420p",
        container="mp4",
    )


class _Progress:
    value = 0.0


class _Status:
    def set_text(self, _text: str) -> None:
        pass


def test_shared_music_stage_is_cleared_before_mix_and_after_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from immich_memories.generate_music import apply_music_file

    video_path = tmp_path / "memory.mp4"
    music_path = tmp_path / "music.wav"
    video_path.write_bytes(b"validated-base")
    music_path.write_bytes(b"music")
    staged_path = build_music_output_path(video_path)
    staged_path.write_bytes(b"stale-valid-mix")
    absent_at_entry: list[bool] = []

    def fail_after_partial_write(**kwargs: object) -> None:
        output_path = cast(Path, kwargs["output_path"])
        absent_at_entry.append(not output_path.exists())
        output_path.write_bytes(b"partial-mix")
        raise RuntimeError("mixer failed")

    monkeypatch.setattr(
        "immich_memories.audio.mixer.mix_audio_with_ducking",
        fail_after_partial_write,
    )

    with pytest.raises(RuntimeError, match="mixer failed"):
        apply_music_file(video_path, music_path, 0.5, _h264_plan())

    assert absent_at_entry == [True]
    assert not staged_path.exists()
    assert video_path.read_bytes() == b"validated-base"


def test_noop_mixer_cannot_publish_a_stale_music_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from immich_memories.generate_music import apply_music_file
    from immich_memories.processing.output_contract import InvalidOutputArtifact

    video_path = tmp_path / "memory.mp4"
    music_path = tmp_path / "music.wav"
    video_path.write_bytes(b"validated-base")
    music_path.write_bytes(b"music")
    staged_path = build_music_output_path(video_path)
    staged_path.write_bytes(b"stale-valid-mix")
    monkeypatch.setattr(
        "immich_memories.audio.mixer.mix_audio_with_ducking",
        lambda **_kwargs: None,
    )

    def require_new_stage(path: Path) -> None:
        if not path.exists():
            raise InvalidOutputArtifact("mixer produced no output")

    monkeypatch.setattr(
        "immich_memories.generate_music._require_audio_stream",
        require_new_stage,
    )

    with pytest.raises(InvalidOutputArtifact, match="mixer produced no output"):
        apply_music_file(video_path, music_path, 0.5, _h264_plan())

    assert not staged_path.exists()
    assert video_path.read_bytes() == b"validated-base"


def test_initial_stale_stage_cleanup_remains_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure to remove stale bytes must stop the mixer before it can publish them."""
    from immich_memories.generate_music import apply_music_file

    video_path = tmp_path / "memory.mp4"
    music_path = tmp_path / "music.wav"
    video_path.write_bytes(b"validated-base")
    music_path.write_bytes(b"music")
    staged_path = build_music_output_path(video_path)
    staged_path.write_bytes(b"stale-valid-mix")
    mixer = MagicMock()
    real_unlink = Path.unlink

    def fail_stale_unlink(path: Path, missing_ok: bool = False) -> None:
        if path == staged_path:
            raise OSError("cannot remove stale stage")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr("immich_memories.audio.mixer.mix_audio_with_ducking", mixer)
    monkeypatch.setattr(Path, "unlink", fail_stale_unlink)

    with pytest.raises(OSError, match="cannot remove stale stage"):
        apply_music_file(video_path, music_path, 0.5, _h264_plan())

    mixer.assert_not_called()
    assert staged_path.read_bytes() == b"stale-valid-mix"
    assert video_path.read_bytes() == b"validated-base"
