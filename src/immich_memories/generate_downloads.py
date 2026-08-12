"""Clip downloading and live photo burst merging for the generation pipeline."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.api.models import Asset, VideoClipInfo
    from immich_memories.cache.video_cache import CacheBatch

logger = logging.getLogger(__name__)


def download_clip(
    client: SyncImmichClient | None,
    video_cache: CacheBatch | None,
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

    if video_cache is None:
        return _download_temporary_asset(client, clip.asset, output_dir)
    return video_cache.download_or_get(client, clip.asset)


def _download_and_merge_burst(
    client: SyncImmichClient,
    video_cache: CacheBatch | None,
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

    temporary_root = output_dir / ".temporary_downloads"
    cache_dir = video_cache.cache_dir if video_cache is not None else temporary_root
    clip_paths = _download_burst_clips(client, cache_dir, burst_ids, batch=video_cache)

    if not clip_paths:
        return _download_fallback(client, video_cache, clip.asset, output_dir)

    # If some downloads failed, filter to the valid subset instead of abandoning
    if len(clip_paths) != len(trim_points):
        clip_paths, trim_points = _align_burst_subset(clip_paths, burst_ids, trim_points)
        if not clip_paths:
            return _download_fallback(client, video_cache, clip.asset, output_dir)

    merged = _try_merge_burst(
        clip_paths,
        trim_points,
        merged_path,
        shutter_timestamps=clip.live_burst_shutter_timestamps,
    )
    return merged or _download_fallback(client, video_cache, clip.asset, output_dir)


def _download_fallback(
    client: SyncImmichClient,
    video_cache: CacheBatch | None,
    asset: Asset,
    output_dir: Path,
) -> Path | None:
    """Download through the persistent batch when enabled, otherwise temporary storage."""
    if video_cache is None:
        return _download_temporary_asset(client, asset, output_dir)
    return video_cache.download_or_get(client, asset)


def _download_temporary_asset(
    client: SyncImmichClient, asset: Asset, output_dir: Path | None
) -> Path | None:
    """Download an uncached video under the run-owned temporary directory."""
    asset_id = asset.live_photo_video_id or asset.id
    original_name = asset.original_file_name or "video.mp4"
    suffix = Path(original_name).suffix or ".mp4"
    if asset.live_photo_video_id:
        suffix = ".MOV"
    if output_dir is None:
        logger.warning("Cannot create a temporary fallback without an output directory")
        return None
    temporary_dir = output_dir / ".temporary_downloads"
    temporary_dir.mkdir(parents=True, exist_ok=True)
    subdir = asset_id[:2] if len(asset_id) >= 2 else "00"
    path = temporary_dir / subdir / f"{asset_id}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        client.download_asset(asset_id, path)
        if path.exists() and path.stat().st_size > 0:
            return path
        path.unlink(missing_ok=True)
    except (OSError, RuntimeError) as exc:
        logger.warning("Failed to download temporary video %s: %s", asset_id, exc)
        path.unlink(missing_ok=True)
    return None


def _download_burst_clips(
    client: SyncImmichClient,
    cache_dir: Path,
    burst_ids: list[str],
    *,
    batch: CacheBatch | None = None,
) -> list[Path]:
    """Download burst videos, preserving the legacy path helper boundary.

    Production supplies ``batch`` so successful writes join its manifest. The
    public test/helper shape remains path-based for existing callers.
    """
    clip_paths: list[Path] = []
    for vid in burst_ids:
        if batch is not None:
            path = batch.download_video_id(client, vid)
            if path is not None:
                clip_paths.append(path)
            continue

        subdir = vid[:2] if len(vid) >= 2 else "00"
        destination = cache_dir / subdir / f"{vid}.MOV"
        if destination.exists() and destination.stat().st_size > 0:
            clip_paths.append(destination)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            client.download_asset(vid, destination)
            if destination.exists() and destination.stat().st_size > 0:
                clip_paths.append(destination)
        except (OSError, RuntimeError) as exc:
            logger.warning("Failed to download burst video %s: %s", vid, exc)
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
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            logger.warning(f"Spectrogram alignment failed, using timestamp trims: {e}")
            audio_trims = None

    cmd = build_merge_command(valid_paths, valid_trims, merged_path, audio_trim_points=audio_trims)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)  # noqa: S603
        if result.returncode == 0 and merged_path.exists():
            return merged_path
        logger.warning(f"Live photo merge failed: {result.stderr[:500]}")
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"Live photo merge error: {e}")

    return None
