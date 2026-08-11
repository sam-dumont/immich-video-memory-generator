"""Regression tests for deterministic music-stage lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

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
async def test_ai_success_survives_final_stage_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A cleanup error after publication cannot rewrite a successful music result."""
    from immich_memories.generate_music import MusicPhaseResult
    from immich_memories.ui.pages._step4_music import apply_ai_music

    video_path = tmp_path / "memory.mp4"
    video_path.write_bytes(b"validated-base")
    staged_path = build_music_output_path(video_path)
    selected = SimpleNamespace(full_mix=tmp_path / "music.wav", stems=None)
    music_result = SimpleNamespace(
        versions=[selected],
        selected=selected,
        cleanup_unselected=MagicMock(),
    )
    monkeypatch.setattr(
        "immich_memories.ui.state.get_app_state",
        lambda: SimpleNamespace(music_preview_result=music_result),
    )

    def write_mix(**kwargs: object) -> None:
        cast(Path, kwargs["output_path"]).write_bytes(b"validated-mix")

    def publish_mix(video_path: Path, encoding_plan: EncodingPlan) -> None:
        del encoding_plan
        build_music_output_path(video_path).replace(video_path)

    async def io_bound(callback: Callable[..., object], **kwargs: object) -> object:
        return callback(**kwargs)

    real_unlink = Path.unlink
    stage_unlinks = 0

    def fail_final_stage_unlink(path: Path, missing_ok: bool = False) -> None:
        nonlocal stage_unlinks
        if path == staged_path:
            stage_unlinks += 1
            if stage_unlinks == 2:
                raise OSError("cleanup leaked private-token-713")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr("immich_memories.audio.mixer.mix_audio_with_ducking", write_mix)
    monkeypatch.setattr("immich_memories.generate_music.publish_music_mix", publish_mix)
    monkeypatch.setattr("immich_memories.ui.pages._step4_music.run.io_bound", io_bound)
    monkeypatch.setattr(Path, "unlink", fail_final_stage_unlink)
    monkeypatch.setattr("immich_memories.ui.pages._step4_music.ui.notify", MagicMock())
    tracker = MagicMock()
    caplog.set_level("DEBUG")

    result = await apply_ai_music(
        video_path,
        assembly_clips=[],
        gen_options={"music_volume": 0.5},
        config=Config(),
        run_output_dir=tmp_path,
        run_tracker=tracker,
        progress_bar=_Progress(),
        status_label=_Status(),
        encoding_plan=_h264_plan(),
    )

    assert result == MusicPhaseResult(applied=True)
    assert video_path.read_bytes() == b"validated-mix"
    tracker.complete_phase.assert_called_once_with(items_processed=1)
    assert "Music stage cleanup failed" in caplog.text
    assert "private-token-713" not in caplog.text


