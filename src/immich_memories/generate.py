"""Standalone video generation orchestrator.

Decoupled from NiceGUI — usable from CLI, scheduler, or UI.
All UI interaction is replaced by a progress callback.
"""

from __future__ import annotations

import asyncio
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

        # Phase 1b: Render photo clips (if enabled)
        if params.include_photos and params.photo_assets:
            _report(params, "photos", 0.5, "Rendering photo animations...")
            photo_clips = _render_photos(params, run_output_dir, len(assembly_clips))
            assembly_clips = _merge_by_date(assembly_clips, photo_clips)

        if not assembly_clips:
            raise GenerationError("No clips could be processed")

        # Phase 2: Assemble
        _report(params, "assemble", 0.7, "Assembling final video...")
        run_tracker.start_phase("assembly", len(assembly_clips))

        settings = _build_assembly_settings(params, assembly_clips)
        assembler = _create_assembler(settings, run_id)
        result_path = assembler.assemble_with_titles(assembly_clips, result_output_path)
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


def _render_photos(
    params: GenerationParams, output_dir: Path, video_clip_count: int
) -> list[AssemblyClip]:
    """Render photo assets as animated video clips for assembly."""
    from immich_memories.photos.photo_pipeline import render_photo_clips

    photo_dir = output_dir / "photos"
    photo_dir.mkdir(exist_ok=True)

    target_res = params.config.output.resolution_tuple
    download_fn = params.client.download_asset if params.client else None
    if not download_fn:
        logger.warning("No Immich client — cannot download photos")
        return []

    return render_photo_clips(
        assets=params.photo_assets or [],
        config=params.config.photos,
        target_w=target_res[0],
        target_h=target_res[1],
        work_dir=photo_dir,
        download_fn=download_fn,
        video_clip_count=video_clip_count,
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
        output_crf=params.output_crf or config.output.crf,
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


def _resolve_music_file(
    params: GenerationParams,
    assembly_clips: list[AssemblyClip],
    run_output_dir: Path,
) -> Path | None:
    """Determine the music file to use: provided path, auto-generated, or None."""
    if params.no_music:
        return None
    if params.music_path and params.music_path.exists():
        return params.music_path
    if not params.music_path and _music_config_available(params.config):
        _report(params, "music", 0.85, "Generating AI music...")
        return _auto_generate_music(params, assembly_clips, run_output_dir)
    return None


def _run_music_phase(
    params: GenerationParams,
    assembly_clips: list[AssemblyClip],
    result_path: Path,
    run_output_dir: Path,
    run_tracker: RunTracker,
) -> None:
    """Resolve and apply music to the assembled video."""
    music_file = _resolve_music_file(params, assembly_clips, run_output_dir)
    if not music_file:
        return
    _report(params, "music", 0.9, "Mixing music...")
    run_tracker.start_phase("music", 1)
    _apply_music_file(result_path, music_file, params.music_volume)
    run_tracker.complete_phase(items_processed=1)


def _music_config_available(config: Config) -> bool:
    """Check if any AI music generation backend is configured and enabled."""
    ace = getattr(config, "ace_step", None)
    mg = getattr(config, "musicgen", None)
    return bool((ace and ace.enabled) or (mg and mg.enabled))


def _auto_generate_music(
    params: GenerationParams,
    assembly_clips: list[AssemblyClip],
    run_output_dir: Path,
) -> Path | None:
    """Auto-generate music using configured AI backends.

    Returns the path to the generated music file, or None if generation
    fails or no backend is available.
    """
    if not _music_config_available(params.config):
        return None

    try:
        from immich_memories.audio.music_generator import generate_music_for_video
        from immich_memories.audio.music_generator_client import MusicGenClientConfig
        from immich_memories.audio.music_generator_models import VideoTimeline

        clip_data: list[tuple[float, str, int | None]] = [
            (
                clip.duration,
                clip.llm_emotion or "calm",
                _clip_month_from_date(clip.date),
            )
            for clip in assembly_clips
        ]

        config = params.config
        timeline = VideoTimeline.from_clips(
            clips=clip_data,
            title_duration=(
                config.title_screens.title_duration if config.title_screens.enabled else 0
            ),
            ending_duration=(
                config.title_screens.ending_duration if config.title_screens.enabled else 0
            ),
        )

        musicgen_config = MusicGenClientConfig.from_app_config(config.musicgen)
        musicgen_config.num_versions = 1  # CLI: just generate one, accept it

        music_dir = run_output_dir / "music"
        music_dir.mkdir(parents=True, exist_ok=True)

        def music_progress(version_idx: int, status: str, progress: float, detail: object) -> None:
            _report(params, "music", 0.85 + (progress / 100.0) * 0.05, f"Music: {status}")

        result = asyncio.run(
            generate_music_for_video(
                timeline=timeline,
                output_dir=music_dir,
                config=musicgen_config,
                progress_callback=music_progress,
                app_config=config,
                memory_type=params.memory_type,
            )
        )

        if result and result.versions:
            result.selected_version = 0
            selected = result.selected
            if selected and selected.full_mix and selected.full_mix.exists():
                logger.info(f"Auto-generated music: {selected.full_mix}")
                return selected.full_mix

    except Exception:
        logger.warning("Auto music generation failed, continuing without music", exc_info=True)

    return None


def _clip_month_from_date(date_str: str | None) -> int | None:
    """Extract month from a YYYY-MM-DD date string."""
    if not date_str:
        return None
    try:
        return int(date_str.split("-")[1])
    except (IndexError, ValueError):
        return None


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
