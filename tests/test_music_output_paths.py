"""Container-preserving music output path regressions."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from immich_memories.config_loader import Config
from immich_memories.generate import GenerationParams
from tests.conftest import make_clip


def _h264_output_plan():
    from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec

    return EncodingPlan(
        codec=OutputCodec.H264,
        encoder="libx264",
        encoder_args=("-c:v", "libx264"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv420p",
        container="mp4",
    )


def _prores_output_plan():
    from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec

    return EncodingPlan(
        codec=OutputCodec.PRORES,
        encoder="prores_ks",
        encoder_args=("-c:v", "prores_ks"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv422p10le",
        container="mov",
    )


def _publish_fake_music_mix(video_path: Path, encoding_plan: object) -> None:
    """Emulate successful validated publication for path-only unit tests."""
    container = encoding_plan.container
    video_path.with_suffix(f".with_music.{container}").replace(video_path)


def _final_probe_payload(*, codec: str = "h264") -> dict[str, object]:
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": codec,
                "pix_fmt": "yuv420p",
                "color_transfer": "bt709",
                "color_primaries": "bt709",
                "width": 1920,
                "height": 1080,
                "nb_read_frames": "360",
            }
        ],
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "12.0",
            "size": "4096",
            "tags": {"major_brand": "isom"},
        },
    }


def test_music_validation_contract_comes_from_published_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UI music validates against artifact truth, not mutable UI selection state."""
    from immich_memories.generate_music import derive_music_validation_plan
    from immich_memories.processing.encoding_plan import HdrTransfer, OutputCodec
    from immich_memories.processing.output_contract import OutputProbe

    base = tmp_path / "memory.mov"
    base.write_bytes(b"published-base")
    monkeypatch.setattr(
        "immich_memories.generate_music.probe_output",
        MagicMock(
            return_value=OutputProbe(
                codec="prores",
                container="mov",
                duration_seconds=5.0,
                size_bytes=1024,
                pixel_format="yuv422p10le",
                color_transfer="bt709",
                color_primaries="bt709",
                width=1920,
                height=1080,
                decoded_frames=120,
            )
        ),
    )

    plan = derive_music_validation_plan(base)

    assert plan.codec is OutputCodec.PRORES
    assert plan.container == "mov"
    assert plan.pixel_format == "yuv422p10le"
    assert plan.target_transfer is HdrTransfer.NONE


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("codec", "vp9", "unsupported published codec"),
        ("container", "matroska", "unsupported published container"),
        ("pixel_format", "gbrp", "unsupported published pixel format"),
        ("color_transfer", None, "unsupported published color transfer"),
    ],
)
def test_music_validation_contract_rejects_unsupported_artifact_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    message: str,
) -> None:
    from dataclasses import replace

    from immich_memories.generate_music import derive_music_validation_plan
    from immich_memories.processing.output_contract import InvalidOutputArtifact, OutputProbe

    base = tmp_path / "memory.mp4"
    base.write_bytes(b"published-base")
    probe = OutputProbe(
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
        "immich_memories.generate_music.probe_output",
        MagicMock(return_value=replace(probe, **{field: value})),
    )

    with pytest.raises(InvalidOutputArtifact, match=message):
        derive_music_validation_plan(base)


def test_music_mix_drifting_from_base_identity_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from immich_memories.generate_music import apply_music_file, derive_music_validation_plan
    from immich_memories.processing import output_contract
    from immich_memories.processing.output_contract import InvalidOutputArtifact, OutputProbe

    video = tmp_path / "memory.mp4"
    music = tmp_path / "music.wav"
    video.write_bytes(b"validated-h264-base")
    music.write_bytes(b"music")
    monkeypatch.setattr(
        "immich_memories.generate_music.probe_output",
        MagicMock(
            return_value=OutputProbe(
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
        ),
    )
    plan = derive_music_validation_plan(video)

    def write_mix(*, output_path: Path, **_kwargs: object) -> None:
        output_path.write_bytes(b"drifted-hevc-mix")

    monkeypatch.setattr(
        "immich_memories.audio.mixer.mix_audio_with_ducking",
        write_mix,
    )
    monkeypatch.setattr(
        "immich_memories.generate_music._require_audio_stream",
        lambda _path: None,
    )
    monkeypatch.setattr(
        output_contract.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, json.dumps(_final_probe_payload(codec="hevc")), ""
        ),
    )

    with pytest.raises(InvalidOutputArtifact, match="expected h264, got hevc"):
        apply_music_file(video, music, volume=0.5, encoding_plan=plan)

    assert video.read_bytes() == b"validated-h264-base"


