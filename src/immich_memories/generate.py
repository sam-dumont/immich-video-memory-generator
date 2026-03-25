"""Standalone video generation orchestrator.

Decoupled from NiceGUI — usable from CLI, scheduler, or UI.
All UI interaction is replaced by a progress callback.
"""

from __future__ import annotations

import contextlib
import io
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.generate_music import apply_music_file, resolve_music_file
from immich_memories.generate_privacy import (
    anonymize_clips_for_privacy,
    anonymize_name,
    anonymize_preset_params,
    clip_location_name,
    extract_trip_locations,
    generate_trip_title_text,
)
from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
    TitleScreenSettings,
    TransitionType,
)
from immich_memories.processing.clip_validation import validate_clips
from immich_memories.security import sanitize_error_message

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.cache.video_cache import VideoDownloadCache
    from immich_memories.config_loader import Config
    from immich_memories.tracking import RunTracker

logger = logging.getLogger(__name__)

# Minimum clip duration filter (matches UI pipeline)
MIN_CLIP_DURATION = 1.5


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


def _make_assembly_callback(
    params: GenerationParams, clip_count: int
) -> Callable[[float, str], None] | None:
    """Create a 2-arg callback that scales assembly progress into the overall pipeline range.

    The assembly phase occupies the range between extraction and music phases.
    Uses workload-based estimates to calculate phase boundaries.
    """
    if not params.progress_callback:
        return None

    # WHY: Estimate relative time for each phase to allocate proportional progress
    est_download = clip_count * 3.0
    est_assembly = clip_count * 8.0
    est_music = 120.0
    total_est = est_download + est_assembly + est_music

    phase_start = est_download / total_est
    phase_end = (est_download + est_assembly) / total_est

    def assembly_cb(pct: float, msg: str) -> None:
        scaled = phase_start + pct * (phase_end - phase_start)
        _report(params, "assemble", scaled, msg)

    return assembly_cb


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

    try:
        # Phase 1: Download and extract clips
        _report(params, "extract", 0.0, "Starting clip extraction...")
        run_tracker.start_phase("clip_extraction", len(params.clips))

        video_cache = VideoDownloadCache(
            cache_dir=params.config.cache.video_cache_path,
            max_size_gb=params.config.cache.video_cache_max_size_gb,
            max_age_days=params.config.cache.video_cache_max_age_days,
        )
        video_cache.evict_old()

        assembly_clips = _extract_clips(params, video_cache, run_output_dir)
        run_tracker.complete_phase(items_processed=len(assembly_clips))

        # Phase 1b: Unified budget selection + render selected photos
        assembly_clips = _add_photos_if_enabled(assembly_clips, params, run_output_dir)

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

        # Phase 2: Assemble
        assembly_cb = _make_assembly_callback(params, len(assembly_clips))
        if assembly_cb:
            assembly_cb(0.0, "Assembling final video...")
        else:
            _report(params, "assemble", 0.7, "Assembling final video...")
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

        # Phase 3: Music
        _run_music_phase(params, assembly_clips, result_path, run_output_dir, run_tracker)

        # Phase 4: Upload (if requested)
        if params.upload_enabled and params.client:
            _report(params, "upload", 0.95, "Uploading to Immich...")
            _upload_to_immich(params.client, result_path, params.upload_album)

        _report(params, "done", 1.0, "Complete!")
        run_tracker.complete_run(
            output_path=result_path,
            clips_analyzed=len(params.clips),
            clips_selected=len(assembly_clips),
        )

        _cleanup_temp_clips(assembly_clips)
        if not params.debug_preserve_intermediates:
            _cleanup_temp_dirs(run_output_dir)
        return result_path

    except GenerationError:
        raise
    except Exception as e:
        logger.exception("Video generation failed")
        safe_msg = sanitize_error_message(str(e))
        raise GenerationError(f"Generation failed: {safe_msg}") from e
    finally:
        set_current_run_id(None)


def _total_clip_duration(params: GenerationParams) -> int:
    total: float = 0.0
    for clip in params.clips:
        seg = params.clip_segments.get(clip.asset.id)
        if seg:
            total += seg[1] - seg[0]
        else:
            total += clip.duration_seconds or 5.0
    return int(total)


