"""Photo pipeline — fetches, processes, and renders photos as video clips.

Streams frames directly to FFmpeg via stdin pipe — never holds more than
one frame in memory at a time. Pre-caps the number of photos BEFORE
rendering to avoid wasting time on photos that will be dropped.
"""

from __future__ import annotations

import hashlib
import logging
import operator
import random
import subprocess
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from immich_memories.api.models import Asset
from immich_memories.config_models import PhotoConfig
from immich_memories.photos.animator import prepare_photo_source
from immich_memories.photos.renderer import (
    KenBurnsParams,
    face_aware_pan,
    render_ken_burns_streaming,
)
from immich_memories.photos.scoring import score_photo
from immich_memories.processing.assembly_config import AssemblyClip

logger = logging.getLogger(__name__)


def render_photo_clips(
    assets: list[Asset],
    config: PhotoConfig,
    target_w: int,
    target_h: int,
    work_dir: Path,
    download_fn: Any,
    video_clip_count: int = 0,
) -> list[AssemblyClip]:
    """Convert photo assets to animated video clips for assembly.

    Pre-caps the number of photos based on video_clip_count and max_ratio
    BEFORE rendering, so we never waste memory/time on excess photos.
    Streams frames to FFmpeg — O(1) memory per photo.
    """
    if not assets:
        return []

    # Score all photos (cheap — no I/O)
    scored = [(a, score_photo(a, config)) for a in assets]
    scored.sort(key=operator.itemgetter(1), reverse=True)

    # Pre-cap: only render as many photos as we'll actually use
    max_photos = _compute_max_photos(video_clip_count, config.max_ratio)
    if len(scored) > max_photos:
        logger.info(
            f"Photo pre-cap: {len(scored)} → {max_photos} (top scored, {config.max_ratio:.0%} of {video_clip_count} videos)"
        )
        scored = scored[:max_photos]

    # Render each photo (streaming to FFmpeg, O(1) memory)
    clips: list[AssemblyClip] = []
    for i, (asset, score) in enumerate(scored):
        logger.info(f"Rendering photo {i + 1}/{len(scored)}: {asset.id[:8]}... (score={score:.2f})")
        clip = _render_single_photo(asset, config, target_w, target_h, work_dir, download_fn)
        if clip:
            clips.append(clip)

    logger.info(f"Rendered {len(clips)} photo clips from {len(assets)} photos")
    return clips


def _compute_max_photos(video_count: int, max_ratio: float) -> int:
    """How many photos to render given video count and max photo ratio."""
    if max_ratio >= 1.0:
        return 999
    if video_count == 0:
        return 10  # Sensible limit when there are no videos
    # max_ratio of total: photos / (videos + photos) <= max_ratio
    # photos <= max_ratio * videos / (1 - max_ratio)
    return max(1, int(max_ratio * video_count / (1 - max_ratio)))


def _render_single_photo(
    asset: Asset,
    config: PhotoConfig,
    target_w: int,
    target_h: int,
    work_dir: Path,
    download_fn: Any,
) -> AssemblyClip | None:
    """Download, prepare, render (streaming), and encode a single photo."""
    try:
        # Download from Immich
        ext = Path(asset.original_file_name).suffix if asset.original_file_name else ".jpg"
        raw_path = work_dir / f"{asset.id}{ext}"
        if not raw_path.exists():
            download_fn(asset.id, raw_path)

        # Prepare (HEIC decode, etc.)
        prepared = prepare_photo_source(raw_path, work_dir)

        # Load image
        img = cv2.imread(str(prepared.path), cv2.IMREAD_UNCHANGED)
        if img is None:
            logger.warning(f"Failed to read {prepared.path}")
            return None
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0

        # Face-aware pan target
        face_target = face_aware_pan(asset.people, prepared.width, prepared.height)

        # Reproducible random params from asset ID
        seed = int(hashlib.sha256(asset.id.encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)

        params = KenBurnsParams(
            zoom_start=1.0,
            zoom_end=1.0 + rng.uniform(0.05, 0.12),
            pan_start=(rng.uniform(0.3, 0.7), rng.uniform(0.3, 0.7)),
            pan_end=face_target,
            fps=60,
            duration=config.duration,
        )

        # Stream-render to mp4 (O(1) memory — one frame at a time)
        output_path = work_dir / f"{asset.id}_photo.mp4"
        _stream_render_to_mp4(img, params, output_path, target_w, target_h)

        if not output_path.exists() or output_path.stat().st_size < 100:
            logger.warning(f"Encoding failed for {asset.id}")
            return None

        return AssemblyClip(
            path=output_path,
            duration=config.duration,
            date=asset.file_created_at.isoformat() if asset.file_created_at else None,
            asset_id=asset.id,
            is_photo=True,
            latitude=asset.exif_info.latitude if asset.exif_info else None,
            longitude=asset.exif_info.longitude if asset.exif_info else None,
            location_name=asset.exif_info.city if asset.exif_info else None,
        )

    except Exception as e:
        logger.warning(f"Failed to render photo {asset.id}: {e}")
        return None


def _stream_render_to_mp4(
    img: np.ndarray,
    params: KenBurnsParams,
    output_path: Path,
    target_w: int,
    target_h: int,
) -> None:
    """Render Ken Burns frames and stream directly to FFmpeg.

    Never holds more than 1 frame in memory. Each frame is generated,
    converted to bytes, and written to FFmpeg's stdin pipe immediately.
    """
    proc = subprocess.Popen(
        [
            "ffmpeg",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{target_w}x{target_h}",
            "-r",
            str(params.fps),
            "-i",
            "pipe:0",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-t",
            str(params.duration),
            "-shortest",
            str(output_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Stream frames one at a time via the generator
    assert proc.stdin is not None  # Popen with stdin=PIPE always sets this
    for frame in render_ken_burns_streaming(img, target_w, target_h, params):
        frame_bytes = (np.clip(frame * 255, 0, 255).astype(np.uint8)).tobytes()
        proc.stdin.write(frame_bytes)

    proc.stdin.close()
    proc.wait(timeout=300)