def test_music_publication_requires_positive_decoded_audio_frames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A staged mix is publishable only after one decoded-audio frame count."""
    from immich_memories.filename_builder import build_music_output_path
    from immich_memories.generate_music import publish_music_mix
    from immich_memories.processing.output_contract import OutputProbe

    video = tmp_path / "memory.mp4"
    video.write_bytes(b"validated-base")
    build_music_output_path(video).write_bytes(b"staged-mix")
    commands: list[list[str]] = []
    probe_kwargs: list[dict[str, object]] = []

    def audio_probe(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        probe_kwargs.append(kwargs)
        frame_count = "24" if "-count_frames" in command else "N/A"
        payload = {"streams": [{"codec_type": "audio", "nb_read_frames": frame_count}]}
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    expected = OutputProbe(
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
    monkeypatch.setattr("immich_memories.generate_music.subprocess.run", audio_probe)
    monkeypatch.setattr(
        "immich_memories.generate_music.publish_validated_output",
        MagicMock(return_value=expected),
    )

    result = publish_music_mix(video, _h264_output_plan())

    assert result == expected
    assert len(commands) == 1
    assert probe_kwargs == [
        {
            "capture_output": True,
            "text": True,
            "timeout": 15 * 60,
            "check": False,
        }
    ]


@pytest.mark.parametrize(
    ("payload", "returncode", "stderr"),
    [
        ({"streams": [{"codec_type": "audio", "nb_read_frames": "12"}]}, 1, ""),
        ({"streams": [], "format": {}}, 0, ""),
        ({"streams": [{"codec_type": "audio", "nb_read_frames": "N/A"}]}, 0, ""),
        ({"streams": [{"codec_type": "audio", "nb_read_frames": "0"}]}, 0, ""),
        ({"streams": [{"codec_type": "audio", "nb_read_frames": "wat"}]}, 0, ""),
        ({"streams": [{"codec_type": "audio", "nb_read_frames": True}]}, 0, ""),
        (
            {"streams": [{"codec_type": "audio", "nb_read_frames": "12"}]},
            0,
            "decode error",
        ),
    ],
    ids=["nonzero", "missing", "na", "zero", "malformed", "boolean", "stderr"],
)
def test_music_publication_rejects_unproven_audio_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
    returncode: int,
    stderr: str,
) -> None:
    """Missing, malformed, or errored audio decode evidence never publishes."""
    from immich_memories.filename_builder import build_music_output_path
    from immich_memories.generate_music import publish_music_mix
    from immich_memories.processing.output_contract import InvalidOutputArtifact

    video = tmp_path / "memory.mp4"
    video.write_bytes(b"validated-base")
    build_music_output_path(video).write_bytes(b"staged-mix")
    monkeypatch.setattr(
        "immich_memories.generate_music.subprocess.run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            returncode,
            json.dumps(payload),
            stderr,
        ),
    )
    monkeypatch.setattr(
        "immich_memories.generate_music.publish_validated_output",
        lambda _staged_path, _final_path, _plan: None,
    )

    with pytest.raises(InvalidOutputArtifact):
        publish_music_mix(video, _h264_output_plan())

    assert video.read_bytes() == b"validated-base"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("music_source", "target"),
    [
        ("AI Generated", "immich_memories.ui.pages._step4_music.apply_ai_music"),
        ("Upload file", "immich_memories.ui.pages._step4_music.apply_uploaded_music"),
    ],
)
async def test_ui_music_conduit_forwards_one_artifact_validation_plan(
    tmp_path: Path,
    music_source: str,
    target: str,
) -> None:
    from immich_memories.ui.pages._step4_generate import _apply_music

    plan = _h264_output_plan()
    state = SimpleNamespace(
        generation_options={
            "music_source": music_source,
            "music_file": b"uploaded",
        },
        clip_segments={},
        memory_type=None,
    )

    with (
        patch(target, new_callable=AsyncMock) as music_helper,
    ):
        await _apply_music(
            state,
            Config(),
            tmp_path / "memory.mp4",
            [],
            tmp_path,
            None,
            _Progress(),
            _Status(),
            encoding_plan=plan,
        )

    assert music_helper.await_args.kwargs["encoding_plan"] is plan


@pytest.mark.asyncio
async def test_ui_music_none_skips_core_ui_music_and_tracker_writes(tmp_path: Path) -> None:
    """None returns without deriving, mixing, or creating detached phase tracking."""
    from immich_memories.generate_music import MusicPhaseResult
    from immich_memories.ui.pages._step4_generate import _run_ui_music_phase

    state = SimpleNamespace(generation_options={"music_source": "None"}, memory_type=None)
    with (
        patch(
            "immich_memories.ui.pages._step4_generate.run.io_bound", new_callable=AsyncMock
        ) as io_bound,
        patch(
            "immich_memories.ui.pages._step4_generate._apply_music", new_callable=AsyncMock
        ) as apply_music,
    ):
        result = await _run_ui_music_phase(
            state,
            Config(),
            tmp_path / "memory.mp4",
            [],
            tmp_path,
            _Progress(),
            _Status(),
        )

    assert result == MusicPhaseResult(applied=False)
    io_bound.assert_not_awaited()
    apply_music.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("music_source", ["AI Generated", "Upload file"])
async def test_ui_music_source_applies_exactly_once_without_detached_tracker(
    tmp_path: Path,
    music_source: str,
) -> None:
    """The selected UI source is applied once after plan derivation, with no fresh tracker."""
    from immich_memories.generate_music import MusicPhaseResult
    from immich_memories.ui.pages._step4_generate import _run_ui_music_phase

    plan = _h264_output_plan()
    state = SimpleNamespace(
        generation_options={"music_source": music_source, "music_file": b"uploaded"},
        memory_type=None,
    )

    async def io_bound(
        _callback: Callable[..., object], *_args: object, **_kwargs: object
    ) -> object:
        return plan

    with (
        patch("immich_memories.ui.pages._step4_generate.run.io_bound", side_effect=io_bound),
        patch(
            "immich_memories.ui.pages._step4_generate._apply_music",
            new_callable=AsyncMock,
            return_value=MusicPhaseResult(applied=True),
        ) as apply_music,
    ):
        result = await _run_ui_music_phase(
            state,
            Config(),
            tmp_path / "memory.mp4",
            [],
            tmp_path,
            _Progress(),
            _Status(),
        )

    assert result == MusicPhaseResult(applied=True)
    apply_music.assert_awaited_once()
    assert len(apply_music.await_args.args) == 8


@pytest.mark.asyncio
async def test_ui_music_plan_failure_preserves_base_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Optional plan derivation failure cannot invalidate the valid base artifact."""
    from immich_memories.generate_music import MusicPhaseResult
    from immich_memories.ui.pages._step4_generate import _run_ui_music_phase

    result_path = tmp_path / "memory.mp4"
    result_path.write_bytes(b"validated-base")
    state = SimpleNamespace(
        generation_options={"music_source": "AI Generated"},
        memory_type=None,
    )

    async def io_bound(
        _callback: Callable[..., object], *_args: object, **_kwargs: object
    ) -> object:
        raise RuntimeError("probe backend unavailable")

    monkeypatch.setattr("immich_memories.ui.pages._step4_generate.run.io_bound", io_bound)
    monkeypatch.setattr("immich_memories.ui.pages._step4_generate.ui.notify", MagicMock())

    result = await _run_ui_music_phase(
        state,
        Config(),
        result_path,
        [],
        tmp_path,
        _Progress(),
        _Status(),
    )

    assert result == MusicPhaseResult(
        applied=False,
        warning="Optional music failed: probe backend unavailable",
    )
    assert result_path.read_bytes() == b"validated-base"


