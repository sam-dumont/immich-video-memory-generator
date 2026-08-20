"""Standalone video generation orchestrator.

Decoupled from NiceGUI — usable from CLI, scheduler, or UI.
All UI interaction is replaced by a progress callback.
"""

from __future__ import annotations

import inspect
import io
import logging
import shutil
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NoReturn, TypeVar, cast, overload

from immich_memories.filename_builder import normalize_output_path
from immich_memories.generate_clips import (
    MIN_CLIP_DURATION,
    _cleanup_temp_clips,
    _cleanup_temp_dirs,
    _extract_clips,
    _probe_file_duration,
    assets_to_clips,
)
from immich_memories.generate_photos import (
    _add_photos_if_enabled,
    _apply_unified_budget,
    _build_title_settings_for_overhead,
    _detect_photo_resolution,
    _interleave_clip_types,
    _merge_by_date,
    _parse_clip_date,
    _render_photo_as_clip,
    _render_photos,
)
from immich_memories.generate_privacy import (
    anonymize_clips_for_privacy,
    anonymize_name,
    anonymize_preset_params,
)
from immich_memories.generate_settings import (
    _build_assembly_settings,
    _build_title_settings,
    _create_assembler,
    _run_music_phase,
    _upload_to_immich,
)
from immich_memories.generate_timeline import (
    apply_final_content_budget as _apply_final_content_budget,
)
from immich_memories.generate_timeline import publish_and_check_duration
from immich_memories.operations.phases import OperationalPhase, PhaseEvent
from immich_memories.processing.clip_validation import validate_clips
from immich_memories.processing.output_canvas import OutputCanvas
from immich_memories.processing.output_contract import validate_output
from immich_memories.security import configured_secret_values, sanitize_error_message

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.config_loader import Config
    from immich_memories.processing.assembly_config import AssemblyClip
    from immich_memories.processing.encoding_plan import EncodingPlan
    from immich_memories.processing.probe_cache import ProbeCache
    from immich_memories.processing.timeline_budget import TimelinePlan
    from immich_memories.tracking import RunTracker

logger = logging.getLogger(__name__)

_CallResult = TypeVar("_CallResult")

# Re-export all extracted symbols so existing callers continue to work
__all__ = [
    "GenerationParams",
    "PreparedGeneration",
    "GenerationError",
    "DeliveryError",
    "deliver_completed_artifact",
    "PipelineLock",
    "generate_memory",
    "check_disk_space",
    "assets_to_clips",
    "MIN_CLIP_DURATION",
    "_add_photos_if_enabled",
    "_apply_unified_budget",
    "_build_title_settings_for_overhead",
    "_detect_photo_resolution",
    "_interleave_clip_types",
    "_merge_by_date",
    "_parse_clip_date",
    "_render_photo_as_clip",
    "_render_photos",
    "_probe_file_duration",
    "_extract_clips",
    "_cleanup_temp_clips",
    "_cleanup_temp_dirs",
    "_build_assembly_settings",
    "_build_title_settings",
    "_create_assembler",
    "_run_music_phase",
    "_upload_to_immich",
]


