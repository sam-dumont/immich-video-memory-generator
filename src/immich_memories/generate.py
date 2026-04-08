"""Standalone video generation orchestrator.

Decoupled from NiceGUI — usable from CLI, scheduler, or UI.
All UI interaction is replaced by a progress callback.
"""

from __future__ import annotations

import io
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

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
from immich_memories.processing.clip_validation import validate_clips
from immich_memories.security import sanitize_error_message

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.config_loader import Config

logger = logging.getLogger(__name__)

# Re-export all extracted symbols so existing callers continue to work
__all__ = [
    "GenerationParams",
    "GenerationError",
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
    output_crf: int | None = None

    # Title settings
    title: str | None = None
    subtitle: str | None = None
    memory_type: str | None = None
    memory_preset_params: dict = field(default_factory=dict)
    person_name: str | None = None
    date_start: date | None = None
    date_end: date | None = None

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

    # Pre-selected photo IDs from UI (skip re-scoring when set)
    selected_photo_ids: set[str] | None = None

    # Progress callback: (phase, progress_fraction, status_message)
    progress_callback: Callable[[str, float, str], None] | None = None

    # Frame preview callback: receives JPEG bytes for live UI thumbnail
    frame_preview_callback: Callable[[bytes], None] | None = None


class GenerationError(Exception):
    """Raised when video generation fails."""


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


def generate_memory(params: GenerationParams) -> Path:
    """Run the full video generation pipeline synchronously.

    Acquires a file lock to prevent concurrent runs, then executes
    the full pipeline: extract → assemble → music → upload.
    """
    if not params.clips:
        raise GenerationError("No clips provided for generation")

    # Single-instance lock: prevent concurrent pipeline runs from corrupting state
    lock_path = Path.home() / ".immich-memories" / ".lock"
    with PipelineLock(lock_path):
        return _generate_memory_inner(params)


def _build_memory_key(params: GenerationParams) -> str | None:
    """Compute deterministic dedup key from generation params, or None if incomplete."""
    if not (params.memory_type and params.date_start and params.date_end):
        return None
    from immich_memories.automation.candidates import make_memory_key

    person_names = [params.person_name] if params.person_name else []
    return make_memory_key(params.memory_type, params.date_start, params.date_end, person_names)


def _generate_memory_inner(params: GenerationParams) -> Path:
    """Inner pipeline — runs under PipelineLock."""
    from immich_memories.cache.video_cache import VideoDownloadCache
    from immich_memories.security import sanitize_filename
    from immich_memories.tracking import RunTracker, generate_run_id

    run_id = generate_run_id()

    # Tag all log lines with run_id for correlation
    from immich_memories.logging_config import set_current_run_id

    set_current_run_id(run_id)

    run_tracker = RunTracker(run_id, db_path=params.config.cache.database_path)

    # Create output directory structure
    dir_slug = params.output_path.stem
    run_output_dir = params.output_path.parent / f"{dir_slug}_{run_id}"
    run_output_dir.mkdir(parents=True, exist_ok=True)

    # Preflight: abort early if disk is critically low
    check_disk_space(run_output_dir)
    result_output_path = run_output_dir / sanitize_filename(params.output_path.name)

    run_tracker.start_run(
        person_name=params.person_name,
        date_range=None,
        target_duration_seconds=round(_total_clip_duration(params)),
        memory_type=params.memory_type,
        memory_key=_build_memory_key(params),
    )

    assembly_clips: list = []  # WHY: populated in try, needed in finally for cleanup
    try:
        import time as _time

        _phase_times: dict[str, float] = {}
        _phase_start = _time.monotonic()
        pp = _PipelineProgress(params, len(params.clips))

        # Phase 1: Download and extract clips
        pp.report("download", 0.0, "Downloading clips...")
        run_tracker.start_phase("clip_extraction", len(params.clips))

        video_cache = VideoDownloadCache(
            cache_dir=params.config.cache.video_cache_path,
            max_size_gb=params.config.cache.video_cache_max_size_gb,
            max_age_days=params.config.cache.video_cache_max_age_days,
        )
        video_cache.evict_old()

        assembly_clips = _extract_clips(params, video_cache, run_output_dir)
        run_tracker.complete_phase(items_processed=len(assembly_clips))
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
            from dataclasses import replace

            assembly_clips = anonymize_clips_for_privacy(assembly_clips)
            anon_preset = anonymize_preset_params(params.memory_preset_params)
            params = replace(
                params,
                person_name=anonymize_name(params.person_name),
                memory_preset_params=anon_preset,
            )

        # Phase 2: Assemble (includes title generation + streaming encode)
        _t = _time.monotonic()
        assembly_cb = pp.assembly_callback()
        run_tracker.start_phase("assembly", len(assembly_clips))

        settings = _build_assembly_settings(params, assembly_clips)
        assembler = _create_assembler(settings, run_id, params.config)
        result_path = assembler.assemble_with_titles(
            assembly_clips,
            result_output_path,
            assembly_cb,
            frame_preview_callback=params.frame_preview_callback,
        )
        run_tracker.complete_phase(items_processed=len(assembly_clips))
        _phase_times["assembly"] = _time.monotonic() - _t

        # Phase 3: Music
        _t = _time.monotonic()
        pp.report("music", 0.0, "Generating music...")
        _run_music_phase(params, assembly_clips, result_path, run_output_dir, run_tracker)
        _phase_times["music"] = _time.monotonic() - _t
        pp.report("music", 1.0, "Music ready")

        # Phase 4: Upload (if requested)
        if params.upload_enabled and params.client:
            _report(params, "upload", 0.95, "Uploading to Immich...")
            _upload_to_immich(params.client, result_path, params.upload_album)

        _phase_times["total"] = _time.monotonic() - _phase_start
        _log_phase_timing(_phase_times, len(assembly_clips))

        _report(params, "done", 1.0, "Complete!")
        run_tracker.complete_run(
            output_path=result_path,
            clips_analyzed=len(params.clips),
            clips_selected=len(assembly_clips),
        )

        return result_path

    except GenerationError as e:
        run_tracker.fail_run(str(e))
        raise
    except Exception as e:
        logger.exception("Video generation failed")
        safe_msg = sanitize_error_message(str(e))
        run_tracker.fail_run(safe_msg)
        raise GenerationError(f"Generation failed: {safe_msg}") from e
    finally:
        try:
            _cleanup_temp_clips(assembly_clips)
        except Exception:
            logger.debug("Temp clip cleanup failed", exc_info=True)
        try:
            if not params.debug_preserve_intermediates:
                _cleanup_temp_dirs(run_output_dir)
        except Exception:
            logger.debug("Temp dir cleanup failed", exc_info=True)
        set_current_run_id(None)


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
