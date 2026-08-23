"""Single-owner artifact and delivery lifecycle for NiceGUI generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from immich_memories.config_loader import Config
from immich_memories.generate import GenerationParams, PreparedGeneration
from immich_memories.processing.encoding_plan import EncodingPlan, HdrTransfer, OutputCodec
from immich_memories.processing.output_contract import OutputProbe
from immich_memories.tracking import DeliveryStatus, RunDatabase, RunTracker
from immich_memories.ui.state import AppState
from tests.conftest import make_clip


class _Progress:
    value = 0.0


class _Status:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def set_text(self, message: str) -> None:
        self.messages.append(message)


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
        duration_seconds=42.5,
        size_bytes=4096,
        pixel_format="yuv420p",
        color_transfer="bt709",
        color_primaries="bt709",
        width=1920,
        height=1080,
        decoded_frames=1020,
    )


@pytest.mark.asyncio
async def test_ui_finalizer_validates_exact_plan_and_completes_no_upload_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A prepared UI artifact becomes one completed, not-requested run."""
    from immich_memories.ui.pages import _step4_generate as step4_generate

    db_path = tmp_path / "runs.db"
    config = Config(cache={"database": str(db_path)})
    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"validated-base")
    plan = _h264_plan()
    prepared = PreparedGeneration(
        path=output_path,
        encoding_plan=plan,
        assembly_clips=(),
        clips_analyzed=3,
        clips_selected=2,
    )
    phase_events = []
    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=output_path,
        config=config,
        upload_enabled=False,
        upload_album="UI Album",
        phase_callback=phase_events.append,
    )
    tracker = RunTracker("ui-finalize", db_path=db_path, capture_system=False)
    tracker.start_run(source="manual")
    state = AppState(config=config, generation_options={"music_source": "None"})
    seen_plans: list[EncodingPlan] = []
    complete_calls = 0
    complete_artifact = tracker.complete_artifact

    def count_completion(*args, **kwargs):
        nonlocal complete_calls
        complete_calls += 1
        return complete_artifact(*args, **kwargs)

    def validate(path: Path, encoding_plan: EncodingPlan) -> OutputProbe:
        assert path == output_path
        seen_plans.append(encoding_plan)
        return _probe()

    async def io_bound(callback, *args, **kwargs):
        return callback(*args, **kwargs)

    monkeypatch.setattr(step4_generate, "validate_output", validate, raising=False)
    monkeypatch.setattr(step4_generate.run, "io_bound", io_bound)
    monkeypatch.setattr(tracker, "complete_artifact", count_completion)

    completed = await step4_generate.finalize_ui_generation(
        state,
        params,
        prepared,
        tracker,
        progress_bar=object(),
        status_label=object(),
    )
    saved = RunDatabase(db_path).get_run("ui-finalize")

    assert seen_plans == [plan]
    assert completed.status == "completed"
    assert completed.delivery_status is DeliveryStatus.NOT_REQUESTED
    assert completed.delivery_attempts == 0
    assert completed.clips_analyzed == 3
    assert completed.clips_selected == 2
    assert saved is not None
    assert saved.to_dict() == completed.to_dict()
    assert state.generation_warning is None
    assert state.delivery_status is DeliveryStatus.NOT_REQUESTED
    assert [event.phase.value for event in phase_events] == ["music", "delivery", "complete"]
    assert saved.last_phase.value == "complete"
    assert complete_calls == 1


@pytest.mark.asyncio
async def test_ui_music_failure_retains_music_as_last_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from immich_memories.ui.pages import _step4_generate as step4_generate

    db_path = tmp_path / "runs.db"
    config = Config(cache={"database": str(db_path)})
    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"validated-base")
    prepared = PreparedGeneration(output_path, _h264_plan(), (), 1, 1)
    params = GenerationParams(clips=[make_clip("clip-1")], output_path=output_path, config=config)
    tracker = RunTracker("ui-music-failure", db_path=db_path, capture_system=False)
    tracker.start_run(source="manual")
    state = AppState(config=config, generation_options={"music_source": "AI Generated"})

    def fail_music(*_args, **_kwargs):
        raise RuntimeError("music backend failed")

    monkeypatch.setattr("immich_memories.generate_settings._run_music_phase", fail_music)
    with pytest.raises(RuntimeError, match="music backend failed"):
        await step4_generate.finalize_ui_generation(
            state,
            params,
            prepared,
            tracker,
            progress_bar=object(),
            status_label=object(),
        )

    saved = RunDatabase(db_path).get_run(tracker.run_id)
    assert saved is not None
    assert saved.status == "running"
    assert saved.last_phase.value == "music"