@pytest.mark.asyncio
async def test_mixer_failure_is_not_masked_by_final_stage_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The optional boundary reports the primary mixer failure, never cleanup fallout."""
    from immich_memories.generate_music import MusicPhaseResult
    from immich_memories.ui.pages._step4_music import apply_ai_music

    video_path = tmp_path / "memory.mp4"
    video_path.write_bytes(b"validated-base")
    staged_path = build_music_output_path(video_path)
    selected = SimpleNamespace(full_mix=tmp_path / "music.wav", stems=None)
    music_result = SimpleNamespace(
        versions=[selected],
        selected=selected,
        cleanup_unselected=MagicMock(),
    )
    monkeypatch.setattr(
        "immich_memories.ui.state.get_app_state",
        lambda: SimpleNamespace(music_preview_result=music_result),
    )

    def fail_mix(**kwargs: object) -> None:
        cast(Path, kwargs["output_path"]).write_bytes(b"partial-mix")
        raise RuntimeError("primary mixer failed")

    async def io_bound(callback: Callable[..., object], **kwargs: object) -> object:
        return callback(**kwargs)

    real_unlink = Path.unlink
    stage_unlinks = 0

    def fail_final_stage_unlink(path: Path, missing_ok: bool = False) -> None:
        nonlocal stage_unlinks
        if path == staged_path:
            stage_unlinks += 1
            if stage_unlinks == 2:
                raise OSError("secondary cleanup leaked private-token-935")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr("immich_memories.audio.mixer.mix_audio_with_ducking", fail_mix)
    monkeypatch.setattr("immich_memories.ui.pages._step4_music.run.io_bound", io_bound)
    monkeypatch.setattr(Path, "unlink", fail_final_stage_unlink)
    monkeypatch.setattr("immich_memories.ui.pages._step4_music.ui.notify", MagicMock())
    caplog.set_level("DEBUG")

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

    assert result == MusicPhaseResult(
        applied=False,
        warning="Optional music failed: primary mixer failed",
    )
    assert video_path.read_bytes() == b"validated-base"
    assert "secondary cleanup leaked private-token-935" not in caplog.text


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


@pytest.mark.asyncio
async def test_uploaded_success_survives_temporary_music_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Temporary-upload cleanup cannot override an already-published result."""
    from immich_memories.generate_music import MusicPhaseResult
    from immich_memories.ui.pages._step4_music import apply_uploaded_music

    video_path = tmp_path / "memory.mp4"
    video_path.write_bytes(b"validated-base")
    temporary_music_path = tmp_path / "uploaded.mp3"

    def named_temporary_file(**_kwargs: object):
        return temporary_music_path.open("w+b")

    def write_mix(**kwargs: object) -> None:
        cast(Path, kwargs["output_path"]).write_bytes(b"validated-mix")

    def publish_mix(video_path: Path, encoding_plan: EncodingPlan) -> None:
        del encoding_plan
        build_music_output_path(video_path).replace(video_path)

    async def io_bound(callback: Callable[..., object], **kwargs: object) -> object:
        return callback(**kwargs)

    real_unlink = Path.unlink

    def fail_temporary_unlink(path: Path, missing_ok: bool = False) -> None:
        if path == temporary_music_path:
            raise OSError("temporary cleanup leaked private-token-824")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr("tempfile.NamedTemporaryFile", named_temporary_file)
    monkeypatch.setattr("immich_memories.audio.mixer.mix_audio_with_ducking", write_mix)
    monkeypatch.setattr("immich_memories.generate_music.publish_music_mix", publish_mix)
    monkeypatch.setattr("immich_memories.ui.pages._step4_music.run.io_bound", io_bound)
    monkeypatch.setattr(Path, "unlink", fail_temporary_unlink)
    monkeypatch.setattr("immich_memories.ui.pages._step4_music.ui.notify", MagicMock())
    tracker = MagicMock()
    caplog.set_level("DEBUG")

    result = await apply_uploaded_music(
        video_path,
        gen_options={"music_file": b"uploaded", "music_volume": 0.5},
        run_tracker=tracker,
        progress_bar=_Progress(),
        status_label=_Status(),
        encoding_plan=_h264_plan(),
        config=Config(),
    )

    assert result == MusicPhaseResult(applied=True)
    assert video_path.read_bytes() == b"validated-mix"
    tracker.complete_phase.assert_called_once_with(items_processed=1)
    assert "Temporary music cleanup failed" in caplog.text
    assert "private-token-824" not in caplog.text


