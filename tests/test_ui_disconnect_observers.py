"""NiceGUI disconnect contracts for Step 4 generation observers."""

from __future__ import annotations

from pathlib import Path

import pytest

from immich_memories.config_loader import Config
from immich_memories.generate import GenerationParams, PreparedGeneration
from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec
from immich_memories.processing.output_contract import OutputProbe
from immich_memories.tracking import DeliveryStatus, RunDatabase
from immich_memories.ui.state import AppState
from tests.conftest import make_clip

_CLIENT_DELETED = "The client this element belongs to has been deleted."


class _Element:
    def __init__(self, value=None) -> None:
        self.value = value
        self.source = None

    def classes(self, *_args, **_kwargs):
        return self

    def style(self, *_args, **_kwargs):
        return self

    def set_text(self, value: str) -> None:
        self.value = value

    def disable(self) -> None:
        return None

    def set_visibility(self, _visible: bool) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


class _Container:
    def clear(self) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


class _FailingValue:
    def __init__(self, error: RuntimeError) -> None:
        self.error = error

    @property
    def value(self) -> float:
        return 0.0

    @value.setter
    def value(self, _value: float) -> None:
        raise self.error


class _FailingSource:
    def __init__(self, error: RuntimeError) -> None:
        self.error = error

    @property
    def source(self) -> None:
        return None

    @source.setter
    def source(self, _value: str) -> None:
        raise self.error


class _FailingText:
    def __init__(self, error: RuntimeError) -> None:
        self.error = error

    def set_text(self, _value: str) -> None:
        raise self.error


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


def _probe() -> OutputProbe:
    return OutputProbe(
        codec="h264",
        container="mp4",
        duration_seconds=5.0,
        size_bytes=4096,
        pixel_format="yuv420p",
        color_transfer="bt709",
        color_primaries="bt709",
        width=1920,
        height=1080,
        decoded_frames=120,
    )


