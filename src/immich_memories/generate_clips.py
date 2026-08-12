"""Clip extraction, probing, and cleanup for generate pipeline."""

from __future__ import annotations

import contextlib
import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.generate_privacy import clip_location_name
from immich_memories.processing.assembly_config import AssemblyClip

if TYPE_CHECKING:
    from immich_memories.cache.video_cache import CacheBatch
    from immich_memories.generate import GenerationParams
    from immich_memories.processing.download_coordinator import (
        DownloadCoordinator,
        DownloadResult,
        PrefetchAsset,
    )
    from immich_memories.processing.probe_cache import ProbeCache

logger = logging.getLogger(__name__)

# Minimum clip duration filter (matches UI pipeline)
MIN_CLIP_DURATION = 1.5


def _probe_file_duration(path: Path, *, probe_cache: ProbeCache | None = None) -> float | None:
    """Probe actual file duration via ffprobe. Returns None on failure."""
    from immich_memories.processing.probe_cache import ProbeCache, ProbeError

    with contextlib.suppress(OSError, ProbeError, ValueError):
        duration = (probe_cache or ProbeCache()).get(path).duration_seconds
        return duration or None
    return None


def _prefetch_assets(clips: list) -> list[PrefetchAsset]:
    """Return network-only video targets, including burst components."""
    from immich_memories.api.models import AssetType
    from immich_memories.processing.download_coordinator import DownloadTarget

    targets: list[PrefetchAsset] = []
    for clip in clips:
        if clip.local_path and Path(clip.local_path).exists():
            continue
        if clip.asset.type == AssetType.IMAGE and not clip.asset.live_photo_video_id:
            continue
        if clip.live_burst_video_ids and clip.live_burst_trim_points:
            targets.extend(DownloadTarget(id=video_id) for video_id in clip.live_burst_video_ids)
        else:
            targets.append(clip.asset)
    return targets


def _download_video_path(
    params: GenerationParams,
    clip,
    video_cache: CacheBatch | None,
    output_dir: Path,
    prefetched: dict[str, DownloadResult] | None,
) -> Path | None:
    """Resolve one clip's source without retrying completed burst prefetches."""
    from immich_memories.generate_downloads import download_clip

    if clip.live_burst_video_ids and clip.live_burst_trim_points:
        burst_results = (
            {
                video_id: prefetched[video_id]
                for video_id in clip.live_burst_video_ids
                if video_id in prefetched
            }
            if prefetched is not None
            else None
        )
        return download_clip(
            params.client,
            video_cache,
            clip,
            output_dir,
            prefetched_burst_results=burst_results,
        )

    prefetched_result = prefetched.get(clip.asset.id) if prefetched is not None else None
    if prefetched_result and prefetched_result.path is not None:
        return prefetched_result.path
    if prefetched_result and prefetched_result.error:
        logger.warning("Failed to prefetch %s: %s", clip.asset.id, prefetched_result.error)
        return None
    return download_clip(params.client, video_cache, clip, output_dir)


def _extract_clips(
    params: GenerationParams,
    video_cache: CacheBatch | None,
    output_dir: Path,
    *,
    download_coordinator: DownloadCoordinator | None = None,
    probe_cache: ProbeCache | None = None,
) -> list[AssemblyClip]:
    """Download videos and extract clip segments. Renders IMAGE clips as photo animations."""
    from immich_memories.api.models import AssetType
    from immich_memories.generate_photos import _render_photo_as_clip
    from immich_memories.processing.clips import extract_clip

    def _report(phase: str, progress: float, msg: str) -> None:
        if params.progress_callback:
            params.progress_callback(phase, progress, msg)

    assembly_clips: list[AssemblyClip] = []
    total = len(params.clips)
    prefetched: dict[str, DownloadResult] | None = None
    if download_coordinator is not None:
        prefetched = download_coordinator.prefetch(_prefetch_assets(params.clips))

    for i, clip in enumerate(params.clips):
        progress = (i / total) * 0.7
        clip_name = clip.asset.original_file_name or clip.asset.id[:8]
        _report("extract", progress, f"Downloading: {clip_name}")

        try:
            # IMAGE-type clips from the unified selection pool:
            # - Live photos (has video component) → download video, extract segment
            # - Static photos → render as Ken Burns animation
            if clip.asset.type == AssetType.IMAGE and not clip.asset.live_photo_video_id:
                photo_clip = _render_photo_as_clip(clip, params, output_dir)
                if photo_clip:
                    assembly_clips.append(photo_clip)
                continue

            video_path = _download_video_path(params, clip, video_cache, output_dir, prefetched)
            if not video_path or not video_path.exists():
                logger.warning(f"Failed to download {clip.asset.id}, skipping")
                continue

            start_time, end_time = params.clip_segments.get(
                clip.asset.id, (0.0, clip.duration_seconds or 5.0)
            )

            _report("extract", progress, f"Extracting segment: {clip_name}")
            segment_path = extract_clip(
                video_path, start_time=start_time, end_time=end_time, config=params.config
            )

            # WHY: extract_clip with -c copy can produce files shorter OR longer
            # than requested due to keyframe boundaries. Use min(actual, nominal)
            # so we never claim more duration than the file actually has (prevents
            # frame underruns) but also never more than what was requested
            # (prevents audio starting early).
            nominal_duration = end_time - start_time
            actual_duration = (
                _probe_file_duration(segment_path, probe_cache=probe_cache)
                if probe_cache is not None
                else _probe_file_duration(segment_path)
            )
            duration = (
                min(actual_duration, nominal_duration) if actual_duration else nominal_duration
            )

            exif = clip.asset.exif_info
            assembly_clips.append(
                AssemblyClip(
                    path=segment_path,
                    duration=duration,
                    date=clip.asset.file_created_at.strftime("%Y-%m-%d"),
                    asset_id=clip.asset.id,
                    rotation_override=params.clip_rotations.get(clip.asset.id),
                    llm_emotion=clip.llm_emotion,
                    latitude=exif.latitude if exif else None,
                    longitude=exif.longitude if exif else None,
                    location_name=clip_location_name(exif),
                )
            )
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            logger.warning(f"Failed to process {clip.asset.id}: {e}")
            continue

    return assembly_clips


def _cleanup_temp_clips(assembly_clips: list[AssemblyClip]) -> None:
    for clip in assembly_clips:
        with contextlib.suppress(Exception):
            if clip.path.exists() and "tmp" in str(clip.path).lower():
                clip.path.unlink()


def _cleanup_temp_dirs(output_dir: Path) -> None:
    """Remove intermediate directories created during generation."""
    import shutil

    for subdir in (
        ".title_screens",
        ".intermediates",
        ".live_merges",
        ".assembly_temps",
        ".temporary_downloads",
        "photos",
    ):
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