def _add_photos_if_enabled(
    assembly_clips: list[AssemblyClip],
    params: GenerationParams,
    run_output_dir: Path,
) -> list[AssemblyClip]:
    """Add photo clips to assembly if photo support is enabled."""
    if not params.include_photos or not params.photo_assets:
        return assembly_clips

    _report(params, "photos", 0.5, "Selecting and rendering photos...")

    # WHY: always use unified budget to avoid rendering photos that get discarded
    effective_duration = params.target_duration_seconds
    if effective_duration is None:
        effective_duration = sum(c.duration for c in assembly_clips) * 1.25

    video_clips, photo_clips = _apply_unified_budget(
        assembly_clips, params, run_output_dir, target_override=effective_duration
    )

    return _merge_by_date(video_clips, photo_clips)


def _detect_photo_resolution(params: GenerationParams) -> tuple[int, int]:
    """Detect the correct resolution for photo rendering.

    WHY: config.output.resolution_tuple always returns landscape (1920x1080).
    But if the majority of video clips are portrait, the assembly pipeline
    will swap to portrait (1080x1920). Photos must match or they get
    double-blur-backgrounded — once by the renderer, once by the assembler.
    """
    target_w, target_h = params.config.output.resolution_tuple
    portrait_count = sum(1 for c in params.clips if c.height > c.width)
    if portrait_count > len(params.clips) // 2 and target_w > target_h:
        target_w, target_h = target_h, target_w
        logger.info(f"Photos: detected portrait orientation, rendering to {target_w}x{target_h}")
    return target_w, target_h


def _render_photos(
    params: GenerationParams, output_dir: Path, video_clip_count: int
) -> list[AssemblyClip]:
    """Render photo assets as animated video clips for assembly."""
    from immich_memories.photos.photo_pipeline import render_photo_clips

    photo_dir = output_dir / "photos"
    photo_dir.mkdir(exist_ok=True)

    target_w, target_h = _detect_photo_resolution(params)
    download_fn = params.client.download_asset if params.client else None
    thumbnail_fn = params.client.get_asset_thumbnail if params.client else None
    if not download_fn:
        logger.warning("No Immich client — cannot download photos")
        return []

    return render_photo_clips(
        assets=params.photo_assets or [],
        config=params.config.photos,
        target_w=target_w,
        target_h=target_h,
        work_dir=photo_dir,
        download_fn=download_fn,
        video_clip_count=video_clip_count,
        thumbnail_fn=thumbnail_fn,
    )


def _apply_unified_budget(
    assembly_clips: list[AssemblyClip],
    params: GenerationParams,
    output_dir: Path,
    target_override: float | None = None,
) -> tuple[list[AssemblyClip], list[AssemblyClip]]:
    """Apply unified budget: score photos, select within budget, render selected.

    Returns (filtered_video_clips, rendered_photo_clips).
    """
    from immich_memories.analysis.unified_budget import (
        BudgetCandidate,
        estimate_title_overhead,
        select_within_budget,
    )
    from immich_memories.photos.photo_pipeline import render_photo_clips, score_photos

    target = target_override or params.target_duration_seconds
    assert target is not None
    photo_dir = output_dir / "photos"
    photo_dir.mkdir(exist_ok=True)

    download_fn = params.client.download_asset if params.client else None
    thumbnail_fn = params.client.get_asset_thumbnail if params.client else None
    if not download_fn:
        logger.warning("No Immich client — cannot download photos")
        return assembly_clips, []

    # Score photos (no rendering yet)
    scored_photos = score_photos(
        assets=params.photo_assets or [],
        config=params.config.photos,
        video_clip_count=len(assembly_clips),
        work_dir=photo_dir,
        download_fn=download_fn,
        thumbnail_fn=thumbnail_fn,
    )

    # Build budget candidates
    video_candidates = [
        BudgetCandidate(
            asset_id=c.asset_id,
            duration=c.duration,
            score=0.5,  # Videos already selected by SmartPipeline — uniform base
            candidate_type="video",
            date=_parse_clip_date(c.date),
            is_favorite=False,
        )
        for c in assembly_clips
    ]
    photo_candidates = [
        BudgetCandidate(
            asset_id=asset.id,
            duration=params.config.photos.duration,
            score=score,
            candidate_type="photo",
            date=asset.file_created_at,
            is_favorite=asset.is_favorite,
        )
        for asset, score in scored_photos
    ]

    # Estimate title overhead (with crossfade compensation)
    clip_dates = [c.date or "" for c in assembly_clips]
    title_settings = _build_title_settings_for_overhead(params)
    transition_dur = params.transition_duration
    overhead = estimate_title_overhead(
        clip_dates=clip_dates,
        title_settings=title_settings,
        target_duration=target,
        memory_type=params.memory_type,
        num_clips=len(assembly_clips),
        transition_duration=transition_dur,
    )
    content_budget = target - overhead

    logger.info(
        f"Unified budget: target={target:.0f}s, "
        f"overhead={overhead:.1f}s, content_budget={content_budget:.1f}s"
    )

    # Select within budget (min 10% photos, max from config)
    selection = select_within_budget(
        video_candidates,
        photo_candidates,
        content_budget=content_budget,
        max_photo_ratio=params.config.photos.max_ratio,
        min_photo_ratio=0.10,
    )

    # Filter video clips to kept set
    filtered_videos = [c for c in assembly_clips if (c.asset_id) in selection.kept_video_ids]

    # Render only selected photos
    selected_photo_ids = set(selection.selected_photo_ids)
    selected_assets = [asset for asset, _ in scored_photos if asset.id in selected_photo_ids]

    target_w, target_h = _detect_photo_resolution(params)
    photo_clips = render_photo_clips(
        assets=selected_assets,
        config=params.config.photos,
        target_w=target_w,
        target_h=target_h,
        work_dir=photo_dir,
        download_fn=download_fn,
        video_clip_count=len(filtered_videos),
        thumbnail_fn=thumbnail_fn,
    )

    logger.info(
        f"Unified selection: {len(filtered_videos)} videos + "
        f"{len(photo_clips)} photos = {selection.content_duration:.0f}s content"
    )

    return filtered_videos, photo_clips


