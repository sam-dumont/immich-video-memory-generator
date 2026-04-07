"""Clip extraction, probing, and cleanup for generate pipeline."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.generate_privacy import clip_location_name
from immich_memories.processing.assembly_config import AssemblyClip

if TYPE_CHECKING:
    from immich_memories.cache.video_cache import VideoDownloadCache
    from immich_memories.generate import GenerationParams

logger = logging.getLogger(__name__)

# Minimum clip duration filter (matches UI pipeline)
MIN_CLIP_DURATION = 1.5


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

            from immich_memories.generate_downloads import download_clip

            video_path = download_clip(params.client, video_cache, clip, output_dir)
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
        except Exception as e:
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