@dataclass
class GenerationParams:
    """All parameters needed for video generation, decoupled from UI state."""

    clips: list[VideoClipInfo]
    output_path: Path
    config: Config

    # Immich connection (needed for downloads and upload)
    client: SyncImmichClient | None = None

    # Assembly settings
    transition: str = "crossfade"
    transition_duration: float = 0.5
    output_resolution: str | None = None
    output_orientation: str | None = None
    output_canvas: OutputCanvas | None = None
    output_crf: int | None = None

    # Title settings
    title: str | None = None
    subtitle: str | None = None
    memory_type: str | None = None
    memory_preset_params: dict = field(default_factory=dict)
    person_name: str | None = None
    date_start: date | None = None
    date_end: date | None = None
    source: str = "manual"
    memory_key_override: str | None = None
    memory_category: str | None = None
    memory_people: tuple[str, ...] = ()
    automation_attempt_id: str | None = None

    # Music
    music_path: Path | None = None
    music_volume: float = 0.5
    no_music: bool = False

    # Upload
    upload_enabled: bool = False
    upload_album: str | None = None

    # Clip overrides from review step
    clip_segments: dict[str, tuple[float, float]] = field(default_factory=dict)
    clip_rotations: dict[str, int | None] = field(default_factory=dict)

    # Output format and display
    scale_mode: str | None = None
    output_format: str | None = None
    add_date_overlay: bool = False
    debug_preserve_intermediates: bool = False

    # Privacy mode
    privacy_mode: bool = False

    # Photo support
    include_photos: bool = False
    photo_assets: list | None = None  # Pre-fetched photo assets (IMAGE type)

    # Duration budget for unified photo+video selection
    target_duration_seconds: float | None = None
    timeline_plan: TimelinePlan | None = None

    # Pre-selected photo IDs from UI (skip re-scoring when set)
    selected_photo_ids: set[str] | None = None

    # Progress callback: (phase, progress_fraction, status_message)
    progress_callback: Callable[[str, float, str], None] | None = None

    # Stable outer lifecycle. Legacy progress_callback remains a compatibility detail.
    phase_callback: Callable[[PhaseEvent], None] | None = None
    completed_operational_phase: OperationalPhase | None = None

    # Frame preview callback: receives JPEG bytes for live UI thumbnail
    frame_preview_callback: Callable[[bytes], None] | None = None


@dataclass(frozen=True, slots=True)
class PreparedGeneration:
    """Published base artifact awaiting caller-managed post-processing."""

    path: Path
    encoding_plan: EncodingPlan
    assembly_clips: tuple[AssemblyClip, ...]
    clips_analyzed: int
    clips_selected: int


class GenerationError(Exception):
    """Raised when video generation fails."""


class DeliveryError(GenerationError):
    """Raised when a completed artifact could not be delivered to Immich."""


class PipelineLock:
    """File-based lock preventing concurrent pipeline runs.

    Uses fcntl.flock() for cross-process exclusion. Non-blocking —
    raises GenerationError immediately if another instance holds the lock.
    """

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._fd: io.TextIOWrapper | None = None

    def __enter__(self) -> PipelineLock:
        import fcntl

        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = self._lock_path.open("w")
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._fd.close()
            self._fd = None
            raise GenerationError(
                f"Another instance is already running. Lock file: {self._lock_path}"
            )
        return self

    def __exit__(self, *exc: object) -> None:
        import fcntl

        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()
            self._fd = None


# Minimum free disk space before starting generation
_MIN_FREE_BYTES = 1024 * 1024 * 1024  # 1 GB


def check_disk_space(output_dir: Path) -> None:
    """Abort early if disk space is critically low."""
    usage = shutil.disk_usage(output_dir)
    if usage.free < _MIN_FREE_BYTES:
        free_gb = usage.free / (1024**3)
        raise GenerationError(f"Insufficient disk space: {free_gb:.1f} GB free, need at least 1 GB")


def _report(params: GenerationParams, phase: str, progress: float, msg: str) -> None:
    if params.progress_callback:
        params.progress_callback(phase, progress, msg)


def emit_operational_phase(
    params: GenerationParams,
    run_tracker: RunTracker,
    phase: OperationalPhase,
    *,
    current: int,
    total: int,
    message: str,
    elapsed_seconds: float = 0.0,
) -> PhaseEvent:
    """Publish and persist an outer phase without making telemetry job-critical."""
    event = PhaseEvent(phase, current, total, message, elapsed_seconds)
    try:
        run_tracker.record_phase_event(event)
    except Exception:  # WHY: extension trackers must not make status telemetry fatal
        logger.warning("Could not persist operational phase '%s'", phase.value)
    if params.phase_callback is not None:
        try:
            params.phase_callback(event)
        except Exception:  # WHY: observer failures cannot invalidate completed pipeline work
            logger.warning("Operational phase observer failed for '%s'", phase.value)
    return event


