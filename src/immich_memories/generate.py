"""Standalone video generation orchestrator.

Decoupled from NiceGUI — usable from CLI, scheduler, or UI.
All UI interaction is replaced by a progress callback.
"""

from __future__ import annotations

import io
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Literal, overload

from immich_memories.generate_clips import (
    MIN_CLIP_DURATION,
    _cleanup_temp_clips,
    _cleanup_temp_dirs,
    _extract_clips,
    _probe_file_duration,
    assets_to_clips,
)
from immich_memories.generate_delivery import (
    _deliver_with_operational_progress,
    _safe_delivery_message,
)
from immich_memories.generate_photos import (
    _detect_photo_resolution,
    _render_photo_as_clip,
)
from immich_memories.generate_settings import (
    _build_assembly_settings,
    _build_title_settings,
    _create_assembler,
    _run_music_phase,
)
from immich_memories.operations.phases import OperationalPhase, PhaseEvent
from immich_memories.operations.run_index import record_run_attempt
from immich_memories.processing.output_canvas import OutputCanvas
from immich_memories.processing.output_contract import (
    DecodeCheck,
    OutputProbe,
    publish_validated_output,
    validate_output,
)

if TYPE_CHECKING:
    from immich_memories.analysis.editorial_planner import EditorialSelection
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.config_loader import Config
    from immich_memories.processing.assembly_config import AssemblyClip
    from immich_memories.processing.encoding_plan import EncodingPlan
    from immich_memories.processing.timeline_budget import TimelinePlan
    from immich_memories.tracking import RunTracker

from immich_memories.generate_progress import (  # noqa: E402
    _OperationalProgress,
    _PipelineProgress,
    _report,
)

logger = logging.getLogger(__name__)