def _call_observer_boundary(
    boundary: str,
    failure: RuntimeError,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> bool:
    from immich_memories.ui.pages import _step4_generate as step4_generate

    if boundary == "progress":
        return step4_generate._write_progress_ui(
            _FailingValue(failure), _Element(), 0.5, "Encoding"
        )
    if boundary == "phase":
        return step4_generate._write_phase_ui(_FailingText(failure), "Encoding")
    if boundary == "preview":
        return step4_generate._write_preview_ui(_FailingSource(failure), b"jpeg")
    if boundary == "completion":
        state = AppState(delivery_status=DeliveryStatus.PENDING)
        return step4_generate._write_completion_ui(
            _FailingText(failure),
            _Element(),
            _Element(),
            _Element(),
            _Container(),
            tmp_path / "memory.mp4",
            state,
        )
    if boundary == "failure":
        monkeypatch.setattr(
            step4_generate.ui,
            "notify",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
        )
        return step4_generate._write_failure_ui(
            _Element(),
            _Container(),
            "Generation failed",
            artifact_completed=False,
        )
    raise AssertionError(f"unknown boundary: {boundary}")


@pytest.mark.parametrize("boundary", ["progress", "phase", "preview", "completion", "failure"])
def test_observer_boundary_suppresses_client_disconnect(
    boundary: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    completed = _call_observer_boundary(
        boundary,
        RuntimeError(_CLIENT_DELETED),
        monkeypatch,
        tmp_path,
    )

    assert completed is False


@pytest.mark.parametrize("boundary", ["progress", "phase", "preview", "completion", "failure"])
def test_observer_boundary_preserves_unrelated_runtime_error(
    boundary: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    failure = RuntimeError(f"{boundary} observer bug")

    with pytest.raises(RuntimeError) as raised:
        _call_observer_boundary(boundary, failure, monkeypatch, tmp_path)

    assert raised.value is failure


def _patch_generation_shell(
    monkeypatch: pytest.MonkeyPatch,
    step4_generate,
    *,
    params: GenerationParams,
    output_path: Path,
    execute,
    run_id: str,
) -> None:
    monkeypatch.setattr(step4_generate.ui, "linear_progress", lambda **_kwargs: _Element())
    monkeypatch.setattr(step4_generate.ui, "label", lambda *_args, **_kwargs: _Element())
    monkeypatch.setattr(step4_generate.ui, "image", lambda **_kwargs: _Element())
    monkeypatch.setattr(
        "immich_memories.ui.components.im_button",
        lambda *_args, **_kwargs: _Element(),
    )
    monkeypatch.setattr(step4_generate, "normalize_ui_output_path", lambda *_args: output_path)
    monkeypatch.setattr(step4_generate, "_build_generation_params", lambda *_args: params)
    monkeypatch.setattr(step4_generate, "execute_ui_generation", execute)
    monkeypatch.setattr("immich_memories.tracking.generate_run_id", lambda: run_id)


@pytest.mark.asyncio
async def test_completion_disconnect_preserves_pending_delivery_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from immich_memories.ui.pages import _step4_generate as step4_generate

    db_path = tmp_path / "runs.db"
    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"validated-video")
    config = Config(cache={"database": str(db_path)})
    state = AppState(config=config, generation_options={"music_source": "None"})
    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=output_path,
        config=config,
        upload_enabled=True,
        upload_album="Memories",
    )
    prepared = PreparedGeneration(output_path, _h264_plan(), (), 1, 1)
    notifications: list[str] = []

    async def execute(_state, _params, tracker, _progress, _status):
        tracker.start_run(source="manual")
        tracker.complete_artifact(
            output_path,
            _probe(),
            warnings=[],
            delivery_requested=True,
            delivery_album="Memories",
            clips_analyzed=1,
            clips_selected=1,
        )
        return prepared

    _patch_generation_shell(
        monkeypatch,
        step4_generate,
        params=params,
        output_path=output_path,
        execute=execute,
        run_id="ui-disconnect-completed",
    )
    monkeypatch.setattr(
        step4_generate.ui,
        "notify",
        lambda message, **_kwargs: notifications.append(message),
    )
    monkeypatch.setattr(
        step4_generate,
        "_show_output",
        lambda *_args: (_ for _ in ()).throw(RuntimeError(_CLIENT_DELETED)),
    )

    await step4_generate.run_generation(
        state,
        [make_clip("clip-1")],
        total_duration=5.0,
        output_dir=tmp_path,
        output_path=output_path,
        filename_input=_Element("memory.mp4"),
        progress_container=_Container(),
        output_container=_Container(),
    )

    saved = RunDatabase(db_path).get_run("ui-disconnect-completed")
    assert saved is not None
    assert saved.status == "completed"
    assert saved.delivery_status is DeliveryStatus.PENDING
    assert state.output_path == output_path
    assert state.delivery_status is DeliveryStatus.PENDING
    assert notifications == []


@pytest.mark.asyncio
async def test_failure_notification_disconnect_preserves_failed_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from immich_memories.ui.pages import _step4_generate as step4_generate

    db_path = tmp_path / "runs.db"
    output_path = tmp_path / "memory.mp4"
    config = Config(cache={"database": str(db_path)})
    state = AppState(config=config, generation_options={"music_source": "None"})
    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=output_path,
        config=config,
    )

    async def execute(_state, _params, tracker, _progress, _status):
        tracker.start_run(source="manual")
        raise ValueError("generation exploded")

    _patch_generation_shell(
        monkeypatch,
        step4_generate,
        params=params,
        output_path=output_path,
        execute=execute,
        run_id="ui-disconnect-failed",
    )
    monkeypatch.setattr(
        step4_generate.ui,
        "notify",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(_CLIENT_DELETED)),
    )

    await step4_generate.run_generation(
        state,
        [make_clip("clip-1")],
        total_duration=5.0,
        output_dir=tmp_path,
        output_path=output_path,
        filename_input=_Element("memory.mp4"),
        progress_container=_Container(),
        output_container=_Container(),
    )

    saved = RunDatabase(db_path).get_run("ui-disconnect-failed")
    assert saved is not None
    assert saved.status == "failed"
    assert saved.errors_count == 1
    assert state.output_path is None