class _OperationalProgress:
    """Emit one monotonic outer lifecycle around generation internals."""

    def __init__(self, params: GenerationParams, run_tracker: RunTracker) -> None:
        self._params = params
        self._run_tracker = run_tracker
        self._started = time.monotonic()
        self._last_phase: OperationalPhase | None = None

    def emit(
        self,
        phase: OperationalPhase,
        current: int,
        total: int,
        message: str,
    ) -> PhaseEvent:
        now = time.monotonic()
        event = emit_operational_phase(
            self._params,
            self._run_tracker,
            phase,
            current=current,
            total=total,
            message=message,
            elapsed_seconds=now - self._started,
        )
        self._started = now
        self._last_phase = phase
        return event

    def emit_unperformed_prerequisites(self, through: OperationalPhase) -> None:
        """Mark only prerequisites not owned by this generation call as complete."""
        completed = self._params.completed_operational_phase
        messages = {
            OperationalPhase.DISCOVERY: "Discovery not required",
            OperationalPhase.DOWNLOAD: "Downloads already prepared",
            OperationalPhase.ANALYSIS: "Analysis already prepared",
            OperationalPhase.SELECTION: "Selection already prepared",
        }
        for phase, message in messages.items():
            if (
                phase.order <= through.order
                and (completed is None or phase.order > completed.order)
                and (self._last_phase is None or phase.order > self._last_phase.order)
            ):
                self.emit(phase, 0, 0, message)

    def phase_is_unperformed(self, phase: OperationalPhase) -> bool:
        completed = self._params.completed_operational_phase
        return completed is None or phase.order > completed.order


class _PipelineProgress:
    """Maps per-phase 0.0-1.0 progress into the overall pipeline range.

    Each phase gets a proportional slice of 0.0-1.0 based on estimated
    wall-clock time. All progress reports go through this to ensure the
    bar only moves forward, never jumps backward.
    """

    def __init__(self, params: GenerationParams, clip_count: int) -> None:
        self._params = params
        has_music = not params.no_music

        # WHY: Estimated relative durations for each phase.
        # These determine how much of the progress bar each phase occupies.
        # Tune based on _log_phase_timing output from real runs.
        weights = {
            "download": clip_count * 3.0,
            "photos": 20.0,
            "assembly": 180.0 + clip_count * 8.0,  # titles + encoding
            "music": 120.0 if has_music else 0.0,
        }
        total = sum(weights.values())

        # Build [start, end) ranges for each phase
        self._ranges: dict[str, tuple[float, float]] = {}
        cursor = 0.0
        for phase, w in weights.items():
            span = w / total if total > 0 else 0
            self._ranges[phase] = (cursor, cursor + span)
            cursor += span

    def report(self, phase: str, pct: float, msg: str) -> None:
        """Report progress within a phase. pct is 0.0-1.0 within that phase."""
        if not self._params.progress_callback:
            return
        start, end = self._ranges.get(phase, (0.0, 1.0))
        scaled = start + pct * (end - start)
        self._params.progress_callback(phase, scaled, msg)

    def assembly_callback(self) -> Callable[[float, str], None] | None:
        """Create a 2-arg callback for assemble_with_titles."""
        if not self._params.progress_callback:
            return None

        def cb(pct: float, msg: str) -> None:
            self.report("assembly", pct, msg)

        return cb


@overload
def generate_memory(
    params: GenerationParams,
    *,
    run_tracker: RunTracker | None = None,
    defer_finalization: Literal[False] = False,
) -> Path: ...


@overload
def generate_memory(
    params: GenerationParams,
    *,
    run_tracker: RunTracker,
    defer_finalization: Literal[True],
) -> PreparedGeneration: ...