@pytest.mark.asyncio
async def test_ui_artifact_completion_survives_sidecar_mirror_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The database lifecycle stays authoritative when its JSON mirror cannot write."""
    from immich_memories.ui.pages import _step4_generate as step4_generate

    configured_literal = "sidecar-secondary-secret-611"
    db_path = tmp_path / "runs.db"
    config = Config(cache={"database": str(db_path)})
    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"validated-video")
    tracker = RunTracker("ui-sidecar", db_path=db_path, capture_system=False)
    tracker.start_run(source="manual")
    state = AppState(config=config, generation_options={"music_source": "None"})

    def fail_sidecar(*_args) -> None:
        raise OSError(configured_literal)

    async def io_bound(callback, *args, **kwargs):
        return callback(*args, **kwargs)

    monkeypatch.setattr(tracker, "_save_metadata_json", fail_sidecar)
    monkeypatch.setattr(step4_generate, "validate_output", lambda *_args: _probe())
    monkeypatch.setattr(step4_generate.run, "io_bound", io_bound)
    caplog.set_level("WARNING", logger="immich_memories.tracking.run_tracker")

    completed = await step4_generate.finalize_ui_generation(
        state,
        GenerationParams(
            clips=[make_clip("clip-1")],
            output_path=output_path,
            config=config,
        ),
        PreparedGeneration(output_path, _h264_plan(), (), 1, 1),
        tracker,
        progress_bar=_Progress(),
        status_label=_Status(),
    )

    saved = RunDatabase(db_path).get_run("ui-sidecar")
    assert saved is not None
    assert completed.to_dict() == saved.to_dict()
    assert completed.status == "completed"
    assert completed.output_path == str(output_path)
    assert configured_literal not in caplog.text


@pytest.mark.asyncio
async def test_ui_music_warning_is_durable_and_final_validation_runs_after_music(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Optional music fallback stays visible and is committed with the artifact."""
    from immich_memories.generate_music import MusicPhaseResult
    from immich_memories.ui.pages import _step4_generate as step4_generate

    db_path = tmp_path / "runs.db"
    config = Config(cache={"database": str(db_path)})
    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"validated-base")
    plan = _h264_plan()
    prepared = PreparedGeneration(
        path=output_path,
        encoding_plan=plan,
        assembly_clips=(),
        clips_analyzed=4,
        clips_selected=3,
    )
    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=output_path,
        config=config,
        upload_enabled=False,
    )
    tracker = RunTracker("ui-music-warning", db_path=db_path, capture_system=False)
    tracker.start_run(source="manual")
    state = AppState(
        config=config,
        generation_options={"music_source": "Upload file", "music_file": b"music"},
    )
    warning = "Optional music failed: backend unavailable"
    events: list[str] = []

    def apply_music(
        _params,
        _assembly_clips,
        result_path,
        _run_output_dir,
        run_tracker,
        *,
        encoding_plan,
        mute_windows=None,
    ) -> MusicPhaseResult:
        assert run_tracker is tracker
        assert encoding_plan is plan
        assert result_path.read_bytes() == b"validated-base"
        events.append("music")
        run_tracker.start_phase("music", 1)
        run_tracker.complete_phase(items_processed=0, errors=[{"error": warning}])
        return MusicPhaseResult(applied=False, warning=warning)

    def validate(path: Path, encoding_plan: EncodingPlan) -> OutputProbe:
        assert encoding_plan is plan
        assert path.read_bytes() == b"validated-base"
        events.append("final-validation")
        return _probe()

    async def io_bound(callback, *args, **kwargs):
        return callback(*args, **kwargs)

    monkeypatch.setattr("immich_memories.generate_settings._run_music_phase", apply_music)
    monkeypatch.setattr(step4_generate, "validate_output", validate)
    monkeypatch.setattr(step4_generate.run, "io_bound", io_bound)
    caplog.set_level("WARNING")

    completed = await step4_generate.finalize_ui_generation(
        state,
        params,
        prepared,
        tracker,
        progress_bar=object(),
        status_label=object(),
    )

    assert events == ["music", "final-validation"]
    assert completed.warnings == [warning]
    assert [phase.phase_name for phase in completed.phases] == ["music"]
    assert state.generation_warning == warning
    assert state.delivery_status is DeliveryStatus.NOT_REQUESTED
    assert "foreign key" not in caplog.text.lower()