@pytest.mark.asyncio
async def test_ui_music_plan_failure_is_sanitized_without_detached_tracking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed optional plan keeps the base and emits one redacted UI warning."""
    from immich_memories.generate_music import MusicPhaseResult
    from immich_memories.ui.pages._step4_generate import _run_ui_music_phase

    credential_value = "probe-secret-427"
    config = Config()
    config.musicgen.api_key = credential_value
    result_path = tmp_path / "memory.mp4"
    result_path.write_bytes(b"validated-base")
    state = SimpleNamespace(
        generation_options={"music_source": "Upload file", "music_file": b"uploaded"},
        memory_type=None,
    )
    notify = MagicMock()

    async def io_bound(callback: Callable[..., object], *_args: object) -> object:
        return callback(result_path)

    monkeypatch.setattr(
        "immich_memories.generate_music.derive_music_validation_plan",
        MagicMock(side_effect=RuntimeError(f"probe rejected {credential_value}")),
    )
    monkeypatch.setattr("immich_memories.ui.pages._step4_generate.run.io_bound", io_bound)
    monkeypatch.setattr("immich_memories.ui.pages._step4_generate.ui.notify", notify)
    caplog.set_level("DEBUG")

    result = await _run_ui_music_phase(
        state,
        config,
        result_path,
        [],
        tmp_path,
        _Progress(),
        _Status(),
    )

    warning = "Optional music failed: probe rejected ***"
    assert result == MusicPhaseResult(applied=False, warning=warning)
    assert result_path.read_bytes() == b"validated-base"
    assert notify.call_args.args[0] == f"{warning}. Video saved without music."
    assert credential_value not in caplog.text
    assert credential_value not in str(notify.call_args)


@pytest.mark.asyncio
async def test_ui_upload_continues_after_optional_music_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A nonfatal music failure must not skip final validation or delivery."""
    from immich_memories.generate import PreparedGeneration
    from immich_memories.generate_music import MusicPhaseResult
    from immich_memories.processing.output_contract import OutputProbe
    from immich_memories.tracking import DeliveryStatus
    from immich_memories.ui.pages._step4_generate import finalize_ui_generation

    result_path = tmp_path / "memory.mp4"
    result_path.write_bytes(b"validated-base")
    state = SimpleNamespace(
        generation_options={
            "music_source": "Upload file",
            "music_file": b"uploaded",
        },
        memory_type=None,
        upload_enabled=True,
        generation_warning=None,
        delivery_status=DeliveryStatus.NOT_REQUESTED,
        upload_result=None,
    )
    plan = _h264_output_plan()
    prepared = PreparedGeneration(result_path, plan, (), 1, 1)
    params = GenerationParams(
        clips=[],
        output_path=result_path,
        config=Config(),
        client=object(),  # type: ignore[arg-type]
        upload_enabled=True,
        upload_album="UI Album",
    )
    warning = "Optional music failed: probe failed"
    music = AsyncMock(return_value=MusicPhaseResult(applied=False, warning=warning))
    monkeypatch.setattr("immich_memories.ui.pages._step4_generate._apply_music", music)
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

    async def io_bound(callback, *args, **kwargs):
        return callback(*args, **kwargs)

    monkeypatch.setattr("immich_memories.ui.pages._step4_generate.run.io_bound", io_bound)
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

    await finalize_ui_generation(
        state,
        params,
        prepared,
        tracker,
        progress,
        status,
    )

    music.assert_awaited_once()
    assert music.await_args.kwargs["encoding_plan"] is plan
    tracker.complete_artifact.assert_called_once_with(
        result_path,
        final_probe,
        [warning],
        delivery_requested=True,
        delivery_album="UI Album",
        clips_analyzed=1,
        clips_selected=1,
    )
    upload.assert_awaited_once_with(result_path, state, params, tracker, progress, status)
    assert result_path.read_bytes() == b"validated-base"


@pytest.mark.asyncio
async def test_ui_finalization_preserves_selected_clip_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deferred UI music receives original selections, not assembly internals."""
    from immich_memories.generate import PreparedGeneration
    from immich_memories.generate_music import MusicPhaseResult
    from immich_memories.tracking import DeliveryStatus
    from immich_memories.ui.pages._step4_generate import finalize_ui_generation

    result_path = tmp_path / "memory.mp4"
    result_path.write_bytes(b"validated-base")
    selected_clip = make_clip("clip-1", duration=9.0, file_created_at=datetime(2025, 7, 4))
    plan = _h264_output_plan()
    state = SimpleNamespace(
        generation_options={"music_source": "AI Generated"},
        memory_type=None,
        generation_warning=None,
        delivery_status=DeliveryStatus.NOT_REQUESTED,
    )
    params = GenerationParams(clips=[selected_clip], output_path=result_path, config=Config())
    prepared = PreparedGeneration(result_path, plan, (), 1, 1)
    music = AsyncMock(return_value=MusicPhaseResult(applied=False))
    monkeypatch.setattr("immich_memories.ui.pages._step4_generate._apply_music", music)
    monkeypatch.setattr(
        "immich_memories.ui.pages._step4_generate.validate_output", lambda *_args: MagicMock()
    )

    async def io_bound(callback, *args, **kwargs):
        return callback(*args, **kwargs)

    monkeypatch.setattr("immich_memories.ui.pages._step4_generate.run.io_bound", io_bound)
    tracker = MagicMock()
    tracker.complete_artifact.return_value = SimpleNamespace(
        delivery_status=DeliveryStatus.NOT_REQUESTED
    )

    await finalize_ui_generation(state, params, prepared, tracker, _Progress(), _Status())

    assert music.await_args.args[3] == [selected_clip]
    assert music.await_args.kwargs["encoding_plan"] is plan


@pytest.mark.asyncio
async def test_ai_cleanup_failure_does_not_undo_successful_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cleanup after atomic publication is best-effort, not phase failure."""
    from immich_memories.generate_music import MusicPhaseResult
    from immich_memories.ui.pages._step4_music import apply_ai_music

    result_path = tmp_path / "memory.mp4"
    result_path.write_bytes(b"validated-base")
    selected = SimpleNamespace(full_mix=tmp_path / "music.wav", stems=None)
    music_result = SimpleNamespace(
        versions=[selected],
        selected=selected,
        cleanup_unselected=MagicMock(side_effect=RuntimeError("cleanup-secret-912")),
    )
    monkeypatch.setattr(
        "immich_memories.ui.state.get_app_state",
        lambda: SimpleNamespace(music_preview_result=music_result),
    )

    async def publish_mix(*_args: object, **_kwargs: object) -> None:
        result_path.write_bytes(b"validated-mix")

    monkeypatch.setattr(
        "immich_memories.ui.pages._step4_music._mix_selected_ai_music",
        publish_mix,
    )
    monkeypatch.setattr("immich_memories.ui.pages._step4_music.ui.notify", MagicMock())
    tracker = MagicMock()
    caplog.set_level("DEBUG")

    result = await apply_ai_music(
        result_path,
        [],
        {},
        {"music_volume": 0.5},
        Config(),
        tmp_path,
        tracker,
        _Progress(),
        _Status(),
        encoding_plan=_h264_output_plan(),
    )

    assert result == MusicPhaseResult(applied=True)
    assert result_path.read_bytes() == b"validated-mix"
    tracker.complete_phase.assert_called_once_with(items_processed=1)
    assert "cleanup-secret-912" not in caplog.text