def _parse_clip_date(date_str: str | None) -> datetime:
    """Parse a date string from AssemblyClip into datetime."""
    from datetime import UTC

    if not date_str:
        return datetime(2000, 1, 1, tzinfo=UTC)
    try:
        return datetime.fromisoformat(date_str).replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return datetime(2000, 1, 1, tzinfo=UTC)


def _build_title_settings_for_overhead(params: GenerationParams):
    """Build minimal TitleScreenSettings for overhead estimation."""
    from immich_memories.processing.assembly_config import TitleScreenSettings

    if not params.config.title_screens.enabled:
        return None
    return TitleScreenSettings(
        enabled=True,
        title_duration=params.config.title_screens.title_duration,
        month_divider_duration=params.config.title_screens.month_divider_duration,
        ending_duration=params.config.title_screens.ending_duration,
        show_month_dividers=params.config.title_screens.show_month_dividers,
        month_divider_threshold=params.config.title_screens.month_divider_threshold,
    )


def _merge_by_date(
    video_clips: list[AssemblyClip], photo_clips: list[AssemblyClip]
) -> list[AssemblyClip]:
    """Interleave video and photo clips by date, videos first for ties."""
    all_clips = video_clips + photo_clips
    all_clips.sort(key=lambda c: c.date or "")
    return all_clips


def _extract_clips(
    params: GenerationParams,
    video_cache: VideoDownloadCache,
    output_dir: Path,
) -> list[AssemblyClip]:
    """Download videos and extract clip segments."""
    from immich_memories.processing.clips import extract_clip

    assembly_clips: list[AssemblyClip] = []
    total = len(params.clips)

    for i, clip in enumerate(params.clips):
        progress = (i / total) * 0.7
        clip_name = clip.asset.original_file_name or clip.asset.id[:8]
        _report(params, "extract", progress, f"Downloading: {clip_name}")

        try:
            video_path = _download_clip(params.client, video_cache, clip, output_dir)
            if not video_path or not video_path.exists():
                logger.warning(f"Failed to download {clip.asset.id}, skipping")
                continue

            start_time, end_time = params.clip_segments.get(
                clip.asset.id, (0.0, clip.duration_seconds or 5.0)
            )

            _report(params, "extract", progress, f"Extracting segment: {clip_name}")
            segment_path = extract_clip(
                video_path, start_time=start_time, end_time=end_time, config=params.config
            )

            exif = clip.asset.exif_info
            assembly_clips.append(
                AssemblyClip(
                    path=segment_path,
                    duration=end_time - start_time,
                    date=clip.asset.file_created_at.strftime("%Y-%m-%d"),
                    asset_id=clip.asset.id,
                    rotation_override=params.clip_rotations.get(clip.asset.id),
                    llm_emotion=clip.llm_emotion,
                    latitude=exif.latitude if exif else None,
                    longitude=exif.longitude if exif else None,
                    location_name=clip_location_name(exif),
                )
            )
        except Exception as e:
            logger.warning(f"Failed to process {clip.asset.id}: {e}")
            continue

    return assembly_clips