@pytest.mark.asyncio
async def test_ui_successful_upload_uses_completed_pending_row_and_same_tracker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one UI API call starts from pending zero and records one delivered attempt."""
    from immich_memories.ui.pages import _step4_generate as step4_generate

    db_path = tmp_path / "runs.db"
    config = Config(cache={"database": str(db_path)})
    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"validated-base")
    plan = _h264_plan()
    prepared = PreparedGeneration(output_path, plan, (), 5, 4)
    tracker = RunTracker("ui-upload-success", db_path=db_path, capture_system=False)
    tracker.start_run(source="manual")
    calls: list[str] = []

    class Client:
        def upload_memory(self, *, video_path: Path, album_name: str | None):
            before_call = RunDatabase(db_path).get_run("ui-upload-success")
            assert before_call is not None
            assert before_call.status == "completed"
            assert before_call.delivery_status is DeliveryStatus.PENDING
            assert before_call.delivery_attempts == 0
            assert before_call.delivery_album == "Original UI Album"
            assert video_path == output_path
            assert album_name == "Original UI Album"
            assert tracker.current_run == before_call
            calls.append("upload")
            return {"asset_id": "ui-asset", "album_id": "ui-album"}

    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=output_path,
        config=config,
        client=Client(),  # type: ignore[arg-type]
        upload_enabled=True,
        upload_album="Original UI Album",
    )
    state = AppState(
        config=config,
        generation_options={"music_source": "None"},
        upload_enabled=True,
        upload_album_name="Original UI Album",
    )

    async def io_bound(callback, *args, **kwargs):
        return callback(*args, **kwargs)

    monkeypatch.setattr(step4_generate, "validate_output", lambda *_args: _probe())
    monkeypatch.setattr(step4_generate.run, "io_bound", io_bound)
    monkeypatch.setattr(step4_generate.ui, "notify", lambda *_args, **_kwargs: None)

    completed = await step4_generate.finalize_ui_generation(
        state,
        params,
        prepared,
        tracker,
        progress_bar=_Progress(),
        status_label=_Status(),
    )

    assert calls == ["upload"]
    assert completed.delivery_status is DeliveryStatus.DELIVERED
    assert completed.delivery_attempts == 1
    assert completed.immich_asset_id == "ui-asset"
    assert completed.delivery_album == "Original UI Album"
    assert state.delivery_status is DeliveryStatus.DELIVERED
    assert state.upload_result == {"asset_id": "ui-asset", "album_id": "ui-album"}


@pytest.mark.asyncio
async def test_ui_success_toast_failure_preserves_delivered_state_and_upload_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A delivery-success observer is outside the durable delivery transition."""
    from immich_memories.ui.pages import _step4_generate as step4_generate

    db_path = tmp_path / "runs.db"
    config = Config(cache={"database": str(db_path)})
    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"validated-base")
    tracker = RunTracker("ui-success-toast", db_path=db_path, capture_system=False)
    tracker.start_run(source="manual")
    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=output_path,
        config=config,
        client=object(),  # type: ignore[arg-type]
        upload_enabled=True,
        upload_album="Delivered Album",
    )
    state = AppState(
        config=config, generation_options={"music_source": "None"}, upload_enabled=True
    )
    upload_result = {"asset_id": "toast-asset", "album_id": "toast-album"}
    notifications: list[str] = []

    async def io_bound(callback, *args, **kwargs):
        return callback(*args, **kwargs)

    def fail_success_toast(message: str, **_kwargs) -> None:
        notifications.append(message)
        raise RuntimeError("success toast observer failed")

    monkeypatch.setattr(step4_generate, "validate_output", lambda *_args: _probe())
    monkeypatch.setattr(step4_generate.run, "io_bound", io_bound)
    monkeypatch.setattr("immich_memories.generate._upload_to_immich", lambda *_args: upload_result)
    monkeypatch.setattr("immich_memories.ui.pages._step4_upload.ui.notify", fail_success_toast)
    caplog.set_level("WARNING", logger="immich_memories.ui.pages._step4_upload")

    completed = await step4_generate.finalize_ui_generation(
        state,
        params,
        PreparedGeneration(output_path, _h264_plan(), (), 1, 1),
        tracker,
        progress_bar=_Progress(),
        status_label=_Status(),
    )

    saved = RunDatabase(db_path).get_run("ui-success-toast")
    assert saved is not None
    assert completed.delivery_status is DeliveryStatus.DELIVERED
    assert saved.delivery_status is DeliveryStatus.DELIVERED
    assert state.delivery_status is DeliveryStatus.DELIVERED
    assert state.upload_result == upload_result
    assert notifications == ["Uploaded to Immich! Album: Delivered Album"]
    assert "remains pending" not in caplog.text


