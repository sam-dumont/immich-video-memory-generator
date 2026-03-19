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

    # Phase 1: Fast metadata scoring (no I/O)
    scored = [(a, score_photo(a, config)) for a in assets]

    # Pre-cap with temporal distribution
    max_photos = _compute_max_photos(video_clip_count, config.max_ratio)
    # Shortlist: take 3x what we need for LLM refinement
    shortlist_size = min(len(scored), max_photos * 3)
    if len(scored) > shortlist_size:
        scored = _select_distributed(scored, shortlist_size)

    # Phase 2: LLM scoring on shortlist (downloads photo, sends to VLM)
    scored = _enhance_with_llm(scored, config, work_dir, download_fn)

    # Final selection: top N after LLM scoring, distributed
    if len(scored) > max_photos:
        scored = _select_distributed(scored, max_photos)
    logger.info(f"Photo selection: {len(assets)} → {len(scored)} (max {max_photos})")

    # Phase 3: Render each selected photo
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


def _select_distributed(
    scored: list[tuple[Asset, float]], max_count: int
) -> list[tuple[Asset, float]]:
    """Select top photos with temporal spread across the date range.

    Divides the date range into equal buckets and picks the best-scored
    photo from each bucket, cycling until max_count is reached.
    """
    if max_count >= len(scored):
        return scored

    # Sort by date for bucketing
    by_date = sorted(scored, key=lambda x: x[0].file_created_at)

    # Divide into max_count buckets
    bucket_size = max(1, len(by_date) // max_count)
    selected: list[tuple[Asset, float]] = []
    seen: set[str] = set()

    for bucket_start in range(0, len(by_date), bucket_size):
        if len(selected) >= max_count:
            break
        bucket = by_date[bucket_start : bucket_start + bucket_size]
        # Pick the highest-scored photo in this bucket
        best = max(bucket, key=operator.itemgetter(1))
        if best[0].id not in seen:
            selected.append(best)
            seen.add(best[0].id)

    return selected


def _enhance_with_llm(
    scored: list[tuple[Asset, float]],
    config: PhotoConfig,
    work_dir: Path,
    download_fn: Any,
) -> list[tuple[Asset, float]]:
    """Check cache first, then LLM-score uncached photos."""

    cache = _get_score_cache()
    asset_ids = [a.id for a, _ in scored]
    cached = cache.get_asset_scores_batch(asset_ids) if cache else {}

    cache_hits = 0
    enhanced: list[tuple[Asset, float]] = []
    for asset, meta_score in scored:
        # Cache hit — use stored score
        if asset.id in cached:
            enhanced.append((asset, cached[asset.id]["combined_score"]))
            cache_hits += 1
            continue

        # Cache miss — download + LLM
        llm_score = _llm_score_photo(asset, meta_score, config, work_dir, download_fn)
        enhanced.append((asset, llm_score))

        # Store in cache
        if cache:
            cache.save_asset_score(
                asset_id=asset.id,
                asset_type="photo",
                metadata_score=meta_score,
                combined_score=llm_score,
            )

    if cache_hits:
        logger.info(f"Photo score cache: {cache_hits} hits, {len(scored) - cache_hits} misses")

    return enhanced


def _llm_score_photo(
    asset: Asset, meta_score: float, config: PhotoConfig, work_dir: Path, download_fn: Any
) -> float:
    """Download, prepare, and LLM-score a single photo."""
    from immich_memories.photos.scoring import score_photo_with_llm

    ext = Path(asset.original_file_name).suffix if asset.original_file_name else ".jpg"
    raw_path = work_dir / f"{asset.id}{ext}"
    if not raw_path.exists():
        try:
            download_fn(asset.id, raw_path)
        except Exception:
            return meta_score

    try:
        from immich_memories.photos.animator import prepare_photo_source

        prepared = prepare_photo_source(raw_path, work_dir)
        return score_photo_with_llm(prepared.path, meta_score, config)
    except Exception:
        return meta_score


def _get_score_cache():
    """Get the asset score cache for score lookups."""
    try:
        from immich_memories.cache.asset_score_cache import AssetScoreCache
        from immich_memories.config_loader import get_config

        config = get_config()
        return AssetScoreCache(db_path=config.cache.database_path)
    except Exception:
        return None


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

    Encodes as HEVC 10-bit HLG/BT.2020 to match iPhone video clips.
    WHY: If photo clips are H.264 SDR and videos are HEVC HLG, the
    assembly pipeline applies SDR→HDR zscale which produces red tint.
    Matching the color space avoids any conversion.

    Streams one frame at a time — O(1) memory.
    """
    encoder_args = _get_photo_encoder_args()

    # WHY: The rendered frames are sRGB (gamma 2.2). The video pipeline
    # outputs HEVC HLG. We must ACTUALLY convert the pixel values from
    # sRGB to HLG transfer function — not just tag the metadata.
    # setparams alone would lie about the transfer, causing red tint.
    # WHY: npl=203 sets SDR white to 203 nits (standard reference white)
    # which matches the brightness iPhone HLG videos display at.
    # Without it, photos appear dimmer than videos in the assembly.
    vf = (
        "zscale=transfer=arib-std-b67:transferin=iec61966-2-1"
        ":primaries=bt2020:primariesin=bt709"
        ":matrix=bt2020nc:matrixin=bt709"
        ":npl=203"
        ",format=yuv420p10le"
    )

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
            "-vf",
            vf,
            *encoder_args,
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

    assert proc.stdin is not None
    for frame in render_ken_burns_streaming(img, target_w, target_h, params):
        frame_bytes = (np.clip(frame * 255, 0, 255).astype(np.uint8)).tobytes()
        proc.stdin.write(frame_bytes)

    proc.stdin.close()
    proc.wait(timeout=300)


def _get_photo_encoder_args() -> list[str]:
    """Encoder args matching the video pipeline's HDR output (HEVC HLG BT.2020).

    WHY: iPhone videos are HEVC HLG 10-bit BT.2020. Photo clips must
    match to avoid the assembly pipeline's SDR→HDR zscale conversion
    which produces red tint on the photos.
    """
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=5
        )
        has_vt = "hevc_videotoolbox" in result.stdout
    except Exception:
        has_vt = False

    # WHY: zscale in the filter already converts to yuv420p10le with
    # HLG/BT.2020 color. Encoder just needs to preserve the metadata.
    if has_vt:
        return [
            "-c:v",
            "hevc_videotoolbox",
            "-profile:v",
            "main10",
            "-tag:v",
            "hvc1",
            "-b:v",
            "10M",
            "-colorspace",
            "bt2020nc",
            "-color_primaries",
            "bt2020",
            "-color_trc",
            "arib-std-b67",
        ]

    return [
        "-c:v",
        "libx265",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-tag:v",
        "hvc1",
        "-colorspace",
        "bt2020nc",
        "-color_primaries",
        "bt2020",
        "-color_trc",
        "arib-std-b67",
        "-x265-params",
        "hdr-opt=1:repeat-headers=1:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc",
    ]
