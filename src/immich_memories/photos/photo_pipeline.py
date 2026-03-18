"""Photo pipeline — fetches, processes, and renders photos as video clips.

This is the glue between the Immich API, photo renderer, and assembly
pipeline. It fetches IMAGE assets, groups them, scores them, renders
each as an animated video clip, and returns AssemblyClips ready for
the video assembly pipeline.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from immich_memories.api.models import Asset
from immich_memories.config_models import PhotoConfig
from immich_memories.photos.animator import prepare_photo_source
from immich_memories.photos.grouper import PhotoGrouper
from immich_memories.photos.renderer import KenBurnsParams, face_aware_pan, render_ken_burns
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
) -> list[AssemblyClip]:
    """Convert photo assets to animated video clips for assembly.

    Args:
        assets: IMAGE assets from Immich (already filtered, no live photos).
        config: PhotoConfig with animation settings.
        target_w: Target output width (e.g., 1920).
        target_h: Target output height (e.g., 1080).
        work_dir: Directory for intermediate files.
        download_fn: Function(asset_id, output_path) to download from Immich.

    Returns:
        List of AssemblyClip with is_photo=True, ready for assembly.
    """
    if not assets:
        return []

    # Group photos by temporal proximity
    grouper = PhotoGrouper(config)
    groups = grouper.group(assets)

    # Score each photo
    asset_scores: dict[str, float] = {}
    for asset in assets:
        asset_scores[asset.id] = score_photo(asset, config)

    # Build asset lookup
    asset_map = {a.id: a for a in assets}

    # Render each group (series → collage in future, singles → Ken Burns)
    all_ids = [aid for group in groups for aid in group.asset_ids]
    clips = _render_photo_list(
        all_ids, asset_map, config, target_w, target_h, work_dir, download_fn, asset_scores
    )

    logger.info(f"Rendered {len(clips)} photo clips from {len(assets)} photos")
    return clips


def _render_photo_list(
    asset_ids: list[str],
    asset_map: dict[str, Asset],
    config: PhotoConfig,
    target_w: int,
    target_h: int,
    work_dir: Path,
    download_fn: Any,
    scores: dict[str, float],
) -> list[AssemblyClip]:
    """Render a list of photos as individual Ken Burns clips."""
    clips: list[AssemblyClip] = []
    for aid in asset_ids:
        clip = _render_single_photo(
            asset_map[aid], config, target_w, target_h, work_dir, download_fn, scores[aid]
        )
        if clip:
            clips.append(clip)
    return clips


def _render_single_photo(
    asset: Asset,
    config: PhotoConfig,
    target_w: int,
    target_h: int,
    work_dir: Path,
    download_fn: Any,
    score: float,
) -> AssemblyClip | None:
    """Download, prepare, render, and encode a single photo."""
    try:
        # Download from Immich
        ext = Path(asset.original_file_name).suffix if asset.original_file_name else ".jpg"
        raw_path = work_dir / f"{asset.id}{ext}"
        if not raw_path.exists():
            download_fn(asset.id, raw_path)

        # Prepare (HEIC decode, etc.)
        prepared = prepare_photo_source(raw_path, work_dir)

        # Load as numpy array for rendering
        import cv2

        img = cv2.imread(str(prepared.path), cv2.IMREAD_UNCHANGED)
        if img is None:
            logger.warning(f"Failed to read {prepared.path}")
            return None
        # BGR → RGB, normalize to float32
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0

        # Compute face-aware pan target
        face_target = face_aware_pan(asset.people, prepared.width, prepared.height)

        # Build Ken Burns params with face awareness
        seed = int(hashlib.sha256(asset.id.encode()).hexdigest()[:8], 16)
        rng = __import__("random").Random(seed)

        pan_start = (rng.uniform(0.3, 0.7), rng.uniform(0.3, 0.7))
        params = KenBurnsParams(
            zoom_start=1.0,
            zoom_end=1.0 + rng.uniform(0.05, 0.12),
            pan_start=pan_start,
            pan_end=face_target,
            fps=60,
            duration=config.duration,
        )

        # Render frames
        frames = render_ken_burns(img, target_w, target_h, params)

        # Encode to mp4
        output_path = work_dir / f"{asset.id}_photo.mp4"
        _encode_frames_to_mp4(frames, output_path, target_w, target_h, fps=60)

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


def _encode_frames_to_mp4(
    frames: list[np.ndarray],
    output_path: Path,
    tw: int,
    th: int,
    fps: int = 60,
) -> None:
    """Encode rendered frames to H.264 mp4 with silent audio."""
    raw = b"".join((np.clip(f * 255, 0, 255).astype(np.uint8)).tobytes() for f in frames)

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{tw}x{th}",
            "-r",
            str(fps),
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
            str(len(frames) / fps),
            "-shortest",
            str(output_path),
        ],
        input=raw,
        capture_output=True,
        timeout=300,
    )