@pytest.mark.asyncio
async def test_ui_reloads_delivered_truth_when_mark_delivered_commits_then_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An ambiguous delivery write is resolved from the authoritative delivered row."""
    from immich_memories.ui.pages import _step4_generate as step4_generate

    db_path = tmp_path / "runs.db"
    config = Config(cache={"database": str(db_path)})
    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"validated-base")
    tracker = RunTracker("ui-committed-delivery", db_path=db_path, capture_system=False)
    tracker.start_run(source="manual")
    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=output_path,
        config=config,
        client=object(),  # type: ignore[arg-type]
        upload_enabled=True,
        upload_album="Committed Album",
    )
    state = AppState(
        config=config, generation_options={"music_source": "None"}, upload_enabled=True
    )
    upload_result = {"asset_id": "committed-asset"}
    original_mark_delivered = tracker.db.mark_delivered
    notifications: list[str] = []
    writes = 0

    def commit_then_raise(run_id: str, asset_id: str):
        nonlocal writes
        writes += 1
        original_mark_delivered(run_id, asset_id)
        raise OSError("connection closed after durable delivery write")

    async def io_bound(callback, *args, **kwargs):
        return callback(*args, **kwargs)

    monkeypatch.setattr(tracker.db, "mark_delivered", commit_then_raise)
    monkeypatch.setattr(step4_generate, "validate_output", lambda *_args: _probe())
    monkeypatch.setattr(step4_generate.run, "io_bound", io_bound)
    monkeypatch.setattr("immich_memories.generate._upload_to_immich", lambda *_args: upload_result)
    monkeypatch.setattr(
        "immich_memories.ui.pages._step4_upload.ui.notify",
        lambda message, **_kwargs: notifications.append(message),
    )
    caplog.set_level("WARNING", logger="immich_memories.ui.pages._step4_upload")

    completed = await step4_generate.finalize_ui_generation(
        state,
        params,
        PreparedGeneration(output_path, _h264_plan(), (), 1, 1),
        tracker,
        progress_bar=_Progress(),
        status_label=_Status(),
    )

    saved = RunDatabase(db_path).get_run("ui-committed-delivery")
    assert saved is not None
    assert completed.delivery_status is DeliveryStatus.DELIVERED
    assert saved.delivery_status is DeliveryStatus.DELIVERED
    assert saved.immich_asset_id == "committed-asset"
    assert tracker.current_run is not None
    assert tracker.current_run.delivery_status is DeliveryStatus.PENDING
    assert state.delivery_status is DeliveryStatus.DELIVERED
    assert state.upload_result == upload_result
    assert notifications == ["Uploaded to Immich! Album: Committed Album"]
    assert "remains pending" not in caplog.text
    assert writes == 1


@pytest.mark.asyncio
async def test_run_generation_does_not_restore_stale_pending_delivery_after_ambiguous_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The outer UI flow must not overwrite delivered DB truth with stale tracker metadata."""
    from immich_memories.ui.pages import _step4_generate as step4_generate
    from immich_memories.ui.pages._step4_upload import upload_to_immich

    db_path = tmp_path / "runs.db"
    config = Config(cache={"database": str(db_path)})
    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"validated-base")
    state = AppState(
        config=config, generation_options={"music_source": "None"}, upload_enabled=True
    )
    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=output_path,
        config=config,
        client=object(),  # type: ignore[arg-type]
        upload_enabled=True,
        upload_album="Stale Tracker Album",
    )
    prepared = PreparedGeneration(output_path, _h264_plan(), (), 1, 1)
    tracker_ref: list[RunTracker] = []
    upload_result = {"asset_id": "stale-tracker-asset"}
    writes = 0
    shown_statuses: list[DeliveryStatus] = []

    async def io_bound(callback, *args, **kwargs):
        return callback(*args, **kwargs)

    async def execute(_state, _params, tracker, progress, status):
        nonlocal writes
        tracker_ref.append(tracker)
        tracker.start_run(source="manual")
        tracker.complete_artifact(
            output_path,
            _probe(),
            warnings=[],
            delivery_requested=True,
            delivery_album="Stale Tracker Album",
            clips_analyzed=1,
            clips_selected=1,
        )
        original_mark_delivered = tracker.db.mark_delivered

        def commit_then_raise(run_id: str, asset_id: str):
            nonlocal writes
            writes += 1
            original_mark_delivered(run_id, asset_id)
            raise OSError("connection closed after durable delivery write")

        monkeypatch.setattr(tracker.db, "mark_delivered", commit_then_raise)
        await upload_to_immich(output_path, _state, _params, tracker, progress, status)
        assert tracker.current_run is not None
        assert tracker.current_run.delivery_status is DeliveryStatus.PENDING
        return prepared

    monkeypatch.setattr(step4_generate.ui, "linear_progress", lambda **_kwargs: _Element())
    monkeypatch.setattr(step4_generate.ui, "label", lambda *_args, **_kwargs: _Element())
    monkeypatch.setattr(step4_generate.ui, "image", lambda **_kwargs: _Element())
    monkeypatch.setattr(
        "immich_memories.ui.components.im_button", lambda *_args, **_kwargs: _Element()
    )
    monkeypatch.setattr(step4_generate, "normalize_ui_output_path", lambda *_args: output_path)
    monkeypatch.setattr(step4_generate, "_build_generation_params", lambda *_args: params)
    monkeypatch.setattr(step4_generate, "execute_ui_generation", execute)
    monkeypatch.setattr(
        step4_generate,
        "_show_output",
        lambda _container, _path, rendered_state: shown_statuses.append(
            rendered_state.delivery_status
        ),
    )
    monkeypatch.setattr(step4_generate.run, "io_bound", io_bound)
    monkeypatch.setattr("immich_memories.generate._upload_to_immich", lambda *_args: upload_result)
    monkeypatch.setattr(
        "immich_memories.ui.pages._step4_upload.ui.notify", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr("immich_memories.tracking.generate_run_id", lambda: "ui-stale-tracker")

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

    saved = RunDatabase(db_path).get_run("ui-stale-tracker")
    assert saved is not None
    assert saved.delivery_status is DeliveryStatus.DELIVERED
    assert state.delivery_status is DeliveryStatus.DELIVERED
    assert state.upload_result == upload_result
    assert shown_statuses == [DeliveryStatus.DELIVERED]
    assert writes == 1


@pytest.mark.asyncio
async def test_ui_unexpected_delivery_error_keeps_pending_video_and_redacts_config_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Even a secondary delivery-boundary bug cannot leak secrets or hide the local video."""
    from immich_memories import generate as generate_module
    from immich_memories.ui.pages import _step4_generate as step4_generate

    configured_literal = "ui-config-secret-842"
    db_path = tmp_path / "runs.db"
    config = Config(
        cache={"database": str(db_path)},
        immich={"api_key": configured_literal},
    )
    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"validated-base")
    prepared = PreparedGeneration(output_path, _h264_plan(), (), 1, 1)
    tracker = RunTracker("ui-upload-redaction", db_path=db_path, capture_system=False)
    tracker.start_run(source="manual")
    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=output_path,
        config=config,
        client=object(),  # type: ignore[arg-type]
        upload_enabled=True,
        upload_album="Private Album",
    )
    state = AppState(
        config=config,
        generation_options={"music_source": "None"},
        upload_enabled=True,
    )
    notifications: list[str] = []

    async def io_bound(callback, *args, **kwargs):
        return callback(*args, **kwargs)

    def unexpected_boundary_failure(*_args, **_kwargs):
        raise RuntimeError(f"secondary failure echoed {configured_literal}")

    monkeypatch.setattr(step4_generate, "validate_output", lambda *_args: _probe())
    monkeypatch.setattr(step4_generate.run, "io_bound", io_bound)
    monkeypatch.setattr(
        generate_module,
        "deliver_completed_artifact",
        unexpected_boundary_failure,
    )
    monkeypatch.setattr(
        "immich_memories.ui.pages._step4_upload.ui.notify",
        lambda message, **_kwargs: notifications.append(message),
    )
    caplog.set_level("WARNING")

    completed = await step4_generate.finalize_ui_generation(
        state,
        params,
        prepared,
        tracker,
        progress_bar=_Progress(),
        status_label=_Status(),
    )

    assert output_path.read_bytes() == b"validated-base"
    assert completed.status == "completed"
    assert completed.delivery_status is DeliveryStatus.PENDING
    assert completed.delivery_attempts == 0
    assert state.delivery_status is DeliveryStatus.PENDING
    assert configured_literal not in " ".join(notifications)
    assert configured_literal not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client_kind", "expected_attempts"),
    [("api_failure", 1), ("missing", 0)],
)
async def test_ui_delivery_failure_preserves_retryable_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    client_kind: str,
    expected_attempts: int,
) -> None:
    """API failures and missing configuration stay completed, pending, and visible."""
    from immich_memories.ui.pages import _step4_generate as step4_generate

    configured_literal = "ui-delivery-secret-194"
    db_path = tmp_path / f"{client_kind}.db"
    config = Config(
        cache={"database": str(db_path)},
        immich={"api_key": configured_literal},
    )
    output_path = tmp_path / f"{client_kind}.mp4"
    output_path.write_bytes(b"validated-video")
    tracker = RunTracker(f"ui-{client_kind}", db_path=db_path, capture_system=False)
    tracker.start_run(source="manual")
    calls = 0

    class FailingClient:
        def upload_memory(self, **_kwargs):
            nonlocal calls
            calls += 1
            raise RuntimeError(f"server rejected {configured_literal}")

    client = FailingClient() if client_kind == "api_failure" else None
    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=output_path,
        config=config,
        client=client,  # type: ignore[arg-type]
        upload_enabled=True,
        upload_album="Stable Retry Album",
    )
    phase_events = []
    params.phase_callback = phase_events.append
    state = AppState(
        config=config,
        generation_options={"music_source": "None"},
        upload_enabled=True,
    )
    notifications: list[str] = []

    async def io_bound(callback, *args, **kwargs):
        return callback(*args, **kwargs)

    monkeypatch.setattr(step4_generate, "validate_output", lambda *_args: _probe())
    monkeypatch.setattr(step4_generate.run, "io_bound", io_bound)
    monkeypatch.setattr(
        "immich_memories.ui.pages._step4_upload.ui.notify",
        lambda message, **_kwargs: notifications.append(message),
    )
    caplog.set_level("WARNING")

    completed = await step4_generate.finalize_ui_generation(
        state,
        params,
        PreparedGeneration(output_path, _h264_plan(), (), 2, 1),
        tracker,
        progress_bar=_Progress(),
        status_label=_Status(),
    )

    saved = RunDatabase(db_path).get_run(f"ui-{client_kind}")
    assert saved is not None
    assert completed.to_dict() == saved.to_dict()
    assert completed.status == "completed"
    assert completed.delivery_status is DeliveryStatus.PENDING
    assert completed.delivery_attempts == expected_attempts
    assert completed.delivery_album == "Stable Retry Album"
    assert state.delivery_status is DeliveryStatus.PENDING
    assert output_path.read_bytes() == b"validated-video"
    assert calls == expected_attempts
    assert configured_literal not in completed.delivery_error
    assert configured_literal not in " ".join(notifications)
    assert configured_literal not in caplog.text
    assert [event.phase.value for event in phase_events][-1] == "delivery"
    assert saved.last_phase.value == "delivery"


@pytest.mark.asyncio
async def test_ui_lifecycle_passes_one_tracker_from_generation_into_finalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The UI orchestration seam cannot replace the tracker between pipeline stages."""
    from immich_memories.ui.pages import _step4_generate as step4_generate

    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"validated-base")
    prepared = PreparedGeneration(output_path, _h264_plan(), (), 2, 1)
    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=output_path,
        config=Config(),
    )
    state = AppState(config=params.config)
    tracker = object()
    events: list[str] = []

    def generate(
        received_params: GenerationParams,
        *,
        run_tracker: object,
        defer_finalization: bool,
    ) -> PreparedGeneration:
        assert received_params is params
        assert run_tracker is tracker
        assert defer_finalization is True
        events.append("generate")
        return prepared

    async def finalize(
        received_state,
        received_params,
        received_prepared,
        run_tracker,
        progress_bar,
        status_label,
    ):
        assert received_state is state
        assert received_params is params
        assert received_prepared is prepared
        assert run_tracker is tracker
        assert isinstance(progress_bar, _Progress)
        assert isinstance(status_label, _Status)
        events.append("finalize")

    async def io_bound(callback, *args, **kwargs):
        return callback(*args, **kwargs)

    monkeypatch.setattr(step4_generate, "generate_memory", generate, raising=False)
    monkeypatch.setattr(step4_generate, "finalize_ui_generation", finalize)
    monkeypatch.setattr(step4_generate.run, "io_bound", io_bound)

    result = await step4_generate.execute_ui_generation(
        state,
        params,
        tracker,
        _Progress(),
        _Status(),
    )

    assert result is prepared
    assert events == ["generate", "finalize"]