def _download_clip(
    client: SyncImmichClient | None,
    video_cache: VideoDownloadCache,
    clip: VideoClipInfo,
    output_dir: Path,
) -> Path | None:
    """Download a single clip, handling live photo bursts.

    If clip.local_path is already set and the file exists, skip downloading.
    """
    # Use pre-downloaded clip if available (e.g., from analysis cache)
    if clip.local_path and Path(clip.local_path).exists():
        return Path(clip.local_path)

    if client is None:
        return None

    if clip.live_burst_video_ids and clip.live_burst_trim_points:
        return _download_and_merge_burst(client, video_cache, clip, output_dir)

    return video_cache.download_or_get(client, clip.asset)


def _download_and_merge_burst(
    client: SyncImmichClient,
    video_cache: VideoDownloadCache,
    clip: VideoClipInfo,
    output_dir: Path,
) -> Path | None:
    """Download live photo burst videos and merge into one file."""
    burst_ids = clip.live_burst_video_ids or []
    trim_points = clip.live_burst_trim_points or []

    merge_dir = output_dir / ".live_merges"
    merge_dir.mkdir(parents=True, exist_ok=True)
    merged_path = merge_dir / f"{clip.asset.id}_merged.mp4"
    if merged_path.exists() and merged_path.stat().st_size > 1000:
        return merged_path

    clip_paths = _download_burst_clips(client, video_cache.cache_dir, burst_ids)

    if not clip_paths:
        return video_cache.download_or_get(client, clip.asset)

    # If some downloads failed, filter to the valid subset instead of abandoning
    if len(clip_paths) != len(trim_points):
        clip_paths, trim_points = _align_burst_subset(clip_paths, burst_ids, trim_points)
        if not clip_paths:
            return video_cache.download_or_get(client, clip.asset)

    merged = _try_merge_burst(
        clip_paths,
        trim_points,
        merged_path,
        shutter_timestamps=clip.live_burst_shutter_timestamps,
    )
    return merged or video_cache.download_or_get(client, clip.asset)


def _download_burst_clips(
    client: SyncImmichClient, cache_dir: Path, burst_ids: list[str]
) -> list[Path]:
    """Download each burst video component and return local paths."""
    clip_paths: list[Path] = []
    for vid in burst_ids:
        subdir = vid[:2] if len(vid) >= 2 else "00"
        dest = cache_dir / subdir / f"{vid}.MOV"
        if dest.exists() and dest.stat().st_size > 0:
            clip_paths.append(dest)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            client.download_asset(vid, dest)
            if dest.exists() and dest.stat().st_size > 0:
                clip_paths.append(dest)
        except Exception:
            logger.warning(f"Failed to download burst video {vid}", exc_info=True)
    return clip_paths


def _align_burst_subset(
    downloaded_paths: list[Path],
    burst_ids: list[str],
    trim_points: list[tuple[float, float]],
) -> tuple[list[Path], list[tuple[float, float]]]:
    """Match downloaded clips back to their trim points by burst ID.

    When some burst downloads fail, we have fewer clip_paths than trim_points.
    Re-align by matching the downloaded filenames (which contain the burst ID)
    to the original burst_ids ordering, keeping only paired entries.
    """
    # Map downloaded filenames (stem = burst ID) back to their paths
    path_by_id = {p.stem: p for p in downloaded_paths}

    aligned_paths: list[Path] = []
    aligned_trims: list[tuple[float, float]] = []
    for bid, trim in zip(burst_ids, trim_points, strict=False):
        if bid in path_by_id:
            aligned_paths.append(path_by_id[bid])
            aligned_trims.append(trim)

    return aligned_paths, aligned_trims