def _prores_plan():
    from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec

    return EncodingPlan(
        codec=OutputCodec.PRORES,
        encoder="prores_ks",
        encoder_args=("-profile:v", "3"),
        target_transfer=HdrTransfer.NONE,
        tone_map_to_sdr=False,
        pixel_format="yuv422p10le",
        container="mov",
    )


class _RunTracker:
    def start_phase(self, _name: str, _total: int) -> None:
        pass

    def complete_phase(self, *, items_processed: int) -> None:
        del items_processed


class _Progress:
    value = 0.0


class _Status:
    def set_text(self, _text: str) -> None:
        pass


def test_apply_music_file_preserves_mov_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shared music application must mix through memory.with_music.mov."""
    from immich_memories.generate_music import apply_music_file

    video_path = tmp_path / "memory.mov"
    music_path = tmp_path / "music.wav"
    video_path.write_bytes(b"video")
    music_path.write_bytes(b"music")

    def write_output_name(
        *,
        video_path: Path,
        music_path: Path,
        output_path: Path,
        config: object,
    ) -> None:
        del video_path, music_path, config
        output_path.write_text(output_path.name)

    monkeypatch.setattr(
        "immich_memories.audio.mixer.mix_audio_with_ducking",
        write_output_name,
    )
    monkeypatch.setattr(
        "immich_memories.generate_music.publish_music_mix",
        _publish_fake_music_mix,
    )

    apply_music_file(
        video_path,
        music_path,
        volume=0.5,
        encoding_plan=_prores_output_plan(),
    )

    assert video_path.read_text() == "memory.with_music.mov"


@pytest.mark.asyncio
@pytest.mark.parametrize("use_four_stems", [False, True], ids=["full-mix", "four-stem"])
async def test_ai_music_preserves_mov_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    use_four_stems: bool,
) -> None:
    """Generated full-mix and four-stem paths must keep the MOV container."""
    from immich_memories.ui.pages._step4_music import apply_ai_music

    result_path = tmp_path / "memory.mov"
    result_path.write_bytes(b"video")
    stems = None
    if use_four_stems:
        stems = SimpleNamespace(
            drums=tmp_path / "drums.wav",
            bass=tmp_path / "bass.wav",
            vocals=tmp_path / "vocals.wav",
            other=tmp_path / "other.wav",
        )
    selected_music = SimpleNamespace(full_mix=tmp_path / "music.wav", stems=stems)
    music_result = SimpleNamespace(
        versions=[selected_music],
        selected=selected_music,
        cleanup_unselected=lambda: None,
    )
    monkeypatch.setattr(
        "immich_memories.ui.state.get_app_state",
        lambda: SimpleNamespace(music_preview_result=music_result),
    )

    def write_full_mix(**kwargs: object) -> None:
        output_path = cast(Path, kwargs["output_path"])
        output_path.write_text(f"full-mix:{output_path.name}")

    def write_four_stem(**kwargs: object) -> None:
        output_path = cast(Path, kwargs["output_path"])
        output_path.write_text(f"four-stem:{output_path.name}")

    async def io_bound(callback: Callable[..., object], **kwargs: object) -> object:
        return callback(**kwargs)

    monkeypatch.setattr(
        "immich_memories.audio.mixer.mix_audio_with_ducking",
        write_full_mix,
    )
    monkeypatch.setattr(
        "immich_memories.audio.mixer_helpers.mix_audio_with_4stem_ducking",
        write_four_stem,
    )
    monkeypatch.setattr(
        "immich_memories.ui.pages._step4_music.run.io_bound",
        io_bound,
    )
    monkeypatch.setattr(
        "immich_memories.generate_music.publish_music_mix",
        _publish_fake_music_mix,
    )

    await apply_ai_music(
        result_path,
        selected_clips=[],
        clip_segments={},
        gen_options={"music_volume": 0.5},
        config=object(),
        run_output_dir=tmp_path,
        run_tracker=_RunTracker(),
        progress_bar=_Progress(),
        status_label=_Status(),
        encoding_plan=_prores_output_plan(),
    )

    mixer = "four-stem" if use_four_stems else "full-mix"
    assert result_path.read_text() == f"{mixer}:memory.with_music.mov"


@pytest.mark.asyncio
async def test_uploaded_music_preserves_mov_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uploaded music must mix through memory.with_music.mov."""
    from immich_memories.ui.pages._step4_music import apply_uploaded_music

    result_path = tmp_path / "memory.mov"
    result_path.write_bytes(b"video")

    def write_uploaded_mix(**kwargs: object) -> None:
        output_path = cast(Path, kwargs["output_path"])
        output_path.write_text(f"uploaded:{output_path.name}")

    async def io_bound(callback: Callable[..., object], **kwargs: object) -> object:
        return callback(**kwargs)

    monkeypatch.setattr(
        "immich_memories.audio.mixer.mix_audio_with_ducking",
        write_uploaded_mix,
    )
    monkeypatch.setattr(
        "immich_memories.ui.pages._step4_music.run.io_bound",
        io_bound,
    )
    monkeypatch.setattr(
        "immich_memories.generate_music.publish_music_mix",
        _publish_fake_music_mix,
    )

    await apply_uploaded_music(
        result_path,
        gen_options={"music_file": b"uploaded", "music_volume": 0.5},
        run_tracker=_RunTracker(),
        progress_bar=_Progress(),
        status_label=_Status(),
        encoding_plan=_prores_output_plan(),
        config=Config(),
    )

    assert result_path.read_text() == "uploaded:memory.with_music.mov"