@pytest.mark.asyncio
async def test_run_generation_constructs_one_tracker_and_uses_the_lifecycle_seam(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Step 4 cannot let core generation and UI post-processing create separate runs."""
    from immich_memories.ui.pages import _step4_generate as step4_generate

    config = Config(cache={"database": str(tmp_path / "runs.db")})
    state = AppState(config=config, generation_options={"music_source": "None"})
    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"validated-base")
    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=output_path,
        config=config,
    )
    prepared = PreparedGeneration(output_path, _h264_plan(), (), 1, 1)
    constructed: list[object] = []
    lifecycle_trackers: list[object] = []
    shown: list[tuple[Path, DeliveryStatus]] = []

    class Tracker:
        def __init__(self, *_args, **_kwargs) -> None:
            constructed.append(self)

    async def io_bound(callback, *_args, **_kwargs):
        if callback is step4_generate.generate_memory:
            Tracker()
            return output_path
        return callback(*_args, **_kwargs)

    async def execute(_state, _params, tracker, _progress, _status):
        lifecycle_trackers.append(tracker)
        _state.delivery_status = DeliveryStatus.PENDING
        return prepared

    monkeypatch.setattr(step4_generate.ui, "linear_progress", lambda **_kwargs: _Element())
    monkeypatch.setattr(step4_generate.ui, "label", lambda *_args, **_kwargs: _Element())
    monkeypatch.setattr(step4_generate.ui, "image", lambda **_kwargs: _Element())
    monkeypatch.setattr(
        "immich_memories.ui.components.im_button",
        lambda *_args, **_kwargs: _Element(),
    )
    monkeypatch.setattr(step4_generate, "normalize_ui_output_path", lambda *_args: output_path)
    monkeypatch.setattr(step4_generate, "_build_generation_params", lambda *_args: params)
    monkeypatch.setattr(step4_generate.run, "io_bound", io_bound)
    monkeypatch.setattr(step4_generate, "execute_ui_generation", execute)
    monkeypatch.setattr(
        step4_generate,
        "_show_output",
        lambda _container, path, rendered_state: shown.append(
            (path, rendered_state.delivery_status)
        ),
    )
    monkeypatch.setattr("immich_memories.tracking.RunTracker", Tracker)
    monkeypatch.setattr("immich_memories.tracking.generate_run_id", lambda: "one-ui-run")
    monkeypatch.setattr("immich_memories.config.get_config", lambda: config)

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

    assert len(constructed) == 1
    assert lifecycle_trackers == constructed
    assert state.output_path == output_path
    assert shown == [(output_path, DeliveryStatus.PENDING)]


@pytest.mark.asyncio
async def test_post_completion_ui_error_cannot_downgrade_artifact_or_leak_config_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Display failures happen after ownership ends and must not rewrite the run."""
    from immich_memories.ui.pages import _step4_generate as step4_generate

    configured_literal = "post-completion-ui-secret-733"
    db_path = tmp_path / "runs.db"
    config = Config(
        cache={"database": str(db_path)},
        immich={"api_key": configured_literal},
    )
    state = AppState(config=config, generation_options={"music_source": "None"})
    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"validated-video")
    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=output_path,
        config=config,
    )
    prepared = PreparedGeneration(output_path, _h264_plan(), (), 1, 1)
    notifications: list[str] = []
    cards: list[str] = []

    async def execute(_state, _params, tracker, _progress, _status):
        tracker.start_run(source="manual")
        tracker.complete_artifact(
            output_path,
            _probe(),
            warnings=[],
            clips_analyzed=1,
            clips_selected=1,
        )
        return prepared

    def fail_display(*_args) -> None:
        raise RuntimeError(f"display failed with {configured_literal}")

    monkeypatch.setattr(step4_generate.ui, "linear_progress", lambda **_kwargs: _Element())
    monkeypatch.setattr(step4_generate.ui, "label", lambda *_args, **_kwargs: _Element())
    monkeypatch.setattr(step4_generate.ui, "image", lambda **_kwargs: _Element())
    monkeypatch.setattr(
        step4_generate.ui, "notify", lambda message, **_kwargs: notifications.append(message)
    )
    monkeypatch.setattr(
        "immich_memories.ui.components.im_button",
        lambda *_args, **_kwargs: _Element(),
    )
    monkeypatch.setattr(
        step4_generate, "im_info_card", lambda message, **_kwargs: cards.append(message)
    )
    monkeypatch.setattr(step4_generate, "normalize_ui_output_path", lambda *_args: output_path)
    monkeypatch.setattr(step4_generate, "_build_generation_params", lambda *_args: params)
    monkeypatch.setattr(step4_generate, "execute_ui_generation", execute)
    monkeypatch.setattr(step4_generate, "_show_output", fail_display)
    monkeypatch.setattr("immich_memories.tracking.generate_run_id", lambda: "ui-display-error")
    caplog.set_level("ERROR", logger="immich_memories.ui.pages._step4_generate")

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

    saved = RunDatabase(db_path).get_run("ui-display-error")
    assert saved is not None
    assert saved.status == "completed"
    assert saved.output_path == str(output_path)
    assert state.output_path == output_path
    assert configured_literal not in caplog.text
    assert configured_literal not in " ".join(notifications + cards)


