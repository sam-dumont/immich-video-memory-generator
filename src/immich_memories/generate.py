"""Standalone video generation orchestrator.

Decoupled from NiceGUI — usable from CLI, scheduler, or UI.
All UI interaction is replaced by a progress callback.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
    TitleScreenSettings,
    TransitionType,
)
from immich_memories.security import sanitize_error_message

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.cache.video_cache import VideoDownloadCache
    from immich_memories.config_loader import Config

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

    # Upload
    upload_enabled: bool = False
    upload_album: str | None = None

    # Clip overrides from review step
    clip_segments: dict[str, tuple[float, float]] = field(default_factory=dict)
    clip_rotations: dict[str, int | None] = field(default_factory=dict)

    # Privacy mode
    privacy_mode: bool = False

    # Progress callback: (phase, progress_fraction, status_message)
    progress_callback: Callable[[str, float, str], None] | None = None


class GenerationError(Exception):
    """Raised when video generation fails."""


def _report(params: GenerationParams, phase: str, progress: float, msg: str) -> None:
    if params.progress_callback:
        params.progress_callback(phase, progress, msg)


def generate_memory(params: GenerationParams) -> Path:
    """Run the full video generation pipeline synchronously.

    Phases:
    1. Download + extract clip segments
    2. Assemble video with transitions and titles
    3. Apply music (if music_path provided)
    4. Upload to Immich (if upload_enabled)

    Returns the path to the final video file.
    """
    from immich_memories.cache.video_cache import VideoDownloadCache
    from immich_memories.security import sanitize_filename
    from immich_memories.tracking import RunTracker, generate_run_id

    if not params.clips:
        raise GenerationError("No clips provided for generation")

    run_id = generate_run_id()
    run_tracker = RunTracker(run_id)

    # Create output directory structure
    dir_slug = params.output_path.stem
    run_output_dir = params.output_path.parent / f"{dir_slug}_{run_id}"
    run_output_dir.mkdir(parents=True, exist_ok=True)
    result_output_path = run_output_dir / sanitize_filename(params.output_path.name)

    run_tracker.start_run(
        person_name=params.person_name,
        date_range=None,
        target_duration_minutes=_total_clip_duration(params) // 60,
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

        assembly_clips = _extract_clips(params, video_cache, run_output_dir)
        run_tracker.complete_phase(items_processed=len(assembly_clips))

        if not assembly_clips:
            raise GenerationError("No clips could be processed")

        # Phase 2: Assemble
        _report(params, "assemble", 0.7, "Assembling final video...")
        run_tracker.start_phase("assembly", len(assembly_clips))

        settings = _build_assembly_settings(params, assembly_clips)
        assembler = _create_assembler(settings, run_id)
        result_path = assembler.assemble_with_titles(assembly_clips, result_output_path)
        run_tracker.complete_phase(items_processed=len(assembly_clips))

        # Phase 3: Music (if provided)
        if params.music_path and params.music_path.exists():
            _report(params, "music", 0.9, "Adding music...")
            run_tracker.start_phase("music", 1)
            _apply_music_file(result_path, params.music_path, params.music_volume)
            run_tracker.complete_phase(items_processed=1)

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
        return result_path

    except GenerationError:
        raise
    except Exception as e:
        logger.exception("Video generation failed")
        safe_msg = sanitize_error_message(str(e))
        raise GenerationError(f"Generation failed: {safe_msg}") from e


def _total_clip_duration(params: GenerationParams) -> int:
    total: float = 0.0
    for clip in params.clips:
        seg = params.clip_segments.get(clip.asset.id)
        if seg:
            total += seg[1] - seg[0]
        else:
            total += clip.duration_seconds or 5.0
    return int(total)


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
            segment_path = extract_clip(video_path, start_time=start_time, end_time=end_time)

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
                    location_name=_clip_location_name(exif),
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

    if not clip_paths or len(clip_paths) != len(trim_points):
        return video_cache.download_or_get(client, clip.asset)

    merged = _try_merge_burst(clip_paths, trim_points, merged_path)
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


def _try_merge_burst(clip_paths: list[Path], trim_points: list, merged_path: Path) -> Path | None:
    """Try to merge burst clips, retrying with filtered clips on failure."""
    import subprocess

    from immich_memories.processing.live_photo_merger import build_merge_command, filter_valid_clips

    cmd = build_merge_command(clip_paths, trim_points, merged_path)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)  # noqa: S603
        if result.returncode == 0 and merged_path.exists():
            return merged_path
    except Exception as e:
        logger.warning(f"Live photo merge error: {e}")

    # Retry with filtered valid clips
    valid_paths, valid_trims = filter_valid_clips(clip_paths, trim_points)
    if not valid_paths or len(valid_paths) >= len(clip_paths):
        return None
    if merged_path.exists():
        merged_path.unlink()
    retry_cmd = build_merge_command(valid_paths, valid_trims, merged_path)
    with contextlib.suppress(Exception):
        retry = subprocess.run(retry_cmd, capture_output=True, text=True, timeout=120)  # noqa: S603
        if retry.returncode == 0 and merged_path.exists():
            return merged_path
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

    resolution_map = {"4K": (3840, 2160), "1080p": (1920, 1080), "720p": (1280, 720)}
    auto_resolution = params.output_resolution is None
    target_resolution = (
        resolution_map.get(params.output_resolution or "") if not auto_resolution else None
    )

    title_screen_settings = _build_title_settings(params, config, assembly_clips)

    return AssemblySettings(
        transition=transition_type,
        transition_duration=params.transition_duration,
        output_crf=params.output_crf or config.output.crf,
        auto_resolution=auto_resolution,
        target_resolution=target_resolution,
        title_screens=title_screen_settings,
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
        trip_locations = _extract_trip_locations(assembly_clips)
        trip_title_text = _generate_trip_title_text(params.memory_preset_params)

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


def _create_assembler(settings: AssemblySettings, run_id: str):
    """Create a VideoAssembler with the given settings."""
    from immich_memories.processing.video_assembler import VideoAssembler

    return VideoAssembler(settings, run_id=run_id)


def _apply_music_file(video_path: Path, music_path: Path, volume: float) -> None:
    """Mix a music file into the assembled video."""
    from immich_memories.audio.mixer import DuckingConfig, MixConfig, mix_audio_with_ducking

    final_path = video_path.with_suffix(".with_music.mp4")
    mix_config = MixConfig(
        ducking=DuckingConfig(
            music_volume_db=-20 + (volume * 20),
        ),
    )
    mix_audio_with_ducking(
        video_path=video_path,
        music_path=music_path,
        output_path=final_path,
        config=mix_config,
    )
    video_path.unlink()
    final_path.rename(video_path)


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


def _clip_location_name(exif) -> str | None:
    if not exif:
        return None
    city = exif.city
    country = exif.country
    if city and country:
        return f"{city}, {country}"
    return country or city


def _extract_trip_locations(assembly_clips: list[AssemblyClip]) -> list[tuple[float, float]]:
    """Extract unique GPS locations from assembly clips for map pins."""
    seen: set[tuple[float, float]] = set()
    locations: list[tuple[float, float]] = []
    for clip in assembly_clips:
        if clip.latitude is not None and clip.longitude is not None:
            key = (round(clip.latitude, 2), round(clip.longitude, 2))
            if key not in seen:
                seen.add(key)
                locations.append((clip.latitude, clip.longitude))
    return locations


def _generate_trip_title_text(preset_params: dict) -> str | None:
    """Generate trip title text from preset params."""
    from immich_memories.titles._trip_titles import generate_trip_title

    location_name = preset_params.get("location_name")
    trip_start = preset_params.get("trip_start")
    trip_end = preset_params.get("trip_end")

    if not location_name or not trip_start or not trip_end:
        return None

    return generate_trip_title(location_name, trip_start, trip_end)


def assets_to_clips(assets: list) -> list:
    """Convert raw Asset objects to VideoClipInfo, filtering short clips."""
    from immich_memories.api.models import VideoClipInfo

    clips = []
    for asset in assets:
        duration = asset.duration_seconds or 0
        if duration < MIN_CLIP_DURATION:
            continue
        clips.append(VideoClipInfo(asset=asset, duration_seconds=duration))
    return clips