def generate_memory(
    params: GenerationParams,
    *,
    run_tracker: RunTracker | None = None,
    defer_finalization: bool = False,
) -> Path | PreparedGeneration:
    """Run the full video generation pipeline synchronously.

    Acquires a file lock to prevent concurrent runs, then executes
    the full pipeline: extract → assemble → music → upload.
    """
    if not params.clips:
        raise GenerationError("No clips provided for generation")
    if defer_finalization and run_tracker is None:
        raise ValueError("Deferred finalization requires a caller-owned RunTracker")

    # Single-instance lock: prevent concurrent pipeline runs from corrupting state
    lock_path = params.config.cache.database_path.parent / ".lock"
    with PipelineLock(lock_path):
        if run_tracker is None and not defer_finalization:
            return _generate_memory_inner(params)
        return _generate_memory_inner(
            params,
            run_tracker=run_tracker,
            defer_finalization=defer_finalization,
        )


def _build_memory_key(params: GenerationParams) -> str | None:
    """Compute deterministic dedup key from generation params, or None if incomplete."""
    if params.memory_key_override is not None:
        return params.memory_key_override
    if not (params.memory_type and params.date_start and params.date_end):
        return None
    from immich_memories.automation.candidates import make_memory_key

    person_names = [params.person_name] if params.person_name else []
    return make_memory_key(params.memory_type, params.date_start, params.date_end, person_names)


def _safe_delivery_message(exc: Exception, config: Config) -> str:
    """Sanitize one delivery error, including unlabelled configured secrets."""
    safe_message = sanitize_error_message(str(exc))
    for secret in configured_secret_values(config):
        safe_message = safe_message.replace(secret, "***")
    return safe_message


def _pending_delivery_error(
    run_tracker: RunTracker,
    message: str,
    *,
    attempted: bool,
) -> DeliveryError:
    """Persist retry state and build a safe error outside any raw exception chain."""
    try:
        run_tracker.mark_delivery_pending(message, attempted=attempted)
    except Exception:  # WHY: secondary details may contain secrets; keep the artifact primary
        logger.error("Could not persist pending delivery state")
    return DeliveryError(f"Immich delivery failed: {message}")


def _raise_delivery_error(
    run_tracker: RunTracker,
    message: str,
    *,
    attempted: bool,
) -> NoReturn:
    """Persist retry state and raise without invalidating the completed artifact."""
    error = _pending_delivery_error(run_tracker, message, attempted=attempted)
    raise error from None


def deliver_completed_artifact(
    params: GenerationParams,
    result_path: Path,
    run_tracker: RunTracker,
) -> dict | None:
    """Deliver a completed artifact and persist exactly one API attempt."""
    if not params.upload_enabled:
        return None
    if params.client is None:
        _raise_delivery_error(
            run_tracker,
            "no Immich client is configured",
            attempted=False,
        )

    _report(params, "upload", 0.95, "Uploading to Immich...")
    delivery_error: DeliveryError | None = None
    asset_id: str | None = None
    try:
        result = _upload_to_immich(params.client, result_path, params.upload_album)
        asset_id = result.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id.strip():
            raise ValueError("Immich upload returned no asset ID")
    except Exception as exc:
        safe_message = _safe_delivery_message(exc, params.config)
        logger.warning("Immich delivery failed: %s", safe_message)
        delivery_error = _pending_delivery_error(
            run_tracker,
            safe_message,
            attempted=True,
        )
    if delivery_error is not None:
        raise delivery_error from None

    assert asset_id is not None  # validated in the API-call boundary above
    normalized_asset_id = asset_id.strip()
    try:
        run_tracker.mark_delivered(asset_id)
    except Exception as exc:
        persisted = None
        try:
            persisted = run_tracker.db.get_run(run_tracker.run_id)
        except Exception:  # WHY: an ambiguous transition must not trigger a second upload
            logger.error("Could not inspect successful Immich delivery state")
        if (
            persisted is not None
            and persisted.delivery_status.value == "delivered"
            and persisted.immich_asset_id == normalized_asset_id
        ):
            return result
        safe_message = _safe_delivery_message(exc, params.config)
        logger.error("Could not persist successful Immich delivery: %s", safe_message)
        delivery_error = DeliveryError(f"Immich delivery state update failed: {safe_message}")
    if delivery_error is not None:
        raise delivery_error from None
    return result