def _try_merge_burst(
    clip_paths: list[Path],
    trim_points: list,
    merged_path: Path,
    shutter_timestamps: list[float] | None = None,
) -> Path | None:
    """Try to merge burst clips with spectrogram-aligned audio/video.

    If shutter_timestamps is provided, uses spectrogram cross-correlation
    for sample-accurate alignment. Otherwise falls back to timestamp-based
    trim points.
    """
    import subprocess

    from immich_memories.processing.live_photo_merger import (
        align_clips_spectrogram,
        build_merge_command,
        filter_valid_clips,
        probe_clip_has_audio,
    )

    # Pre-validate: filter out clips with no valid video stream
    valid_paths, valid_trims = filter_valid_clips(clip_paths, trim_points)
    if not valid_paths:
        return None

    # Spectrogram alignment for sample-accurate audio + frame-accurate video
    audio_trims = None
    has_audio = probe_clip_has_audio(valid_paths[0]) if valid_paths else False
    if not has_audio:
        logger.info("Burst clips have no audio — skipping spectrogram alignment")
    if has_audio and shutter_timestamps and len(valid_paths) > 1:
        try:
            import json

            durations = []
            for p in valid_paths:
                probe = subprocess.run(  # noqa: S603, S607
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_entries",
                        "format=duration",
                        "-of",
                        "json",
                        str(p),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                durations.append(float(json.loads(probe.stdout)["format"]["duration"]))

            video_trims, audio_trims = align_clips_spectrogram(
                valid_paths, shutter_timestamps[: len(valid_paths)], durations
            )
            valid_trims = video_trims  # Use frame-aligned trims for video
        except Exception as e:
            logger.warning(f"Spectrogram alignment failed, using timestamp trims: {e}")
            audio_trims = None

    cmd = build_merge_command(valid_paths, valid_trims, merged_path, audio_trim_points=audio_trims)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)  # noqa: S603
        if result.returncode == 0 and merged_path.exists():
            return merged_path
        logger.warning(f"Live photo merge failed: {result.stderr[:500]}")
    except Exception as e:
        logger.warning(f"Live photo merge error: {e}")

    return None


def _build_assembly_settings(
    params: GenerationParams,
    assembly_clips: list[AssemblyClip],
) -> AssemblySettings:
    """Build AssemblySettings from GenerationParams."""
    config = params.config

    transition_type = {
        "smart": TransitionType.SMART,
        "crossfade": TransitionType.CROSSFADE,
        "cut": TransitionType.CUT,
        "none": TransitionType.NONE,
    }.get(params.transition.lower(), TransitionType.CROSSFADE)

    resolution_map = {"4k": (3840, 2160), "1080p": (1920, 1080), "720p": (1280, 720)}
    auto_resolution = params.output_resolution is None
    target_resolution = (
        resolution_map.get((params.output_resolution or "").lower())
        if not auto_resolution
        else None
    )

    title_screen_settings = _build_title_settings(params, config, assembly_clips)

    # Scale mode: CLI/param > config > default
    effective_scale_mode = params.scale_mode or config.defaults.scale_mode

    # Output format → codec mapping
    _format_to_codec = {"mp4": "h264", "prores": "prores"}
    output_codec: str = (
        _format_to_codec.get(params.output_format.lower(), config.output.codec)
        if params.output_format
        else config.output.codec
    )

    return AssemblySettings(
        transition=transition_type,
        transition_duration=params.transition_duration,
        output_crf=params.output_crf or config.output.effective_crf,
        auto_resolution=auto_resolution,
        target_resolution=target_resolution,
        title_screens=title_screen_settings,
        scale_mode=effective_scale_mode,
        output_codec=output_codec,
        add_date_overlay=params.add_date_overlay,
        debug_preserve_intermediates=params.debug_preserve_intermediates,
        privacy_mode=params.privacy_mode,
    )