@pytest.mark.asyncio
async def test_uploaded_invalid_mix_preserves_valid_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UI upload music must validate before replacing the base artifact."""
    from immich_memories.generate_music import MusicPhaseResult
    from immich_memories.processing.output_contract import InvalidOutputArtifact
    from immich_memories.ui.pages._step4_music import apply_uploaded_music

    result_path = tmp_path / "memory.mp4"
    result_path.write_bytes(b"validated-base")

    def write_invalid_mix(**kwargs: object) -> None:
        cast(Path, kwargs["output_path"]).write_bytes(b"invalid-mix")

    async def io_bound(callback: Callable[..., object], **kwargs: object) -> object:
        return callback(**kwargs)

    monkeypatch.setattr(
        "immich_memories.audio.mixer.mix_audio_with_ducking",
        write_invalid_mix,
    )
    monkeypatch.setattr(
        "immich_memories.ui.pages._step4_music.run.io_bound",
        io_bound,
    )
    monkeypatch.setattr(
        "immich_memories.generate_music.publish_music_mix",
        MagicMock(side_effect=InvalidOutputArtifact("missing audio/video stream")),
    )
    monkeypatch.setattr(
        "immich_memories.ui.pages._step4_music.ui.notify",
        MagicMock(),
    )
    tracker = MagicMock()

    result = await apply_uploaded_music(
        result_path,
        gen_options={"music_file": b"uploaded", "music_volume": 0.5},
        run_tracker=tracker,
        progress_bar=_Progress(),
        status_label=_Status(),
        encoding_plan=_h264_output_plan(),
        config=Config(),
    )

    warning = "Optional music failed: missing audio/video stream"
    assert result == MusicPhaseResult(applied=False, warning=warning)
    assert result_path.read_bytes() == b"validated-base"
    tracker.complete_phase.assert_called_once_with(
        items_processed=0,
        errors=[{"error": warning}],
    )


@pytest.mark.asyncio
async def test_uploaded_music_warning_redacts_unlabelled_configured_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Uploaded-music errors redact config literals in every user-visible sink."""
    from immich_memories.generate_music import MusicPhaseResult
    from immich_memories.ui.pages._step4_music import apply_uploaded_music

    result_path = tmp_path / "memory.mp4"
    result_path.write_bytes(b"validated-base")
    config = Config()
    config.musicgen.api_key = "literal-secret-881"

    async def io_bound(callback: Callable[..., object], **kwargs: object) -> object:
        return callback(**kwargs)

    def write_mix(**kwargs: object) -> None:
        cast(Path, kwargs["output_path"]).write_bytes(b"staged-mix")

    notify = MagicMock()
    tracker = MagicMock()
    monkeypatch.setattr("immich_memories.audio.mixer.mix_audio_with_ducking", write_mix)
    monkeypatch.setattr("immich_memories.ui.pages._step4_music.run.io_bound", io_bound)
    monkeypatch.setattr(
        "immich_memories.generate_music.publish_music_mix",
        MagicMock(side_effect=RuntimeError("upload rejected literal-secret-881")),
    )
    monkeypatch.setattr("immich_memories.ui.pages._step4_music.ui.notify", notify)
    caplog.set_level("DEBUG")

    result = await apply_uploaded_music(
        result_path,
        gen_options={"music_file": b"uploaded", "music_volume": 0.5},
        config=config,
        run_tracker=tracker,
        progress_bar=_Progress(),
        status_label=_Status(),
        encoding_plan=_h264_output_plan(),
    )

    warning = "Optional music failed: upload rejected ***"
    assert result == MusicPhaseResult(applied=False, warning=warning)
    assert warning in caplog.text
    assert notify.call_args.args[0] == f"{warning}. Video saved without music."
    tracker.complete_phase.assert_called_once_with(
        items_processed=0,
        errors=[{"error": warning}],
    )
    assert "literal-secret-881" not in caplog.text
    assert "literal-secret-881" not in str(notify.call_args)
    assert "literal-secret-881" not in str(tracker.complete_phase.call_args)


