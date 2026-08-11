"""Regression tests for deterministic music-stage lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest

from immich_memories.config_loader import Config
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


@pytest.mark.asyncio
@pytest.mark.parametrize("use_four_stems", [False, True], ids=["full-mix", "four-stem"])
async def test_ai_music_stage_is_cleared_before_mix_and_after_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_four_stems: bool,
) -> None:
    from immich_memories.generate_music import MusicPhaseResult
    from immich_memories.ui.pages._step4_music import apply_ai_music

    video_path = tmp_path / "memory.mp4"
    video_path.write_bytes(b"validated-base")
    staged_path = build_music_output_path(video_path)
    staged_path.write_bytes(b"stale-valid-mix")
    stems = None
    if use_four_stems:
        stems = SimpleNamespace(
            drums=tmp_path / "drums.wav",
            bass=tmp_path / "bass.wav",
            vocals=tmp_path / "vocals.wav",
            other=tmp_path / "other.wav",
        )
    selected = SimpleNamespace(full_mix=tmp_path / "music.wav", stems=stems)
    music_result = SimpleNamespace(
        versions=[selected],
        selected=selected,
        cleanup_unselected=MagicMock(),
    )
    monkeypatch.setattr(
        "immich_memories.ui.state.get_app_state",
        lambda: SimpleNamespace(music_preview_result=music_result),
    )
    absent_at_entry: list[bool] = []

    def fail_after_partial_write(**kwargs: object) -> None:
        output_path = cast(Path, kwargs["output_path"])
        absent_at_entry.append(not output_path.exists())
        output_path.write_bytes(b"partial-mix")
        raise RuntimeError("mixer failed")

    async def io_bound(callback: Callable[..., object], **kwargs: object) -> object:
        return callback(**kwargs)

    monkeypatch.setattr(
        "immich_memories.audio.mixer.mix_audio_with_ducking",
        fail_after_partial_write,
    )
    monkeypatch.setattr(
        "immich_memories.audio.mixer_helpers.mix_audio_with_4stem_ducking",
        fail_after_partial_write,
    )
    monkeypatch.setattr("immich_memories.ui.pages._step4_music.run.io_bound", io_bound)
    monkeypatch.setattr("immich_memories.ui.pages._step4_music.ui.notify", MagicMock())

    result = await apply_ai_music(
        video_path,
        assembly_clips=[],
        gen_options={"music_volume": 0.5},
        config=Config(),
        run_output_dir=tmp_path,
        run_tracker=MagicMock(),
        progress_bar=_Progress(),
        status_label=_Status(),
        encoding_plan=_h264_plan(),
    )

    assert result == MusicPhaseResult(applied=False, warning="Optional music failed: mixer failed")
    assert absent_at_entry == [True]
    assert not staged_path.exists()
    assert video_path.read_bytes() == b"validated-base"


@pytest.mark.asyncio
async def test_uploaded_music_stage_is_cleared_before_mix_and_after_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from immich_memories.generate_music import MusicPhaseResult
    from immich_memories.ui.pages._step4_music import apply_uploaded_music

    video_path = tmp_path / "memory.mp4"
    video_path.write_bytes(b"validated-base")
    staged_path = build_music_output_path(video_path)
    staged_path.write_bytes(b"stale-valid-mix")
    absent_at_entry: list[bool] = []

    def fail_after_partial_write(**kwargs: object) -> None:
        output_path = cast(Path, kwargs["output_path"])
        absent_at_entry.append(not output_path.exists())
        output_path.write_bytes(b"partial-mix")
        raise RuntimeError("mixer failed")

    async def io_bound(callback: Callable[..., object], **kwargs: object) -> object:
        return callback(**kwargs)

    monkeypatch.setattr(
        "immich_memories.audio.mixer.mix_audio_with_ducking",
        fail_after_partial_write,
    )
    monkeypatch.setattr("immich_memories.ui.pages._step4_music.run.io_bound", io_bound)
    monkeypatch.setattr("immich_memories.ui.pages._step4_music.ui.notify", MagicMock())

    result = await apply_uploaded_music(
        video_path,
        gen_options={"music_file": b"uploaded", "music_volume": 0.5},
        run_tracker=MagicMock(),
        progress_bar=_Progress(),
        status_label=_Status(),
        encoding_plan=_h264_plan(),
        config=Config(),
    )

    assert result == MusicPhaseResult(applied=False, warning="Optional music failed: mixer failed")
    assert absent_at_entry == [True]
    assert not staged_path.exists()
    assert video_path.read_bytes() == b"validated-base"