def _build_title_settings(
    params: GenerationParams,
    config: Config,
    assembly_clips: list[AssemblyClip],
) -> TitleScreenSettings | None:
    """Build TitleScreenSettings if title screens are enabled in config."""
    if not config.title_screens.enabled:
        return None

    from immich_memories.ui.filename_builder import build_title_person_name, get_divider_mode

    title_person_name = build_title_person_name(
        memory_type=params.memory_type,
        preset_params=params.memory_preset_params,
        person_name=params.person_name,
        use_first_name_only=config.title_screens.use_first_name_only,
    )

    divider_mode = get_divider_mode(
        memory_type=params.memory_type,
        date_start=params.date_start,
        date_end=params.date_end,
    )
    if not config.title_screens.show_month_dividers:
        divider_mode = "none"

    # Trip-specific title settings
    trip_locations = None
    trip_title_text = None
    if params.memory_type == "trip":
        trip_locations = extract_trip_locations(assembly_clips)
        trip_title_text = generate_trip_title_text(params.memory_preset_params)

    settings = TitleScreenSettings(
        enabled=True,
        person_name=title_person_name,
        start_date=params.date_start,
        end_date=params.date_end,
        locale=config.title_screens.locale,
        style_mode=config.title_screens.style_mode,
        title_duration=config.title_screens.title_duration,
        month_divider_duration=config.title_screens.month_divider_duration,
        ending_duration=config.title_screens.ending_duration,
        show_month_dividers=divider_mode == "month",
        divider_mode=divider_mode,
        month_divider_threshold=config.title_screens.month_divider_threshold,
        use_first_name_only=config.title_screens.use_first_name_only,
        memory_type=params.memory_type,
        trip_locations=trip_locations,
        trip_title_text=trip_title_text,
        home_lat=params.memory_preset_params.get("home_lat"),
        home_lon=params.memory_preset_params.get("home_lon"),
    )

    # Apply LLM-generated title overrides
    if params.title:
        settings.title_override = params.title
        settings.subtitle_override = params.subtitle

    return settings


def _create_assembler(settings: AssemblySettings, run_id: str, config: Config):
    """Create a VideoAssembler with the given settings."""
    from immich_memories.processing.video_assembler import VideoAssembler

    return VideoAssembler(
        settings,
        run_id=run_id,
        output_crf=config.output.effective_crf,
        default_transition_duration=config.defaults.transition_duration,
        default_resolution=config.output.resolution_tuple,
        db_path=Path(config.cache.database).expanduser(),
    )


def _run_music_phase(
    params: GenerationParams,
    assembly_clips: list[AssemblyClip],
    result_path: Path,
    run_output_dir: Path,
    run_tracker: RunTracker,
) -> None:
    """Resolve and apply music to the assembled video."""

    def _report_fn(phase: str, progress: float, msg: str) -> None:
        _report(params, phase, progress, msg)

    music_file = resolve_music_file(
        config=params.config,
        music_path=params.music_path,
        no_music=params.no_music,
        assembly_clips=assembly_clips,
        run_output_dir=run_output_dir,
        memory_type=params.memory_type,
        report_fn=_report_fn,
    )
    if not music_file:
        return
    _report(params, "music", 0.9, "Mixing music...")
    run_tracker.start_phase("music", 1)
    apply_music_file(result_path, music_file, params.music_volume)
    run_tracker.complete_phase(items_processed=1)


def _upload_to_immich(
    client: SyncImmichClient,
    video_path: Path,
    album_name: str | None,
) -> dict:
    result = client.upload_memory(video_path=video_path, album_name=album_name)
    logger.info(f"Uploaded to Immich: asset={result.get('asset_id')}, album={album_name}")
    return result


def _cleanup_temp_clips(assembly_clips: list[AssemblyClip]) -> None:
    for clip in assembly_clips:
        with contextlib.suppress(Exception):
            if clip.path.exists() and "tmp" in str(clip.path).lower():
                clip.path.unlink()


def _cleanup_temp_dirs(output_dir: Path) -> None:
    """Remove intermediate directories created during generation."""
    import shutil

    for subdir in (".title_screens", ".intermediates", ".live_merges", ".assembly_temps", "photos"):
        path = output_dir / subdir
        if path.exists():
            with contextlib.suppress(Exception):
                shutil.rmtree(path)


def assets_to_clips(assets: list) -> list:
    """Convert raw Asset objects to VideoClipInfo, filtering short clips."""
    from immich_memories.api.models import VideoClipInfo

    clips = []
    for asset in assets:
        duration = asset.duration_seconds or 0
        if duration < MIN_CLIP_DURATION:
            continue
        clips.append(
            VideoClipInfo(
                asset=asset,
                duration_seconds=duration,
                width=asset.width,
                height=asset.height,
            )
        )
    return clips