def _deliver_completed_artifact(
    params: GenerationParams,
    result_path: Path,
    run_tracker: RunTracker,
) -> dict | None:
    """Compatibility wrapper for the original internal delivery boundary."""
    return deliver_completed_artifact(params, result_path, run_tracker)


def _deliver_with_operational_progress(
    params: GenerationParams,
    result_path: Path,
    run_tracker: RunTracker,
    operational: _OperationalProgress,
) -> None:
    """Expose optional delivery while preserving its existing error boundary."""
    operational.emit(
        OperationalPhase.DELIVERY,
        0,
        1 if params.upload_enabled else 0,
        "Uploading to Immich" if params.upload_enabled else "Delivery not requested",
    )
    _deliver_completed_artifact(params, result_path, run_tracker)
    if params.upload_enabled:
        operational.emit(OperationalPhase.DELIVERY, 1, 1, "Delivered to Immich")


def _complete_music_phase(
    params: GenerationParams,
    assembly_clips: list,
    result_path: Path,
    run_output_dir: Path,
    run_tracker: RunTracker,
    encoding_plan: EncodingPlan,
    operational: _OperationalProgress,
    progress: _PipelineProgress,
):
    """Run or explicitly skip music while emitting the shared outer phase."""
    if params.no_music:
        from immich_memories.generate_music import MusicPhaseResult

        operational.emit(OperationalPhase.MUSIC, 0, 0, "Music disabled")
        return MusicPhaseResult(applied=False)

    operational.emit(OperationalPhase.MUSIC, 0, 1, "Generating music")
    progress.report("music", 0.0, "Generating music...")
    result = _run_music_phase(
        params,
        assembly_clips,
        result_path,
        run_output_dir,
        run_tracker,
        encoding_plan=encoding_plan,
    )
    progress.report("music", 1.0, result.warning or "Music ready")
    operational.emit(OperationalPhase.MUSIC, 1, 1, result.warning or "Music ready")
    return result


def _fail_run_if_running(run_tracker: RunTracker, message: str) -> None:
    """Fail only the still-running authoritative row, never a committed artifact."""
    try:
        persisted = run_tracker.db.get_run(run_tracker.run_id)
    except Exception:  # WHY: an unknown lifecycle must not be destructively rewritten
        logger.error("Could not inspect run lifecycle after generation failure")
        return
    if persisted is None or persisted.status != "running":
        return
    try:
        run_tracker.fail_run(message)
    except Exception:  # WHY: preserve the primary safe generation failure
        logger.error("Could not persist generation failure state")


def _build_download_coordinator(
    params: GenerationParams,
    cache_batch,
    output_dir: Path,
):
    """Build isolated download workers without sharing the generation client."""
    if params.client is None:
        return None

    from immich_memories.generate_downloads import _download_temporary_asset
    from immich_memories.processing.download_coordinator import (
        DownloadCoordinator,
        build_sync_client_factory,
        has_sync_client_connection,
    )

    if not has_sync_client_connection(params.client):
        logger.debug("Download prefetch disabled: client cannot seed isolated workers")
        return None

    download_operation = None
    if cache_batch is None:

        def download_temporary_asset(client, asset):
            return _download_temporary_asset(client, asset, output_dir)

        download_operation = download_temporary_asset
    return DownloadCoordinator(
        build_sync_client_factory(params.client, params.config.immich.api_version),
        cache_batch,
        params.config.analysis.download_workers,
        download_operation=download_operation,
    )