@pytest.mark.asyncio
async def test_uploaded_mixer_failure_is_not_masked_by_temporary_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Uploaded music reports the primary mixer error when temp cleanup also fails."""
    from immich_memories.generate_music import MusicPhaseResult
    from immich_memories.ui.pages._step4_music import apply_uploaded_music

    video_path = tmp_path / "memory.mp4"
    video_path.write_bytes(b"validated-base")
    temporary_music_path = tmp_path / "uploaded.mp3"

    def named_temporary_file(**_kwargs: object):
        return temporary_music_path.open("w+b")

    def fail_mix(**kwargs: object) -> None:
        cast(Path, kwargs["output_path"]).write_bytes(b"partial-mix")
        raise RuntimeError("primary uploaded mixer failed")

    async def io_bound(callback: Callable[..., object], **kwargs: object) -> object:
        return callback(**kwargs)

    real_unlink = Path.unlink

    def fail_temporary_unlink(path: Path, missing_ok: bool = False) -> None:
        if path == temporary_music_path:
            raise OSError("secondary cleanup leaked private-token-146")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr("tempfile.NamedTemporaryFile", named_temporary_file)
    monkeypatch.setattr("immich_memories.audio.mixer.mix_audio_with_ducking", fail_mix)
    monkeypatch.setattr("immich_memories.ui.pages._step4_music.run.io_bound", io_bound)
    monkeypatch.setattr(Path, "unlink", fail_temporary_unlink)
    monkeypatch.setattr("immich_memories.ui.pages._step4_music.ui.notify", MagicMock())
    caplog.set_level("DEBUG")

    result = await apply_uploaded_music(
        video_path,
        gen_options={"music_file": b"uploaded", "music_volume": 0.5},
        run_tracker=MagicMock(),
        progress_bar=_Progress(),
        status_label=_Status(),
        encoding_plan=_h264_plan(),
        config=Config(),
    )

    assert result == MusicPhaseResult(
        applied=False,
        warning="Optional music failed: primary uploaded mixer failed",
    )
    assert video_path.read_bytes() == b"validated-base"
    assert "secondary cleanup leaked private-token-146" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("music_source", ["AI Generated", "Upload file"])
async def test_post_publication_cleanup_failure_does_not_block_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    music_source: str,
) -> None:
    """Both post-publication cleanup failures still reach the independent upload phase."""
    from immich_memories.generate import GenerationParams, PreparedGeneration
    from immich_memories.processing.output_contract import OutputProbe
    from immich_memories.tracking import DeliveryStatus
    from immich_memories.ui.pages._step4_generate import finalize_ui_generation

    video_path = tmp_path / "memory.mp4"
    video_path.write_bytes(b"validated-base")
    staged_path = build_music_output_path(video_path)
    temporary_music_path = tmp_path / "uploaded.mp3"
    selected = SimpleNamespace(full_mix=tmp_path / "music.wav", stems=None)
    music_result = SimpleNamespace(
        versions=[selected],
        selected=selected,
        cleanup_unselected=MagicMock(),
    )
    monkeypatch.setattr(
        "immich_memories.ui.state.get_app_state",
        lambda: SimpleNamespace(music_preview_result=music_result),
    )

    def named_temporary_file(**_kwargs: object):
        return temporary_music_path.open("w+b")

    def write_mix(**kwargs: object) -> None:
        cast(Path, kwargs["output_path"]).write_bytes(b"validated-mix")

    def publish_mix(video_path: Path, encoding_plan: EncodingPlan) -> None:
        del encoding_plan
        build_music_output_path(video_path).replace(video_path)

    async def io_bound(
        callback: Callable[..., object],
        *args: object,
        **kwargs: object,
    ) -> object:
        return callback(*args, **kwargs)

    real_unlink = Path.unlink
    stage_unlinks = 0

    def fail_post_publication_cleanup(path: Path, missing_ok: bool = False) -> None:
        nonlocal stage_unlinks
        if path == staged_path:
            stage_unlinks += 1
            if music_source == "AI Generated" and stage_unlinks == 2:
                raise OSError("final stage cleanup failed")
        if path == temporary_music_path and music_source == "Upload file":
            raise OSError("temporary upload cleanup failed")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr("tempfile.NamedTemporaryFile", named_temporary_file)
    monkeypatch.setattr("immich_memories.audio.mixer.mix_audio_with_ducking", write_mix)
    monkeypatch.setattr("immich_memories.generate_music.publish_music_mix", publish_mix)
    monkeypatch.setattr(
        "immich_memories.generate_music.derive_music_validation_plan",
        lambda _path: _h264_plan(),
    )
    monkeypatch.setattr("immich_memories.ui.pages._step4_music.run.io_bound", io_bound)
    monkeypatch.setattr(Path, "unlink", fail_post_publication_cleanup)
    monkeypatch.setattr("immich_memories.ui.pages._step4_music.ui.notify", MagicMock())
    upload = AsyncMock()
    monkeypatch.setattr(
        "immich_memories.ui.pages._step4_upload.upload_to_immich",
        upload,
    )
    tracker = MagicMock()
    completed = SimpleNamespace(delivery_status=DeliveryStatus.PENDING)
    tracker.complete_artifact.return_value = completed
    upload.return_value = completed
    progress = _Progress()
    status = _Status()
    state = SimpleNamespace(
        generation_options={
            "music_source": music_source,
            "music_file": b"uploaded",
            "music_volume": 0.5,
        },
        memory_type=None,
        upload_enabled=True,
        generation_warning=None,
        delivery_status=DeliveryStatus.NOT_REQUESTED,
        upload_result=None,
    )
    plan = _h264_plan()
    prepared = PreparedGeneration(video_path, plan, (), 1, 1)
    params = GenerationParams(
        clips=[],
        output_path=video_path,
        config=Config(),
        client=object(),  # type: ignore[arg-type]
        upload_enabled=True,
        upload_album="Cleanup Album",
    )
    final_probe = OutputProbe(
        codec="h264",
        container="mp4",
        duration_seconds=5.0,
        size_bytes=1024,
        pixel_format="yuv420p",
        color_transfer="bt709",
        color_primaries="bt709",
        width=1920,
        height=1080,
        decoded_frames=120,
    )
    monkeypatch.setattr(
        "immich_memories.ui.pages._step4_generate.validate_output",
        lambda _path, _encoding_plan: final_probe,
    )

    await finalize_ui_generation(
        state,
        params,
        prepared,
        tracker,
        progress,
        status,
    )

    assert video_path.read_bytes() == b"validated-mix"
    tracker.complete_phase.assert_called_once_with(items_processed=1)
    tracker.complete_artifact.assert_called_once_with(
        video_path,
        final_probe,
        [],
        delivery_requested=True,
        delivery_album="Cleanup Album",
        clips_analyzed=1,
        clips_selected=1,
    )
    upload.assert_awaited_once_with(video_path, state, params, tracker, progress, status)
