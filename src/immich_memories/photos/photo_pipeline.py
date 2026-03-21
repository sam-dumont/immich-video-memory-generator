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
    fps: int = 30,
    db_path: Path | None = None,
    app_config: Any = None,
    thumbnail_fn: Any = None,
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

    # Phase 2: LLM scoring on shortlist (uses thumbnails, not full downloads)
    scored = _enhance_with_llm(
        scored,
        config,
        work_dir,
        download_fn,
        db_path=db_path,
        app_config=app_config,
        thumbnail_fn=thumbnail_fn,
    )

    # Final selection: top N after LLM scoring, distributed
    if len(scored) > max_photos:
        scored = _select_distributed(scored, max_photos)
    logger.info(f"Photo selection: {len(assets)} → {len(scored)} (max {max_photos})")

    # Phase 3: Render each selected photo
    clips: list[AssemblyClip] = []
    for i, (asset, score) in enumerate(scored):
        logger.info(f"Rendering photo {i + 1}/{len(scored)}: {asset.id[:8]}... (score={score:.2f})")
        clip = _render_single_photo(asset, config, target_w, target_h, work_dir, download_fn, fps)
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
    db_path: Path | None = None,
    app_config: Any = None,
    thumbnail_fn: Any = None,
) -> list[tuple[Asset, float]]:
    """Check cache first, then LLM-score uncached photos."""

    cache = _get_score_cache(db_path) if db_path else None
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
        llm_score = _llm_score_photo(
            asset,
            meta_score,
            config,
            work_dir,
            download_fn,
            app_config,
            thumbnail_fn=thumbnail_fn,
        )
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
    asset: Asset,
    meta_score: float,
    config: PhotoConfig,
    work_dir: Path,
    download_fn: Any,
    app_config: Any,
    thumbnail_fn: Any = None,
) -> float:
    """Score a photo with VLM using a lightweight thumbnail.

    Uses Immich thumbnail API (~100 KB) instead of downloading the full
    HEIC (5-15 MB). Falls back to full download if no thumbnail_fn.
    """
    from immich_memories.photos.scoring import score_photo_with_llm

    thumb_path = work_dir / f"{asset.id}_thumb.jpg"

    # WHY: Thumbnails are ~100 KB vs 5-15 MB for full HEICs. The VLM
    # doesn't need HDR gain maps or 4K resolution to score a photo.
    if thumbnail_fn and not thumb_path.exists():
        try:
            thumb_bytes = thumbnail_fn(asset.id, size="preview")
            thumb_path.write_bytes(thumb_bytes)
        except Exception:
            thumbnail_fn = None  # Fall back to full download

    if thumb_path.exists():
        try:
            return score_photo_with_llm(thumb_path, meta_score, config, app_config)
        except Exception:
            return meta_score

    # Fallback: download full file (old behavior)
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
        return score_photo_with_llm(prepared.path, meta_score, config, app_config)
    except Exception:
        return meta_score


def _get_score_cache(db_path: Path):
    """Get the asset score cache for score lookups."""
    try:
        from immich_memories.cache.asset_score_cache import AssetScoreCache

        return AssetScoreCache(db_path=db_path)
    except Exception:
        return None


def _render_single_photo(
    asset: Asset,
    config: PhotoConfig,
    target_w: int,
    target_h: int,
    work_dir: Path,
    download_fn: Any,
    fps: int = 30,
) -> AssemblyClip | None:
    """Download, prepare, render (streaming), and encode a single photo."""
    try:
        # Download from Immich
        ext = Path(asset.original_file_name).suffix if asset.original_file_name else ".jpg"
        raw_path = work_dir / f"{asset.id}{ext}"
        if not raw_path.exists():
            download_fn(asset.id, raw_path)

        # Prepare (HEIC decode, gain map extraction for HDR)
        prepared = prepare_photo_source(raw_path, work_dir)

        # Load image — 16-bit for gain-mapped HDR, 8-bit for SDR
        img = cv2.imread(str(prepared.path), cv2.IMREAD_UNCHANGED)
        if img is None:
            logger.warning(f"Failed to read {prepared.path}")
            return None
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        if img.dtype == np.uint16:
            img = img.astype(np.float32) / 65535.0
        else:
            img = img.astype(np.float32) / 255.0

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
            fps=fps,
            duration=config.duration,
        )

        # Stream-render to mp4 (O(1) memory — one frame at a time)
        output_path = work_dir / f"{asset.id}_photo.mp4"
        # Peak nits comes from gain map normalization:
        # Apple HEIC: ~1000 nits (headroom=2.3, 2^2.3 * 203 ≈ 1000)
        # UltraHDR: 2^hdr_capacity_max * 203 (varies per image)
        # The peak is baked into the 16-bit normalization — npl must match
        peak_nits = getattr(prepared, "peak_nits", 1000) if prepared.has_gain_map else 203
        _stream_render_to_mp4(
            img,
            params,
            output_path,
            target_w,
            target_h,
            gain_map_hdr=prepared.has_gain_map,
            peak_nits=peak_nits,
        )

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
    gain_map_hdr: bool = False,
    peak_nits: int = 203,
) -> None:
    """Render Ken Burns frames and stream directly to FFmpeg.

    Encodes as HEVC 10-bit HLG/BT.2020 to match iPhone video clips.

    For gain-mapped HDR sources (16-bit linear from Apple gain map),
    pipes rgb48le and uses zscale tin=linear. For SDR sources (8-bit sRGB),
    pipes rgb24 and uses zscale tin=iec61966-2-1.

    Streams one frame at a time — O(1) memory.
    """
    encoder_args = _get_photo_encoder_args()

    if gain_map_hdr:
        # Gain-mapped source: 16-bit linear light → HLG
        # npl must match the peak baked into the uint16 normalization
        pix_fmt = "rgb48le"
        vf = (
            f"zscale=t=arib-std-b67:tin=linear"
            f":p=bt2020:pin=bt709"
            f":m=bt2020nc:min=bt709"
            f":npl={peak_nits}:agamma=false"
            f",format=yuv420p10le"
        )
    else:
        # SDR source: 8-bit sRGB → HLG
        pix_fmt = "rgb24"
        vf = (
            "zscale=t=arib-std-b67:tin=iec61966-2-1"
            ":p=bt2020:pin=bt709"
            ":m=bt2020nc:min=bt709"
            ":npl=203:agamma=false"
            ",format=yuv420p10le"
        )

    proc = subprocess.Popen(
        [
            "ffmpeg",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            pix_fmt,
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
        stderr=subprocess.PIPE,
    )

    assert proc.stdin is not None
    for frame in render_ken_burns_streaming(img, target_w, target_h, params):
        if gain_map_hdr:
            frame_bytes = (np.clip(frame * 65535, 0, 65535).astype(np.uint16)).tobytes()
        else:
            frame_bytes = (np.clip(frame * 255, 0, 255).astype(np.uint8)).tobytes()
        proc.stdin.write(frame_bytes)

    proc.stdin.close()
    proc.wait(timeout=300)

    if proc.returncode != 0:
        stderr_text = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        raise RuntimeError(f"Photo FFmpeg encoding failed (exit {proc.returncode}): {stderr_text}")


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