def _call_with_optional_probe_cache(
    callable_under_test: Callable[..., _CallResult],
    *args: object,
    probe_cache: ProbeCache,
    **kwargs: object,
) -> _CallResult:
    """Pass run-scoped probing only through extension callables that accept it."""
    signature_target = getattr(callable_under_test, "side_effect", None)
    if not callable(signature_target):
        signature_target = callable_under_test
    parameters: Iterable[inspect.Parameter]
    try:
        parameters = inspect.signature(signature_target).parameters.values()
    except (TypeError, ValueError):
        parameters = ()
    accepts_probe_cache = any(
        parameter.name == "probe_cache" or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )
    if accepts_probe_cache:
        kwargs["probe_cache"] = probe_cache
    return callable_under_test(*args, **kwargs)


def _build_settings_with_optional_probe_cache(
    params: GenerationParams,
    assembly_clips: list,
    *,
    probe_cache: ProbeCache,
):
    return _call_with_optional_probe_cache(
        _build_assembly_settings,
        params,
        assembly_clips,
        probe_cache=probe_cache,
    )


def _create_assembler_with_optional_probe_cache(
    settings,
    config: Config,
    *,
    probe_cache: ProbeCache,
):
    return _call_with_optional_probe_cache(
        _create_assembler,
        settings,
        config,
        probe_cache=probe_cache,
    )


def _extract_clips_with_optional_prefetch(
    params: GenerationParams,
    cache_batch,
    output_dir: Path,
    *,
    probe_cache: ProbeCache,
) -> list:
    """Keep the legacy extraction call shape when prefetch is unavailable."""
    coordinator = _build_download_coordinator(params, cache_batch, output_dir)
    if coordinator is None:
        return _call_with_optional_probe_cache(
            _extract_clips,
            params,
            cache_batch,
            output_dir,
            probe_cache=probe_cache,
        )
    return _call_with_optional_probe_cache(
        _extract_clips,
        params,
        cache_batch,
        output_dir,
        download_coordinator=coordinator,
        probe_cache=probe_cache,
    )


def _emit_download_phase(
    operational: _OperationalProgress,
    params: GenerationParams,
    current: int,
    total: int,
    message: str,
) -> None:
    """Report direct-generation download work only while this call owns it."""
    if operational.phase_is_unperformed(OperationalPhase.DOWNLOAD):
        operational.emit(OperationalPhase.DOWNLOAD, current, total, message)