@pytest.mark.asyncio
async def test_ui_observer_after_completion_recovers_persisted_session_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A post-finalization observer failure restores the completed UI session from the DB."""
    from immich_memories.ui.pages import _step4_generate as step4_generate

    configured_literal = "post-finalization-observer-secret-919"
    warning = "Optional music failed: backend unavailable"
    db_path = tmp_path / "runs.db"
    config = Config(
        cache={"database": str(db_path)},
        immich={"api_key": configured_literal},
    )
    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"validated-video")
    state = AppState(config=config, generation_options={"music_source": "None"})
    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=output_path,
        config=config,
    )
    prepared = PreparedGeneration(output_path, _h264_plan(), (), 1, 1)
    notifications: list[str] = []
    cards: list[str] = []
    rendered_paths: list[Path] = []
    linked_paths: list[Path] = []
    show_output = step4_generate._show_output

    async def execute(_state, _params, tracker, _progress, _status):
        tracker.start_run(source="manual")
        tracker.complete_artifact(
            output_path,
            _probe(),
            warnings=[warning],
            clips_analyzed=1,
            clips_selected=1,
        )
        return prepared

    class FailingOutputLabel(_Element):
        def set_text(self, _value: str) -> None:
            raise RuntimeError(f"observer failed with {configured_literal}")

    def label(value="", **_kwargs):
        return FailingOutputLabel(value) if value == "" else _Element(value)

    monkeypatch.setattr(step4_generate.ui, "linear_progress", lambda **_kwargs: _Element())
    monkeypatch.setattr(step4_generate.ui, "label", label)
    monkeypatch.setattr(step4_generate.ui, "image", lambda **_kwargs: _Element())
    monkeypatch.setattr(
        "immich_memories.ui.components.im_button",
        lambda *_args, **_kwargs: _Element(),
    )
    monkeypatch.setattr(
        step4_generate.ui, "notify", lambda message, **_kwargs: notifications.append(message)
    )
    monkeypatch.setattr(
        step4_generate, "im_info_card", lambda message, **_kwargs: cards.append(message)
    )
    monkeypatch.setattr(step4_generate, "normalize_ui_output_path", lambda *_args: output_path)
    monkeypatch.setattr(step4_generate, "_build_generation_params", lambda *_args: params)
    monkeypatch.setattr(step4_generate, "execute_ui_generation", execute)
    monkeypatch.setattr(
        step4_generate, "_show_output", lambda _container, path, _state: rendered_paths.append(path)
    )
    monkeypatch.setattr("immich_memories.tracking.generate_run_id", lambda: "ui-observer-recovery")
    caplog.set_level("ERROR", logger="immich_memories.ui.pages._step4_generate")

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

    saved = RunDatabase(db_path).get_run("ui-observer-recovery")
    assert saved is not None
    assert saved.status == "completed"
    assert state.output_path == output_path
    assert state.generation_warning == warning
    assert state.delivery_status is DeliveryStatus.NOT_REQUESTED
    assert rendered_paths == []
    assert configured_literal not in caplog.text
    assert configured_literal not in " ".join(notifications + cards)

    monkeypatch.setattr(step4_generate.ui, "label", lambda value="", **_kwargs: _Element(value))
    monkeypatch.setattr(step4_generate, "im_separator", lambda: None)
    monkeypatch.setattr(step4_generate.ui, "element", lambda *_args, **_kwargs: _Element())
    monkeypatch.setattr(step4_generate.ui, "row", lambda *_args, **_kwargs: _Element())
    monkeypatch.setattr(step4_generate.ui, "column", lambda *_args, **_kwargs: _Element())
    monkeypatch.setattr(step4_generate.ui, "icon", lambda *_args, **_kwargs: _Element())
    monkeypatch.setattr(
        step4_generate.ui,
        "video",
        lambda value, **_kwargs: (linked_paths.append(value), _Element())[1],
    )
    monkeypatch.setattr(step4_generate.ui, "run_javascript", lambda *_args, **_kwargs: None)
    show_output(_Container(), state.output_path, state)

    assert linked_paths == [output_path]


@pytest.mark.asyncio
async def test_ui_observer_failure_keeps_saved_classification_when_recovery_read_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A secondary lifecycle read cannot erase completion already copied into the session."""
    from immich_memories.ui.pages import _step4_generate as step4_generate

    configured_literal = "observer-primary-secret-778"
    db_path = tmp_path / "runs.db"
    config = Config(
        cache={"database": str(db_path)},
        immich={"api_key": configured_literal},
    )
    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"validated-video")
    state = AppState(config=config, generation_options={"music_source": "None"})
    params = GenerationParams(
        clips=[make_clip("clip-1")],
        output_path=output_path,
        config=config,
    )
    prepared = PreparedGeneration(output_path, _h264_plan(), (), 1, 1)
    tracker_ref: list[RunTracker] = []
    notifications: list[str] = []
    cards: list[str] = []

    async def execute(_state, _params, tracker, _progress, _status):
        tracker_ref.append(tracker)
        tracker.start_run(source="manual")
        tracker.complete_artifact(
            output_path, _probe(), warnings=[], clips_analyzed=1, clips_selected=1
        )
        return prepared

    class FailingOutputLabel(_Element):
        def set_text(self, _value: str) -> None:
            monkeypatch.setattr(
                tracker_ref[0].db,
                "get_run",
                lambda *_args: (_ for _ in ()).throw(OSError("secondary DB read failed")),
            )
            raise RuntimeError(f"primary observer failed with {configured_literal}")

    def label(value="", **_kwargs):
        return FailingOutputLabel(value) if value == "" else _Element(value)

    monkeypatch.setattr(step4_generate.ui, "linear_progress", lambda **_kwargs: _Element())
    monkeypatch.setattr(step4_generate.ui, "label", label)
    monkeypatch.setattr(step4_generate.ui, "image", lambda **_kwargs: _Element())
    monkeypatch.setattr(
        "immich_memories.ui.components.im_button", lambda *_args, **_kwargs: _Element()
    )
    monkeypatch.setattr(
        step4_generate.ui, "notify", lambda message, **_kwargs: notifications.append(message)
    )
    monkeypatch.setattr(
        step4_generate, "im_info_card", lambda message, **_kwargs: cards.append(message)
    )
    monkeypatch.setattr(step4_generate, "normalize_ui_output_path", lambda *_args: output_path)
    monkeypatch.setattr(step4_generate, "_build_generation_params", lambda *_args: params)
    monkeypatch.setattr(step4_generate, "execute_ui_generation", execute)
    monkeypatch.setattr("immich_memories.tracking.generate_run_id", lambda: "ui-read-failure")

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

    assert state.output_path == output_path
    assert state.delivery_status is DeliveryStatus.NOT_REQUESTED
    assert notifications[0].startswith("Video saved, but the completion screen failed:")
    assert cards[0].startswith("Video saved, but the completion screen failed:")
    assert configured_literal not in " ".join(notifications + cards)