# Re-export all extracted symbols so existing callers continue to work
__all__ = [
    "GenerationParams",
    "PreparedGeneration",
    "GenerationError",
    "DeliveryError",
    "PipelineLock",
    "generate_memory",
    "check_disk_space",
    "assets_to_clips",
    "MIN_CLIP_DURATION",
    "_detect_photo_resolution",
    "_render_photo_as_clip",
    "_probe_file_duration",
    "_extract_clips",
    "_cleanup_temp_clips",
    "_cleanup_temp_dirs",
    "_build_assembly_settings",
    "_build_title_settings",
    "_create_assembler",
    "_run_music_phase",
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
    # Who wrote `title`: a TitleSource value; None reads as a plain override.
    title_source: str | None = None
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
    editorial_selections: tuple[EditorialSelection, ...] = ()

    # Output format and display
    scale_mode: str | None = None
    output_format: str | None = None
    add_date_overlay: bool = False
    add_place_overlay: bool = False
    debug_preserve_intermediates: bool = False

    # Privacy mode
    privacy_mode: bool = False

    # Photo support
    include_photos: bool = False
    photo_assets: list | None = None  # Pre-fetched photo assets (IMAGE type)

    # Duration budget for unified photo+video selection
    target_duration_seconds: float | None = None
    timeline_plan: TimelinePlan | None = None
    editorial_render_timing: dict | None = None
    editorial_duration_realization: dict | None = None
    # The attempt the cut was selected from, so the run id can find its plan and trace.
    editorial_attempt_dir: Path | None = None
    # Explicit review changes; the original editorial plan remains unchanged.
    editorial_owner_edits: dict | None = None

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
    """A rendered film awaiting caller-managed post-processing and its one decode check.

    ``path`` is where the film is published. Until ``publish`` runs, the film
    sits at ``staged_path``, and music is mixed into it there.
    """

    path: Path
    encoding_plan: EncodingPlan
    assembly_clips: tuple[AssemblyClip, ...]
    clips_analyzed: int
    clips_selected: int
    # The assembly engine is the only place the final sequence and its
    # transitions coexist, so a caller finishing the run later cannot work these
    # out for itself -- they have to travel with the artifact (#466, #479).
    music_mute_windows: list[tuple[float, float]] | None = None
    duration_warning: str | None = None
    render_metrics: dict[str, object] = field(default_factory=dict)
    # Wall time this machine spent rendering the film; None when a worker did.
    # The decode check of the same film is bounded by it.
    encode_seconds: float | None = None
    # None when the film is already at ``path``.
    staged_path: Path | None = None
    # A decode that already vouches for these bytes: the worker's, bound by digest.
    verified: OutputProbe | None = None

    @property
    def current_path(self) -> Path:
        """Where the film is now, and where post-processing writes."""
        return self.staged_path or self.path

    def publish(self, decode_check: DecodeCheck | None = None) -> OutputProbe:
        """Decode the film once, as it stands after its last write, then publish it at ``path``.

        A decode that already vouches for the same bytes is reused. A film that
        fails stays where it is, and the error names it.
        """
        if self.staged_path is None:
            return validate_output(
                self.path, self.encoding_plan, decode_check, verified=self.verified
            )
        return publish_validated_output(
            self.staged_path,
            self.path,
            self.encoding_plan,
            decode_check=decode_check,
            verified=self.verified,
        )


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
    the full pipeline: extract → assemble → music → one decode check and
    publish → upload. With ``defer_finalization`` it returns after assembly,
    and the caller owns the music and ``PreparedGeneration.publish``.
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


def _complete_music_phase(
    params: GenerationParams,
    assembly_clips: list,
    result_path: Path,
    run_output_dir: Path,
    run_tracker: RunTracker,
    encoding_plan: EncodingPlan,
    operational: _OperationalProgress,
    progress: _PipelineProgress,
    mute_windows: list[tuple[float, float]] | None = None,
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
        mute_windows=mute_windows,
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


def _artifact_warnings(
    params: GenerationParams, duration_warning: str | None, music_warning: str | None
) -> list[str]:
    from immich_memories.analysis.editorial_duration_advisory import editorial_duration_warning

    return [
        warning
        for warning in (
            editorial_duration_warning(params.editorial_duration_realization),
            duration_warning,
            music_warning,
        )
        if warning
    ]


def _clear_run_intermediates(
    params: GenerationParams, assembly_clips: list, run_output_dir: Path
) -> None:
    """Cleanup never masks the outcome of the run it is closing."""
    try:
        _cleanup_temp_clips(assembly_clips)
    except OSError:
        logger.debug("Temp clip cleanup failed", exc_info=True)
    try:
        if not params.debug_preserve_intermediates:
            _cleanup_temp_dirs(run_output_dir)
    except OSError:
        logger.debug("Temp dir cleanup failed", exc_info=True)


def _generate_memory_inner(
    params: GenerationParams,
    *,
    run_tracker: RunTracker | None = None,
    defer_finalization: bool = False,
) -> Path | PreparedGeneration:
    """Inner pipeline — runs under PipelineLock."""
    from immich_memories.processing.editorial_timing import prepare_certified_timeline

    prepare_certified_timeline(params)
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

    try:
        import time as _time

        _phase_times: dict[str, float] = {}
        _phase_start = _time.monotonic()
        pp = _PipelineProgress(params, len(params.clips))
        params = replace(params, progress_callback=pp.report)

        from immich_memories.generate_render import render_base

        prepared = render_base(
            params,
            requested_output_path,
            run_output_dir,
            run_tracker,
            operational,
            pp,
            _phase_times,
        )
        assembly_clips = list(prepared.assembly_clips)
        result_path = prepared.path
        plan = prepared.encoding_plan
        duration_warning = prepared.duration_warning
        if defer_finalization:
            return prepared

        decode_check = DecodeCheck(
            encode_seconds=prepared.encode_seconds,
            progress=lambda message: pp.report("check", 1.0, message),
        )

        # Phase 3: Music, mixed into the film before it is published
        _t = _time.monotonic()
        music_result = _complete_music_phase(
            params,
            assembly_clips,
            prepared.current_path,
            run_output_dir,
            run_tracker,
            plan,
            operational,
            pp,
            mute_windows=prepared.music_mute_windows,
        )
        _phase_times["music"] = _time.monotonic() - _t

        final_probe = prepared.publish(decode_check)
        artifact_warnings = _artifact_warnings(params, duration_warning, music_result.warning)
        run_tracker.complete_artifact(
            result_path,
            final_probe,
            artifact_warnings,
            delivery_requested=params.upload_enabled,
            delivery_album=params.upload_album,
            clips_analyzed=len(params.clips),
            clips_selected=len(assembly_clips),
        )
        record_run_attempt(
            params.config.cache.cache_path, run_id, params.editorial_attempt_dir, result_path
        )

        # Phase 4: Upload (if requested)
        _deliver_with_operational_progress(
            params,
            result_path,
            run_tracker,
            operational,
            recheck=lambda: validate_output(result_path, plan, decode_check, verified=final_probe),
        )

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
        _clear_run_intermediates(params, assembly_clips, run_output_dir)
        set_current_run_id(None)

    assert pending_error is not None  # one of the non-delivery exception branches set it
    raise pending_error from None


def _log_phase_timing(times: dict[str, float], clip_count: int) -> None:
    """Log phase durations to help tune progress bar estimates."""
    total = times.get("total", 0)
    parts = []
    for phase in ("download", "assembly", "music"):
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