def _generate_memory_inner(
    params: GenerationParams,
    *,
    run_tracker: RunTracker | None = None,
    defer_finalization: bool = False,
) -> Path | PreparedGeneration:
    """Inner pipeline — runs under PipelineLock."""
    from immich_memories.cache.video_cache import VideoDownloadCache
    from immich_memories.security import sanitize_filename
    from immich_memories.tracking import RunTracker, generate_run_id

    run_id = run_tracker.run_id if run_tracker is not None else generate_run_id()

    # Tag all log lines with run_id for correlation
    from immich_memories.logging_config import set_current_run_id

    set_current_run_id(run_id)

    if run_tracker is None:
        run_tracker = RunTracker(run_id, db_path=params.config.cache.database_path)

    # Create output directory structure
    dir_slug = params.output_path.stem
    run_output_dir = params.output_path.parent / f"{dir_slug}_{run_id}"
    run_output_dir.mkdir(parents=True, exist_ok=True)

    # Preflight: abort early if disk is critically low
    check_disk_space(run_output_dir)
    requested_output_path = run_output_dir / sanitize_filename(params.output_path.name)

    run_tracker.start_run(
        person_name=params.person_name,
        date_range=None,
        target_duration_seconds=round(
            params.target_duration_seconds or _total_clip_duration(params)
        ),
        memory_type=params.memory_type,
        memory_key=_build_memory_key(params),
        memory_category=params.memory_category,
        memory_people=params.memory_people,
        source=params.source,
        automation_attempt_id=params.automation_attempt_id,
    )
    operational = _OperationalProgress(params, run_tracker)
    operational.emit_unperformed_prerequisites(OperationalPhase.DISCOVERY)

    assembly_clips: list = []  # WHY: populated in try, needed in finally for cleanup
    pending_error: GenerationError | None = None
    from immich_memories.processing.probe_cache import ProbeCache

    probe_cache = ProbeCache()
    try:
        import time as _time

        _phase_times: dict[str, float] = {}
        _phase_start = _time.monotonic()
        pp = _PipelineProgress(params, len(params.clips))

        # Phase 1: Download and extract clips
        pp.report("download", 0.0, "Downloading clips...")
        run_tracker.start_phase("clip_extraction", len(params.clips))
        _emit_download_phase(
            operational,
            params,
            0,
            len(params.clips),
            "Preparing source downloads",
        )

        if params.config.cache.video_cache_enabled:
            video_cache = VideoDownloadCache(
                cache_dir=params.config.cache.video_cache_path,
                max_size_gb=params.config.cache.video_cache_max_size_gb,
                max_age_days=params.config.cache.video_cache_max_age_days,
            )
            # One cache lifecycle owns all persistent-cache downloads for this
            # generation. It scans once, then evicts from the manifest on exit.
            with video_cache.begin_batch() as cache_batch:
                assembly_clips = _extract_clips_with_optional_prefetch(
                    params,
                    cache_batch,
                    run_output_dir,
                    probe_cache=probe_cache,
                )
        else:
            # Disabled cache means no interaction with the configured persistent
            # video cache. Extraction uses disposable run-local downloads.
            assembly_clips = _extract_clips_with_optional_prefetch(
                params,
                None,
                run_output_dir,
                probe_cache=probe_cache,
            )
        run_tracker.complete_phase(items_processed=len(assembly_clips))
        _emit_download_phase(
            operational,
            params,
            len(assembly_clips),
            len(params.clips),
            "Sources prepared",
        )
        operational.emit_unperformed_prerequisites(OperationalPhase.SELECTION)
        operational.emit(OperationalPhase.RENDER, 0, len(params.clips), "Rendering memory")
        _phase_times["download"] = _time.monotonic() - _phase_start
        pp.report("download", 1.0, "Clips downloaded")

        # Phase 1b: Unified budget selection + render selected photos
        _t = _time.monotonic()
        pp.report("photos", 0.0, "Selecting and rendering photos...")
        assembly_clips = _add_photos_if_enabled(assembly_clips, params, run_output_dir)
        _phase_times["photos"] = _time.monotonic() - _t
        pp.report("photos", 1.0, "Photos ready")

        # Pre-assembly validation: skip clips with missing/empty files
        assembly_clips, skipped = validate_clips(assembly_clips)

        if not assembly_clips:
            raise GenerationError("No clips could be processed")

        # Privacy mode: anonymize GPS + names before title/assembly
        if params.privacy_mode:
            assembly_clips = anonymize_clips_for_privacy(assembly_clips)
            anon_preset = anonymize_preset_params(params.memory_preset_params)
            params = replace(
                params,
                person_name=anonymize_name(params.person_name),
                memory_preset_params=anon_preset,
            )

        assembly_clips = _apply_final_content_budget(params, assembly_clips)

        # Phase 2: Assemble (includes title generation + streaming encode)
        _t = _time.monotonic()
        assembly_cb = pp.assembly_callback()
        run_tracker.start_phase("assembly", len(assembly_clips))

        settings = _build_settings_with_optional_probe_cache(
            params,
            assembly_clips,
            probe_cache=probe_cache,
        )
        result_output_path = normalize_output_path(
            requested_output_path,
            cast(Literal["mp4", "mov"], settings.encoding_plan.container),
        )
        assembler = _create_assembler_with_optional_probe_cache(
            settings,
            params.config,
            probe_cache=probe_cache,
        )
        staged_output_path = result_output_path.with_name(
            f"{result_output_path.stem}.assembling{result_output_path.suffix}"
        )
        staged_result_path = assembler.assemble_with_titles(
            assembly_clips,
            staged_output_path,
            assembly_cb,
            frame_preview_callback=params.frame_preview_callback,
        )
        plan = settings.encoding_plan
        metrics, duration_warning = publish_and_check_duration(
            params, staged_result_path, result_output_path, plan
        )
        result_path = result_output_path
        run_tracker.complete_phase(items_processed=len(assembly_clips), extra_metrics=metrics)
        operational.emit(
            OperationalPhase.RENDER,
            len(assembly_clips),
            len(assembly_clips),
            "Render complete",
        )
        _phase_times["assembly"] = _time.monotonic() - _t

        if defer_finalization:
            return PreparedGeneration(
                path=result_path,
                encoding_plan=settings.encoding_plan,
                assembly_clips=tuple(assembly_clips),
                clips_analyzed=len(params.clips),
                clips_selected=len(assembly_clips),
            )

        # Phase 3: Music
        _t = _time.monotonic()
        music_result = _complete_music_phase(
            params,
            assembly_clips,
            result_path,
            run_output_dir,
            run_tracker,
            settings.encoding_plan,
            operational,
            pp,
        )
        _phase_times["music"] = _time.monotonic() - _t

        final_probe = validate_output(result_path, settings.encoding_plan)
        artifact_warnings = [w for w in (duration_warning, music_result.warning) if w]
        run_tracker.complete_artifact(
            result_path,
            final_probe,
            artifact_warnings,
            delivery_requested=params.upload_enabled,
            delivery_album=params.upload_album,
            clips_analyzed=len(params.clips),
            clips_selected=len(assembly_clips),
        )

        # Phase 4: Upload (if requested)
        _deliver_with_operational_progress(params, result_path, run_tracker, operational)

        _phase_times["total"] = _time.monotonic() - _phase_start
        _log_phase_timing(_phase_times, len(assembly_clips))

        _report(params, "done", 1.0, "Complete!")
        operational.emit(OperationalPhase.COMPLETE, 1, 1, "Complete")

        return result_path

    except DeliveryError:
        raise
    except GenerationError as e:
        safe_msg = _safe_delivery_message(e, params.config)
        _fail_run_if_running(run_tracker, safe_msg)
        pending_error = GenerationError(safe_msg)
    except (
        Exception
    ) as e:  # WHY: top-level generation boundary — converts unknown errors to GenerationError
        safe_msg = _safe_delivery_message(e, params.config)
        logger.error("Video generation failed: %s", safe_msg)
        _fail_run_if_running(run_tracker, safe_msg)
        pending_error = GenerationError(f"Generation failed: {safe_msg}")
    finally:
        try:
            _cleanup_temp_clips(assembly_clips)
        except OSError:
            logger.debug("Temp clip cleanup failed", exc_info=True)
        try:
            if not params.debug_preserve_intermediates:
                _cleanup_temp_dirs(run_output_dir)
        except OSError:
            logger.debug("Temp dir cleanup failed", exc_info=True)
        set_current_run_id(None)

    assert pending_error is not None  # one of the non-delivery exception branches set it
    raise pending_error from None


def _log_phase_timing(times: dict[str, float], clip_count: int) -> None:
    """Log phase durations to help tune progress bar estimates."""
    total = times.get("total", 0)
    parts = []
    for phase in ("download", "photos", "assembly", "music"):
        dur = times.get(phase, 0)
        pct = (dur / total * 100) if total > 0 else 0
        parts.append(f"{phase}={dur:.1f}s ({pct:.0f}%)")
    logger.info(f"Pipeline timing ({clip_count} clips, {total:.1f}s total): {', '.join(parts)}")


def _total_clip_duration(params: GenerationParams) -> int:
    total: float = 0.0
    for clip in params.clips:
        seg = params.clip_segments.get(clip.asset.id)
        if seg:
            total += seg[1] - seg[0]
        else:
            total += clip.duration_seconds or 5.0
    return int(total)