def test_completion_screen_keeps_music_warning_and_truthful_pending_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A saved local video visibly retains nonfatal music and upload outcomes."""
    from immich_memories.ui.pages import _step4_generate as step4_generate

    output_path = tmp_path / "memory.mp4"
    output_path.write_bytes(b"validated-video")
    warning = "Optional music failed: backend unavailable"
    state = AppState(
        output_path=output_path,
        generation_warning=warning,
        delivery_status=DeliveryStatus.PENDING,
    )
    labels: list[str] = []

    monkeypatch.setattr(step4_generate.ui, "notify", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(step4_generate, "im_separator", lambda: None)
    monkeypatch.setattr(step4_generate.ui, "element", lambda *_args, **_kwargs: _Element())
    monkeypatch.setattr(step4_generate.ui, "row", lambda *_args, **_kwargs: _Element())
    monkeypatch.setattr(step4_generate.ui, "column", lambda *_args, **_kwargs: _Element())
    monkeypatch.setattr(step4_generate.ui, "icon", lambda *_args, **_kwargs: _Element())
    monkeypatch.setattr(
        step4_generate.ui,
        "label",
        lambda value, **_kwargs: (labels.append(value), _Element(value))[1],
    )
    monkeypatch.setattr(step4_generate.ui, "video", lambda *_args, **_kwargs: _Element())
    monkeypatch.setattr(step4_generate.ui, "run_javascript", lambda *_args, **_kwargs: None)

    step4_generate._show_output(_Container(), output_path, state)

    assert warning in labels
    assert "Immich delivery: Pending" in labels
    assert any("Saved to:" in label for label in labels)
