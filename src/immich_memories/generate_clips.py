"""Clip extraction, probing, and cleanup for generate pipeline."""

from __future__ import annotations

import contextlib
import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, cast

from immich_memories.generate_privacy import clip_location_name
from immich_memories.processing.assembly_config import AssemblyClip
from immich_memories.processing.download_coordinator import DownloadCoordinator, DownloadResult

if TYPE_CHECKING:
    from immich_memories.api.models import Asset, VideoClipInfo
    from immich_memories.cache.video_cache import CacheBatch, VideoDownloadCache
    from immich_memories.generate import GenerationParams

logger = logging.getLogger(__name__)

# Minimum clip duration filter (matches UI pipeline)
MIN_CLIP_DURATION = 1.5


def _download_client_factory(params: GenerationParams):
    """Build isolated clients only when the caller supplied a real sync client."""
    from immich_memories.api.immich import SyncImmichClient

    if not isinstance(params.client, SyncImmichClient):
        return None

    base_url = params.config.immich.url
    api_key = params.config.immich.api_key
    api_version = params.config.immich.api_version
    timeout = params.client.timeout

    def factory() -> SyncImmichClient:
        return SyncImmichClient(
            base_url=base_url,
            api_key=api_key,
            api_version=api_version,
            timeout=timeout,
        )

    return factory


def _probe_file_duration(path: Path) -> float | None:
    """Probe actual file duration via ffprobe. Returns None on failure."""
    import subprocess

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired):
        pass
    return None


def _prefetch_remote_assets(params: GenerationParams) -> list[Asset]:
    """Return video-only download targets, including live-burst components."""
    from immich_memories.api.models import AssetType

    remote_video_assets: list[Asset] = []
    for clip in params.clips:
        if (clip.asset.type == AssetType.IMAGE and not clip.asset.live_photo_video_id) or (
            clip.local_path and Path(clip.local_path).exists()
        ):
            continue
        if clip.live_burst_video_ids:
            remote_video_assets.extend(
                clip.asset.model_copy(
                    update={
                        "id": burst_id,
                        "live_photo_video_id": None,
                        "original_file_name": f"{burst_id}.MOV",
                    }
                )
                for burst_id in clip.live_burst_video_ids
            )
        else:
            remote_video_assets.append(clip.asset)
    return remote_video_assets


def _prefetch_downloads(
    params: GenerationParams, video_cache: VideoDownloadCache
) -> dict[str, DownloadResult]:
    """Prefetch remote videos when the caller owns a clonable sync client."""
    client_factory = _download_client_factory(params)
    if client_factory is None:
        return {}
    remote_video_assets = _prefetch_remote_assets(params)
    if not remote_video_assets:
        return {}
    return DownloadCoordinator(
        client_factory=client_factory,
        cache_batch=cast("CacheBatch", video_cache),
        max_workers=params.config.analysis.download_workers,
    ).prefetch(remote_video_assets)


def _download_video_path(
    params: GenerationParams,
    video_cache: VideoDownloadCache,
    clip: VideoClipInfo,
    output_dir: Path,
    prefetched: dict[str, DownloadResult],
) -> Path | None:
    """Resolve a clip source, preserving burst prefetch reuse."""
    from immich_memories.generate_downloads import download_clip

    if clip.live_burst_video_ids:
        prefetched_burst_paths = {
            burst_id: prefetched[burst_id].path
            for burst_id in clip.live_burst_video_ids
            if burst_id in prefetched
        }
        return download_clip(
            params.client,
            video_cache,
            clip,
            output_dir,
            prefetched_burst_paths=prefetched_burst_paths or None,
        )

    prefetched_result = prefetched.get(clip.asset.id)
    if prefetched_result is not None:
        return prefetched_result.path
    return download_clip(params.client, video_cache, clip, output_dir)


def _extract_clips(
    params: GenerationParams,
    video_cache: VideoDownloadCache,
    output_dir: Path,
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
    prefetched = _prefetch_downloads(params, video_cache)

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

            video_path = _download_video_path(params, video_cache, clip, output_dir, prefetched)
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
            actual_duration = _probe_file_duration(segment_path)
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
