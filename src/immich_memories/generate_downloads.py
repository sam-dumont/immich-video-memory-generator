"""Clip downloading and live photo burst merging for the generation pipeline."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

from immich_memories.processing.probe_cache import ProbeError

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.cache.video_cache import CacheBatch, VideoDownloadCache
    from immich_memories.processing.probe_cache import ProbeCache

logger = logging.getLogger(__name__)


def download_clip(
    client: SyncImmichClient | None,
    video_cache: VideoDownloadCache | CacheBatch,
    clip: VideoClipInfo,
    output_dir: Path,
    *,
    prefetched_path: Path | None = None,
    prefetched_burst_paths: dict[str, Path | None] | None = None,
    probe_cache: ProbeCache | None = None,
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
        if not hasattr(video_cache, "record_path"):
            raise RuntimeError("Live-burst downloads require an active cache batch")
        return _download_and_merge_burst(
            client,
            cast("CacheBatch", video_cache),
            clip,
            output_dir,
            prefetched_paths=prefetched_burst_paths,
            probe_cache=probe_cache,
        )

    if prefetched_path is not None:
        return prefetched_path
    return video_cache.download_or_get(client, clip.asset)


def _download_and_merge_burst(
    client: SyncImmichClient,
    video_cache: CacheBatch,
    clip: VideoClipInfo,
    output_dir: Path,
    *,
    prefetched_paths: dict[str, Path | None] | None = None,
    probe_cache: ProbeCache | None = None,
) -> Path | None:
    """Download live photo burst videos and merge into one file."""
    burst_ids = clip.live_burst_video_ids or []
    trim_points = clip.live_burst_trim_points or []

    merge_dir = output_dir / ".live_merges"
    merge_dir.mkdir(parents=True, exist_ok=True)
    merged_path = merge_dir / f"{clip.asset.id}_merged.mp4"
    if merged_path.exists() and merged_path.stat().st_size > 1000:
        return merged_path

    clip_paths = _download_burst_clips(
        client, video_cache, burst_ids, prefetched_paths=prefetched_paths
    )

    if not clip_paths:
        return (
            None
            if prefetched_paths is not None
            else video_cache.download_or_get(client, clip.asset)
        )

    # If some downloads failed, filter to the valid subset instead of abandoning
    if len(clip_paths) != len(trim_points):
        clip_paths, trim_points = _align_burst_subset(clip_paths, burst_ids, trim_points)
        if not clip_paths:
            return (
                None
                if prefetched_paths is not None
                else video_cache.download_or_get(client, clip.asset)
            )

    merged = _try_merge_burst(
        clip_paths,
        trim_points,
        merged_path,
        shutter_timestamps=clip.live_burst_shutter_timestamps,
        probe_cache=probe_cache,
    )
    if merged is not None or prefetched_paths is not None:
        return merged
    return video_cache.download_or_get(client, clip.asset)


def _download_burst_clips(
    client: SyncImmichClient,
    cache_batch: CacheBatch,
    burst_ids: list[str],
    *,
    prefetched_paths: dict[str, Path | None] | None = None,
) -> list[Path]:
    """Download burst components and register every cache hit/miss with the batch."""
    clip_paths: list[Path] = []
    for vid in burst_ids:
        was_prefetched, prefetched = _prefetched_burst_clip(cache_batch, vid, prefetched_paths)
        if was_prefetched:
            if prefetched is not None:
                clip_paths.append(prefetched)
            continue
        path = _cached_or_download_burst_clip(client, cache_batch, vid)
        if path is not None:
            clip_paths.append(path)
    return clip_paths


def _prefetched_burst_clip(
    cache_batch: CacheBatch,
    video_id: str,
    prefetched_paths: dict[str, Path | None] | None,
) -> tuple[bool, Path | None]:
    """Return whether prefetch handled the ID and its usable path, if any."""
    if prefetched_paths is None or video_id not in prefetched_paths:
        return False, None
    prefetched_path = prefetched_paths[video_id]
    if prefetched_path is not None and prefetched_path.exists():
        return True, cache_batch.record_path(prefetched_path)
    subdir = video_id[:2] if len(video_id) >= 2 else "00"
    dest = cache_batch.cache_dir / subdir / f"{video_id}.MOV"
    dest.parent.mkdir(parents=True, exist_ok=True)
    cache_batch.record_absence(dest)
    return True, None


def _cached_or_download_burst_clip(
    client: SyncImmichClient,
    cache_batch: CacheBatch,
    video_id: str,
) -> Path | None:
    """Return the reusable/downloaded burst file, logging failed downloads."""
    subdir = video_id[:2] if len(video_id) >= 2 else "00"
    dest = cache_batch.cache_dir / subdir / f"{video_id}.MOV"
    if dest.exists() and dest.stat().st_size > 0:
        return cache_batch.record_path(dest)
    path = cache_batch.download_video_id(client, video_id)
    if path is None:
        logger.warning("Live-photo burst component download failed for asset %s", video_id)
    return path


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
    probe_cache: ProbeCache | None = None,
) -> Path | None:
    """Try to merge burst clips with spectrogram-aligned audio/video.

    If shutter_timestamps is provided, uses spectrogram cross-correlation
    for sample-accurate alignment. Otherwise falls back to timestamp-based
    trim points.
    """
    from immich_memories.processing.live_photo_merger import (
        align_clips_spectrogram,
        build_merge_command,
        filter_valid_clips,
        probe_clip_has_audio,
    )

    # Pre-validate: filter out clips with no valid video stream
    valid_paths, valid_trims = filter_valid_clips(clip_paths, trim_points, probe_cache=probe_cache)
    if not valid_paths:
        return None

    audio_trims = None
    has_audio = probe_clip_has_audio(valid_paths[0], probe_cache=probe_cache)
    if not has_audio:
        logger.info("Burst clips have no audio — skipping spectrogram alignment")
    if has_audio and shutter_timestamps and len(valid_paths) > 1:
        aligned = _spectrogram_aligned_trims(
            valid_paths, shutter_timestamps, probe_cache, align_clips_spectrogram
        )
        if aligned is not None:
            valid_trims, audio_trims, probe_cache = aligned

    cmd = build_merge_command(
        valid_paths,
        valid_trims,
        merged_path,
        audio_trim_points=audio_trims,
        probe_cache=probe_cache,
    )
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)  # noqa: S603
        if result.returncode == 0 and merged_path.exists():
            if probe_cache is not None:
                probe_cache.invalidate(merged_path)
            return merged_path
        logger.warning(f"Live photo merge failed: {result.stderr[:500]}")
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"Live photo merge error: {e}")

    return None


def _spectrogram_aligned_trims(
    paths: list[Path],
    shutter_timestamps: list[float],
    probe_cache: ProbeCache | None,
    align_clips_spectrogram: Callable[
        [list[Path], list[float], list[float]],
        tuple[list[tuple[float, float]], list[tuple[float, float]]],
    ],
) -> tuple[list[tuple[float, float]], list[tuple[float, float]], ProbeCache] | None:
    """Return frame/audio trims, retaining timestamp trims when probing fails."""
    from immich_memories.processing.probe_cache import ProbeCache

    cache = probe_cache or ProbeCache()
    try:
        durations = [cache.get(path).duration_seconds for path in paths]
        video_trims, audio_trims = align_clips_spectrogram(
            paths, shutter_timestamps[: len(paths)], durations
        )
    except (OSError, subprocess.SubprocessError, ValueError, ProbeError) as error:
        logger.warning("Spectrogram alignment failed, using timestamp trims: %s", error)
        return None
    return video_trims, audio_trims, cache
