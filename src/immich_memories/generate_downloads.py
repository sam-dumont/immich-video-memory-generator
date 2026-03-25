"""Clip downloading and live photo burst merging for the generation pipeline."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.cache.video_cache import VideoDownloadCache

logger = logging.getLogger(__name__)


def download_clip(
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