@pytest.mark.asyncio
async def test_uploaded_music_rejects_base_container_that_disagrees_with_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UI must reject a stale base suffix before creating a mismatched staged mix."""
    from immich_memories.generate_music import MusicPhaseResult
    from immich_memories.ui.pages._step4_music import apply_uploaded_music

    result_path = tmp_path / "memory.mp4"
    result_path.write_bytes(b"validated-base")

    async def io_bound(callback: Callable[..., object], **kwargs: object) -> object:
        return callback(**kwargs)

    def write_mix(**kwargs: object) -> None:
        cast(Path, kwargs["output_path"]).write_bytes(b"should-not-be-written")

    monkeypatch.setattr("immich_memories.audio.mixer.mix_audio_with_ducking", write_mix)
    monkeypatch.setattr("immich_memories.ui.pages._step4_music.run.io_bound", io_bound)
    monkeypatch.setattr("immich_memories.ui.pages._step4_music.ui.notify", MagicMock())

    result = await apply_uploaded_music(
        result_path,
        gen_options={"music_file": b"uploaded", "music_volume": 0.5},
        config=Config(),
        run_tracker=MagicMock(),
        progress_bar=_Progress(),
        status_label=_Status(),
        encoding_plan=_prores_output_plan(),
    )

    warning = "Optional music failed: Music input suffix '.mp4' does not match encoding plan container 'mov'"
    assert result == MusicPhaseResult(applied=False, warning=warning)
    assert result_path.read_bytes() == b"validated-base"
    assert not (tmp_path / "memory.with_music.mp4").exists()


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg and ffprobe are required",
)
def test_real_prores_music_mix_stays_mov(tmp_path: Path) -> None:
    """Real mixing must preserve ProRes video and add AAC audio in the MOV output."""
    from immich_memories.generate_music import apply_music_file

    video_path = tmp_path / "memory.mov"
    music_path = tmp_path / "music.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=10:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-vf",
            "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "1",
            "-pix_fmt",
            "yuv422p10le",
            "-c:a",
            "pcm_s16le",
            str(video_path),
        ],
        capture_output=True,
        check=True,
        timeout=60,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=4",
            "-c:a",
            "pcm_s16le",
            str(music_path),
        ],
        capture_output=True,
        check=True,
        timeout=60,
    )

    apply_music_file(
        video_path,
        music_path,
        volume=0.5,
        encoding_plan=_prores_output_plan(),
    )

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name",
            "-of",
            "json",
            str(video_path),
        ],
        capture_output=True,
        check=True,
        text=True,
        timeout=30,
    )
    streams = json.loads(probe.stdout)["streams"]
    assert video_path.suffix == ".mov"
    assert {stream["codec_type"]: stream["codec_name"] for stream in streams} == {
        "video": "prores",
        "audio": "aac",
    }
    assert not (tmp_path / "memory.with_music.mp4").exists()


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg and ffprobe are required",
)
def test_real_music_mix_without_audio_never_replaces_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A video-only staged artifact is not a successful music mix."""
    from immich_memories.generate_music import apply_music_file
    from immich_memories.processing.output_contract import InvalidOutputArtifact

    video_path = tmp_path / "memory.mp4"
    music_path = tmp_path / "music.wav"
    music_path.write_bytes(b"unused")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=10:duration=1",
            "-vf",
            "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(video_path),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    original = video_path.read_bytes()

    def copy_video_only(*, output_path: Path, **_kwargs: object) -> None:
        output_path.write_bytes(original)

    monkeypatch.setattr(
        "immich_memories.audio.mixer.mix_audio_with_ducking",
        copy_video_only,
    )

    with pytest.raises(InvalidOutputArtifact, match="missing audio stream"):
        apply_music_file(
            video_path,
            music_path,
            volume=0.5,
            encoding_plan=_h264_output_plan(),
        )

    assert video_path.read_bytes() == original


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg and ffprobe are required",
)
def test_real_truncated_aac_fails_decoded_audio_proof(tmp_path: Path) -> None:
    """A truncated AAC artifact cannot satisfy the full-decode music contract."""
    from immich_memories.generate_music import _require_audio_stream
    from immich_memories.processing.output_contract import InvalidOutputArtifact

    audio_path = tmp_path / "music.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:a",
            "aac",
            audio_path,
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    encoded = audio_path.read_bytes()
    audio_path.write_bytes(encoded[: len(encoded) // 2])

    with pytest.raises(InvalidOutputArtifact):
        _require_audio_stream(audio_path)


class TestApplyMusicFileAtomic:
    """Shared music publication keeps the validated base until proof succeeds."""

    def test_replaces_video_with_mixed_output(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from immich_memories.generate_music import apply_music_file
        from immich_memories.processing import output_contract

        video = tmp_path / "output.mp4"
        music = tmp_path / "music.wav"
        video.write_bytes(b"original video")
        music.write_bytes(b"music data")
        monkeypatch.setattr(
            output_contract.subprocess,
            "run",
            lambda command, **_kwargs: subprocess.CompletedProcess(
                command, 0, json.dumps(_final_probe_payload()), ""
            ),
        )

        def fake_mix(*, output_path: Path, **_kwargs: object) -> None:
            output_path.write_bytes(b"mixed video")

        monkeypatch.setattr(
            "immich_memories.audio.mixer.mix_audio_with_ducking",
            fake_mix,
        )
        monkeypatch.setattr(
            "immich_memories.generate_music._require_audio_stream",
            lambda _path: None,
        )
        apply_music_file(video, music, volume=0.8, encoding_plan=_h264_output_plan())

        assert video.read_bytes() == b"mixed video"
        assert not (tmp_path / "output.with_music.mp4").exists()

    def test_does_not_unlink_original_before_swap(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from immich_memories.generate_music import apply_music_file
        from immich_memories.processing import output_contract

        video = tmp_path / "output.mp4"
        music = tmp_path / "music.wav"
        video.write_bytes(b"original video")
        music.write_bytes(b"music data")
        monkeypatch.setattr(
            output_contract.subprocess,
            "run",
            lambda command, **_kwargs: subprocess.CompletedProcess(
                command, 0, json.dumps(_final_probe_payload()), ""
            ),
        )
        unlink_calls: list[Path] = []
        original_unlink = Path.unlink

        def tracking_unlink(self: Path, missing_ok: bool = False) -> None:
            unlink_calls.append(self)
            original_unlink(self, missing_ok=missing_ok)

        def fake_mix(*, output_path: Path, **_kwargs: object) -> None:
            output_path.write_bytes(b"mixed video")

        with patch.object(Path, "unlink", tracking_unlink):
            monkeypatch.setattr(
                "immich_memories.audio.mixer.mix_audio_with_ducking",
                fake_mix,
            )
            monkeypatch.setattr(
                "immich_memories.generate_music._require_audio_stream",
                lambda _path: None,
            )
            apply_music_file(video, music, volume=0.8, encoding_plan=_h264_output_plan())

        assert video not in unlink_calls

    def test_invalid_mix_never_replaces_valid_base(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from immich_memories.generate_music import apply_music_file
        from immich_memories.processing.output_contract import InvalidOutputArtifact

        video = tmp_path / "memory.mp4"
        music = tmp_path / "music.wav"
        video.write_bytes(b"validated-base")
        music.write_bytes(b"music")

        def write_invalid_mix(*, output_path: Path, **_kwargs: object) -> None:
            output_path.write_bytes(b"invalid-mix")

        monkeypatch.setattr(
            "immich_memories.audio.mixer.mix_audio_with_ducking",
            write_invalid_mix,
        )
        monkeypatch.setattr(
            "immich_memories.generate_music.publish_validated_output",
            MagicMock(side_effect=InvalidOutputArtifact("missing audio/video stream")),
        )
        monkeypatch.setattr(
            "immich_memories.generate_music._require_audio_stream",
            lambda _path: None,
        )

        with pytest.raises(InvalidOutputArtifact, match="missing audio/video stream"):
            apply_music_file(
                video,
                music,
                volume=0.8,
                encoding_plan=_h264_output_plan(),
            )

        assert video.read_bytes() == b"validated-base"
        assert not (tmp_path / "memory.with_music.mp4").exists()

    def test_validation_failure_survives_inner_stage_cleanup_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Stage cleanup cannot replace the validation failure or its chained cause."""
        from immich_memories.generate_music import apply_music_file, optional_music_warning
        from immich_memories.processing.output_contract import InvalidOutputArtifact

        video = tmp_path / "memory.mp4"
        music = tmp_path / "music.wav"
        staged = tmp_path / "memory.with_music.mp4"
        video.write_bytes(b"validated-base")
        music.write_bytes(b"music")
        config = Config()
        configured_value = "validation-secret-482"
        config.musicgen.api_key = configured_value
        validation_cause = RuntimeError("decoded video evidence unavailable")
        validation_error = InvalidOutputArtifact("invalid mix from validation-secret-482")
        validation_error.__cause__ = validation_cause

        def write_invalid_mix(*, output_path: Path, **_kwargs: object) -> None:
            output_path.write_bytes(b"invalid-mix")

        real_unlink = Path.unlink

        def fail_stage_cleanup(path: Path, missing_ok: bool = False) -> None:
            if path == staged and path.exists():
                raise OSError("cleanup leaked cleanup-secret-917")
            real_unlink(path, missing_ok=missing_ok)

        monkeypatch.setattr(
            "immich_memories.audio.mixer.mix_audio_with_ducking",
            write_invalid_mix,
        )
        monkeypatch.setattr(
            "immich_memories.generate_music.publish_validated_output",
            MagicMock(side_effect=validation_error),
        )
        monkeypatch.setattr(
            "immich_memories.generate_music._require_audio_stream",
            lambda _path: None,
        )
        monkeypatch.setattr(Path, "unlink", fail_stage_cleanup)
        caplog.set_level("DEBUG")

        with pytest.raises(InvalidOutputArtifact) as caught:
            apply_music_file(video, music, volume=0.5, encoding_plan=_h264_output_plan())

        assert caught.value is validation_error
        assert caught.value.__cause__ is validation_cause
        assert optional_music_warning(caught.value, config) == (
            "Optional music failed: invalid mix from ***"
        )
        assert video.read_bytes() == b"validated-base"
        assert "Music stage cleanup failed; preserving the primary phase outcome" in caplog.text
        assert "cleanup-secret-917" not in caplog.text


def test_music_failure_keeps_valid_base_and_returns_sanitized_warning(tmp_path: Path) -> None:
    from immich_memories import generate_music
    from immich_memories.generate_settings import _run_music_phase

    base_video = tmp_path / "memory.mp4"
    base_video.write_bytes(b"validated-base")
    music_file = tmp_path / "music.wav"
    music_file.write_bytes(b"music")
    params = GenerationParams(
        clips=[],
        output_path=base_video,
        config=Config(),
        music_path=music_file,
    )
    tracker = MagicMock()

    with patch(
        "immich_memories.generate_music.apply_music_file",
        side_effect=RuntimeError("music backend unavailable"),
    ):
        result = _run_music_phase(
            params,
            [],
            base_video,
            tmp_path,
            tracker,
            encoding_plan=_h264_output_plan(),
        )

    warning = "Optional music failed: music backend unavailable"
    assert type(result) is generate_music.MusicPhaseResult
    assert result == generate_music.MusicPhaseResult(applied=False, warning=warning)
    assert base_video.read_bytes() == b"validated-base"
    tracker.complete_phase.assert_called_once_with(
        items_processed=0,
        errors=[{"error": warning}],
    )


def test_music_resolution_failure_is_optional_and_sanitized(tmp_path: Path) -> None:
    from immich_memories import generate_music
    from immich_memories.generate_settings import _run_music_phase

    base_video = tmp_path / "memory.mp4"
    base_video.write_bytes(b"validated-base")
    config = Config()
    config.musicgen.enabled = True
    config.musicgen.api_key = "top-secret"
    params = GenerationParams(clips=[], output_path=base_video, config=config)
    tracker = MagicMock()

    with patch(
        "immich_memories.generate_music.resolve_music_file",
        side_effect=RuntimeError("api_key=top-secret backend unavailable"),
    ):
        result = _run_music_phase(
            params,
            [],
            base_video,
            tmp_path,
            tracker,
            encoding_plan=_h264_output_plan(),
        )

    warning = "Optional music failed: api_key=*** backend unavailable"
    assert type(result) is generate_music.MusicPhaseResult
    assert result == generate_music.MusicPhaseResult(applied=False, warning=warning)
    assert base_video.read_bytes() == b"validated-base"
    tracker.start_phase.assert_called_once_with("music", 1)
    tracker.complete_phase.assert_called_once_with(
        items_processed=0,
        errors=[{"error": warning}],
    )


def test_optional_music_logs_never_include_raw_backend_secret(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Raw backend tracebacks must not bypass the optional boundary sanitizer."""
    from immich_memories.generate_settings import _run_music_phase

    base_video = tmp_path / "memory.mp4"
    base_video.write_bytes(b"validated-base")
    config = Config()
    config.musicgen.enabled = True
    config.musicgen.api_key = "top-secret"
    params = GenerationParams(clips=[], output_path=base_video, config=config)
    tracker = MagicMock()
    caplog.set_level("DEBUG")

    with patch(
        "immich_memories.audio.music_generator.generate_music_for_video",
        new_callable=AsyncMock,
        side_effect=RuntimeError("backend rejected top-secret"),
    ):
        result = _run_music_phase(
            params,
            [],
            base_video,
            tmp_path,
            tracker,
            encoding_plan=_h264_output_plan(),
        )

    # A generator failure no longer ends the phase — it falls through to a
    # bundled track (#422) — so the sanitized backend warning lands in the log
    # rather than in the phase result. The property under test is that the raw
    # secret reaches neither, whatever the phase ends on.
    assert "Optional music failed: backend rejected ***" in caplog.text
    assert "top-secret" not in caplog.text
    assert result.warning is None or "top-secret" not in result.warning


def test_music_phase_passes_exact_encoding_plan_to_publication(tmp_path: Path) -> None:
    from immich_memories import generate_music
    from immich_memories.generate_settings import _run_music_phase

    base_video = tmp_path / "memory.mp4"
    base_video.write_bytes(b"validated-base")
    music_file = tmp_path / "music.wav"
    music_file.write_bytes(b"music")
    plan = _h264_output_plan()
    params = GenerationParams(
        clips=[],
        output_path=base_video,
        config=Config(),
        music_path=music_file,
    )
    tracker = MagicMock()

    with patch("immich_memories.generate_music.apply_music_file") as apply_music:
        result = _run_music_phase(
            params,
            [],
            base_video,
            tmp_path,
            tracker,
            encoding_plan=plan,
        )

    assert result == generate_music.MusicPhaseResult(applied=True)
    apply_music.assert_called_once_with(base_video, music_file, params.music_volume, plan)
    tracker.complete_phase.assert_called_once_with(items_processed=1)


def test_optional_music_failure_preserves_base_and_uploads_valid_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from immich_memories import generate as generate_module
    from immich_memories.generate import generate_memory
    from immich_memories.processing import output_contract
    from immich_memories.processing.assembly_config import AssemblyClip, AssemblySettings

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-video")
    assembly_clip = AssemblyClip(path=source, duration=5.0, asset_id="clip-1")
    music_file = tmp_path / "music.wav"
    music_file.write_bytes(b"music")
    config = Config(
        cache={"directory": str(tmp_path / "cache"), "database": str(tmp_path / "runs.db")}
    )
    progress: list[tuple[str, str]] = []
    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=tmp_path / "memory.mp4",
        config=config,
        client=MagicMock(),
        music_path=music_file,
        upload_enabled=True,
        progress_callback=lambda phase, _pct, message: progress.append((phase, message)),
    )
    plan = _h264_output_plan()

    class Assembler:
        def assemble_with_titles(
            self,
            _clips: object,
            output_path: Path,
            _progress_callback: object,
            **_kwargs: object,
        ) -> Path:
            output_path.write_bytes(b"validated-base")
            return output_path

    monkeypatch.setattr(
        output_contract.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, json.dumps(_final_probe_payload()), ""
        ),
    )
    tracker = MagicMock()
    uploaded: list[bytes] = []

    def upload(_client: object, video_path: Path, _album: object) -> dict[str, str]:
        uploaded.append(video_path.read_bytes())
        return {"asset_id": "asset-1"}

    with (
        patch("immich_memories.tracking.RunTracker", return_value=tracker),
        patch("immich_memories.cache.video_cache.VideoDownloadCache", return_value=MagicMock()),
        patch.object(generate_module, "_extract_clips", return_value=[assembly_clip]),
        patch.object(
            generate_module,
            "_build_assembly_settings",
            return_value=AssemblySettings(encoding_plan=plan),
        ),
        patch.object(generate_module, "_create_assembler", return_value=Assembler()),
        patch(
            "immich_memories.generate_music.apply_music_file",
            side_effect=RuntimeError("music backend unavailable"),
        ),
        patch.object(generate_module, "_upload_to_immich", side_effect=upload),
        patch.object(generate_module, "_cleanup_temp_clips"),
    ):
        result = generate_memory(params)

    warning = "Optional music failed: music backend unavailable"
    assert result.read_bytes() == b"validated-base"
    assert uploaded == [b"validated-base"]
    assert ("music", warning) in progress
    tracker.complete_artifact.assert_called_once()
    assert tracker.complete_artifact.call_args.args[2] == [warning]
    tracker.mark_delivered.assert_called_once_with("asset-1")
    tracker.fail_run.assert_not_called()


def test_no_music_skips_core_music_phase_entirely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UI-owned None music must not even enter core optional-music resolution."""
    from immich_memories import generate as generate_module
    from immich_memories.generate import generate_memory
    from immich_memories.processing import output_contract
    from immich_memories.processing.assembly_config import AssemblyClip, AssemblySettings

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-video")
    assembly_clip = AssemblyClip(path=source, duration=5.0, asset_id="clip-1")
    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=tmp_path / "memory.mp4",
        config=Config(
            cache={"directory": str(tmp_path / "cache"), "database": str(tmp_path / "runs.db")}
        ),
        no_music=True,
    )
    plan = _h264_output_plan()

    class Assembler:
        def assemble_with_titles(
            self,
            _clips: object,
            output_path: Path,
            _progress_callback: object,
            **_kwargs: object,
        ) -> Path:
            output_path.write_bytes(b"validated-base")
            return output_path

    monkeypatch.setattr(
        output_contract.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, json.dumps(_final_probe_payload()), ""
        ),
    )
    with (
        patch("immich_memories.tracking.RunTracker", return_value=MagicMock()),
        patch("immich_memories.cache.video_cache.VideoDownloadCache", return_value=MagicMock()),
        patch.object(generate_module, "_extract_clips", return_value=[assembly_clip]),
        patch.object(
            generate_module,
            "_build_assembly_settings",
            return_value=AssemblySettings(encoding_plan=plan),
        ),
        patch.object(generate_module, "_create_assembler", return_value=Assembler()),
        patch.object(generate_module, "_run_music_phase") as music_phase,
        patch.object(generate_module, "_cleanup_temp_clips"),
    ):
        generate_memory(params)

    music_phase.assert_not_called()
