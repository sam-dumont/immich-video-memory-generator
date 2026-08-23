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
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from immich_memories.analysis.unified_budget import (
    BudgetCandidate,
    UnifiedSelection,
    estimate_title_overhead,
    select_within_budget,
)
from immich_memories.api.immich import ImmichAPIError
from immich_memories.api.models import Asset
from immich_memories.config_models import PhotoConfig
from immich_memories.generate_privacy import clip_location_name
from immich_memories.photos.animator import prepare_photo_source
from immich_memories.photos.renderer import (
    KenBurnsParams,
    face_aware_pan,
    render_ken_burns_streaming,
)
from immich_memories.photos.scoring import PhotoLook, score_photo
from immich_memories.processing.assembly_config import AssemblyClip
from immich_memories.processing.ffmpeg_runner import write_frames_to_ffmpeg

logger = logging.getLogger(__name__)


@dataclass
class PhotoSelectionResult:
    """Result of photo scoring + budget selection."""

    scored_photos: list[tuple[Asset, float]]
    selection: UnifiedSelection


def score_photos(
    assets: list[Asset],
    config: PhotoConfig,
    video_clip_count: int,
    work_dir: Path,
    download_fn: Any,
    db_path: Path | None = None,
    app_config: Any = None,
    thumbnail_fn: Any = None,
    provider_circuit: Any = None,
    thumbnail_cache: Any = None,
) -> list[tuple[Asset, float]]:
    """Score photos (metadata + LLM) without rendering.

    Runs Phases 1 (metadata scoring) and 2 (LLM enhancement) only.
    Pre-caps shortlist to avoid excessive LLM calls.
    """
    if not assets:
        return []

    # Photos score below videos so a recap prefers moving footage. With no
    # video in the month there is nothing to defer to, and the penalty only
    # compresses the range the selection has to work with.
    if video_clip_count == 0:
        config = config.model_copy(update={"score_penalty": 0.0})

    # Phase 1: Fast metadata scoring (no I/O). Keep this complete pool so the
    # final optimizer can use unshortlisted photos when preferred media is sparse.
    metadata_scored = [(a, score_photo(a, config)) for a in assets]

    # A held shutter yields near-identical frames seconds apart, and on a real
    # June pool 21% of the photos were one. Collapsing bursts here rather than
    # after scoring means each one dropped is also an LLM call saved.
    metadata_scored = _drop_burst_duplicates(metadata_scored, config, thumbnail_cache)

    # The metadata score alone ties hundreds of photos onto a handful of
    # values; what the pixels say breaks those ties (#489).
    metadata_scored = _apply_frame_quality(metadata_scored, config, thumbnail_cache)

    # Cap only the expensive semantic-scoring shortlist.
    shortlist_size = llm_shortlist_size(video_clip_count, len(metadata_scored), config.max_ratio)
    shortlist = metadata_scored
    if len(shortlist) > shortlist_size:
        shortlist = _select_distributed(shortlist, shortlist_size)
    logger.info(
        "Photo scoring: %d available -> %d shortlisted for LLM (max %d selectable)",
        len(metadata_scored),
        len(shortlist),
        _compute_max_photos(video_clip_count, config.max_ratio),
    )

    # Phase 2: LLM scoring on shortlist (uses thumbnails, not full downloads)
    enhanced, _payloads = _enhance_with_llm(
        shortlist,
        config,
        work_dir,
        download_fn,
        db_path=db_path,
        app_config=app_config,
        thumbnail_fn=thumbnail_fn,
        provider_circuit=provider_circuit,
    )
    enhanced_scores = {asset.id: score for asset, score in enhanced}

    return [
        (asset, enhanced_scores.get(asset.id, metadata_score))
        for asset, metadata_score in metadata_scored
    ]


def _from_the_camera_roll(photo_assets: list[Asset], config: Any) -> list[Asset]:
    """Drop the photos nothing says the library's own camera made.

    Videos are filtered on the same rule before analysis; photos reached
    selection without ever being asked, so a collage forwarded through a
    messaging app walked into a year recap while a doorbell clip beside it was
    turned away. Dropped here rather than later because there is no sense
    paying a VLM to score something that cannot ship.
    """
    from immich_memories.analysis.source_filter import not_shot_here

    analysis = getattr(config, "analysis", None)
    patterns = getattr(analysis, "exclude_filename_patterns", ())
    stills_need_a_camera = getattr(analysis, "exclude_stills_without_camera_exif", False)
    if not patterns and not stills_need_a_camera:
        return photo_assets
    kept = [
        asset
        for asset in photo_assets
        if not not_shot_here(asset, patterns=patterns, stills_need_a_camera=stills_need_a_camera)
    ]
    if len(kept) < len(photo_assets):
        logger.info(
            "Source filter: %d photo(s) from excluded sources", len(photo_assets) - len(kept)
        )
    return kept


def _no_photos_to_choose_between(video_candidates: list[BudgetCandidate]) -> PhotoSelectionResult:
    """No photo competes for the budget, so every video keeps its place.

    The caller filters its videos down to kept_video_ids, so an empty
    selection is not "nothing was worth keeping" — it renders a memory with
    no content at all. Nothing to select between means nothing to drop.
    """
    return PhotoSelectionResult(
        scored_photos=[],
        selection=UnifiedSelection(
            kept_video_ids={candidate.asset_id for candidate in video_candidates}
        ),
    )


def score_and_select_photos(
    photo_assets: list[Asset],
    video_candidates: list[BudgetCandidate],
    config: Any,
    target_duration: float,
    work_dir: Path,
    download_fn: Any,
    thumbnail_fn: Any | None = None,
    title_settings: Any | None = None,
    clip_dates: list[str] | None = None,
    memory_type: str | None = None,
    transition_duration: float = 0.5,
    provider_circuit: Any = None,
    thumbnail_cache: Any = None,
) -> PhotoSelectionResult:
    """Score photos and select within unified budget.

    Extracted from generate.py:_apply_unified_budget() so it can be
    called from both UI (Step 2) and CLI (generation time).
    """
    photo_assets = _from_the_camera_roll(photo_assets, config)
    if not photo_assets:
        return _no_photos_to_choose_between(video_candidates)

    # WHY every argument: score_photos degrades silently without them. No
    # app_config and the VLM never scores a photo; no thumbnail_cache and both
    # burst dedup and the frame-quality re-weighting return early, leaving the
    # metadata lattice that ties hundreds of photos onto a handful of values;
    # no db_path and every LLM score is paid for again next run.
    scored = score_photos(
        assets=photo_assets,
        config=config.photos,
        video_clip_count=len(video_candidates),
        work_dir=work_dir,
        download_fn=download_fn,
        thumbnail_fn=thumbnail_fn,
        provider_circuit=provider_circuit,
        app_config=config,
        db_path=config.cache.database_path,
        thumbnail_cache=thumbnail_cache,
    )

    if not scored:
        return _no_photos_to_choose_between(video_candidates)

    photo_candidates = [
        BudgetCandidate(
            asset_id=asset.id,
            duration=config.photos.duration,
            score=score,
            candidate_type="photo",
            date=asset.file_created_at,
            is_favorite=asset.is_favorite,
        )
        for asset, score in scored
    ]

    overhead = 0.0
    if title_settings is not None:
        overhead = estimate_title_overhead(
            clip_dates=clip_dates or [],
            title_settings=title_settings,
            target_duration=target_duration,
            memory_type=memory_type,
            num_clips=len(video_candidates),
            transition_duration=transition_duration,
        )
    content_budget = target_duration - overhead

    selection = select_within_budget(
        video_candidates,
        photo_candidates,
        content_budget=content_budget,
        max_photo_ratio=config.photos.max_ratio,
        min_photo_ratio=0.10,
    )

    return PhotoSelectionResult(scored_photos=scored, selection=selection)


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
    provider_circuit: Any = None,
) -> list[AssemblyClip]:
    """Convert photo assets to animated video clips for assembly.

    Pre-caps the number of photos based on video_clip_count and max_ratio
    BEFORE rendering, so we never waste memory/time on excess photos.
    Streams frames to FFmpeg — O(1) memory per photo.
    """
    if not assets:
        return []

    scored = score_photos(
        assets,
        config,
        video_clip_count,
        work_dir,
        download_fn,
        db_path=db_path,
        app_config=app_config,
        thumbnail_fn=thumbnail_fn,
        provider_circuit=provider_circuit,
    )
    if not scored:
        return []

    # Final selection: top N after LLM scoring, distributed
    max_photos = _compute_max_photos(video_clip_count, config.max_ratio)
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


# Scoring one photo costs an LLM round trip, so the shortlist is what actually
# determines how long a run takes. A real library produced a 1194-photo
# shortlist to place a few dozen photos, which ran for hours.
LLM_SHORTLIST_CEILING = 200
_LLM_SHORTLIST_HEADROOM = 3


# How much of a photo's score the pixels are allowed to decide. The metadata
# terms keep their meaning — a favorite is still the user telling us directly —
# but they no longer decide the whole ordering on their own.
_QUALITY_SHARE = 0.35
_SHARPNESS_SHARE = 0.6
_CONTRAST_SHARE = 0.2
_EXPOSURE_SHARE = 0.2


def _apply_frame_quality(
    scored: list[tuple[Asset, float]],
    config: PhotoConfig,
    thumbnail_cache: Any,
) -> list[tuple[Asset, float]]:
    """Re-weight scores so a share of each is decided by the image itself.

    Without this the pool arrives at selection in a handful of tie groups and
    "best N" means "first N of the largest group". Measured on four months:
    227-648 photos collapsing onto 5-8 distinct scores, all inside 0.24-0.48.
    """
    if thumbnail_cache is None or len(scored) < 2:
        return scored

    from immich_memories.photos.frame_quality import measure, rank

    measured = []
    for asset, _score in scored:
        data = thumbnail_cache.get(asset.id, "preview")
        measured.append(measure(data) if data else None)

    usable = [(i, m) for i, m in enumerate(measured) if m is not None]
    if len(usable) < 2:
        return scored

    sharp = rank([m.sharpness for _i, m in usable])
    contrast = rank([m.contrast for _i, m in usable])
    exposure = rank([m.exposure for _i, m in usable])
    quality = {
        idx: sharp[n] * _SHARPNESS_SHARE
        + contrast[n] * _CONTRAST_SHARE
        + exposure[n] * _EXPOSURE_SHARE
        for n, (idx, _m) in enumerate(usable)
    }

    # An unmeasurable thumbnail sits mid-pool rather than last: we know nothing
    # about it, which is not the same as knowing it is bad.
    # The metadata score arrives already carrying the photo penalty, so the
    # quality share takes it too — otherwise a third of every photo's score
    # would quietly escape the rule that keeps photos behind videos.
    penalty = 1.0 - config.score_penalty
    rescored = []
    for i, (asset, score) in enumerate(scored):
        share = quality.get(i, 0.5) * penalty
        rescored.append((asset, score * (1.0 - _QUALITY_SHARE) + share * _QUALITY_SHARE))
    logger.info(
        "Frame quality: %d of %d photos measured, %.0f%% of the score",
        len(usable),
        len(scored),
        _QUALITY_SHARE * 100,
    )
    return rescored


def _drop_burst_duplicates(
    scored: list[tuple[Asset, float]],
    config: PhotoConfig,
    thumbnail_cache: Any,
) -> list[tuple[Asset, float]]:
    """Keep the best-scored frame of each burst, when thumbnails are available."""
    if thumbnail_cache is None or len(scored) < 2:
        return scored

    from immich_memories.analysis.duplicate_hashing import compute_thumbnail_hash
    from immich_memories.photos.burst_dedup import PhotoCandidate, drop_burst_duplicates

    candidates = []
    for asset, score in scored:
        data = thumbnail_cache.get(asset.id, "preview")
        digest = None
        if data:
            # WHY: thumbnails come off disk -- a truncated JPEG costs this photo
            # its comparison, not the run.
            try:
                digest = compute_thumbnail_hash(data) or None
            except (OSError, TypeError, ValueError):
                digest = None
        candidates.append(
            PhotoCandidate(
                key=asset.id,
                taken_at=asset.file_created_at,
                thumbnail_hash=digest,
                score=score,
            )
        )

    kept = set(
        drop_burst_duplicates(
            candidates,
            window_seconds=config.burst_window_seconds,
            hash_threshold=config.burst_hash_threshold,
        )
    )
    return [(asset, score) for asset, score in scored if asset.id in kept]


def _compute_max_photos(video_count: int, max_ratio: float) -> int:
    """How many photos to render given video count and max photo ratio."""
    if max_ratio >= 1.0:
        return 999
    if video_count == 0:
        return 10  # Sensible limit when there are no videos
    # max_ratio is the photos' share of the finished timeline:
    #   photos / (videos + photos) <= max_ratio
    # so photos <= max_ratio * videos / (1 - max_ratio). At max_ratio 0.5 that
    # is exactly video_count, which reads as a cap but is really a 1:1 licence
    # to score a photo for every video. Never exceed the video count.
    ratio_bound = int(max_ratio * video_count / (1 - max_ratio))
    return max(1, min(ratio_bound, video_count))


def video_count_for_photo_budget(total_clips: int, live_photo_clips: int) -> int:
    """Real video clips, excluding live-photo clips.

    The photo budget is a ratio against video content. Live-photo clips are
    themselves built from photos, so counting them as videos lets the photo
    budget grow from the content it is supposed to be balanced against -- on a
    real library 778 live photos became 349 clips and tripled the shortlist.
    """
    return max(0, total_clips - live_photo_clips)


def llm_shortlist_size(video_count: int, available: int, max_ratio: float) -> int:
    """How many photos are worth an LLM call.

    Headroom over what can actually be selected, then an absolute ceiling: on a
    large library the relative bound alone still authorises thousands of calls.
    """
    selectable = _compute_max_photos(video_count, max_ratio)
    return min(available, selectable * _LLM_SHORTLIST_HEADROOM, LLM_SHORTLIST_CEILING)


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


# What a cached row can answer depends on the prompt as much as on the model,
# so the cache key carries both. Rows written when a photo could only report
# two numbers cannot describe themselves, and would have held that silence
# forever; bumping this invalidates them once, and never again.
_PHOTO_LOOK_VERSION = "look1"


def _photo_look_version(model: str) -> str:
    """The cache key for a photo look: which model, answering which prompt."""
    return f"{model}#{_PHOTO_LOOK_VERSION}"


def semantic_payloads_for(
    db_path: Path | None,
    asset_ids: list[str],
    model_version: str | None,
) -> dict[str, dict]:
    """What the VLM said about these photos, keyed by asset id.

    Read from the score cache rather than threaded back through scoring: the
    row is written as each photo is scored, so this answers for the ones just
    looked at and for the ones a previous run paid for. Callers hand the
    result to cache_projection.apply_semantic_payload.
    """
    if not db_path or not asset_ids:
        return {}
    cache = _get_score_cache(db_path)
    if cache is None:
        return {}
    rows = _cached_scores(cache, asset_ids, _photo_look_version(model_version or ""))
    return {asset_id: _payload_from_cache(row) for asset_id, row in rows.items()}


def _as_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _as_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _payload_from_cache(row: dict) -> dict:
    """What a cached row can tell the review, in the shape a clip expects."""
    return {
        "description": row.get("llm_description"),
        "category": None,
        "emotion": row.get("llm_emotion"),
        "subjects": None,
        "setting": None,
        "interestingness": row.get("llm_interest"),
        "quality": row.get("llm_quality"),
    }


def _enhance_with_llm(
    scored: list[tuple[Asset, float]],
    config: PhotoConfig,
    work_dir: Path,
    download_fn: Any,
    db_path: Path | None = None,
    app_config: Any = None,
    thumbnail_fn: Any = None,
    provider_circuit: Any = None,
) -> tuple[list[tuple[Asset, float]], dict[str, dict]]:
    """Check cache first, then LLM-score uncached photos.

    Returns the scores and, beside them, what the model said about each photo
    — keyed by asset id, in the shape apply_semantic_payload expects. A photo
    that cannot describe itself is one the holistic review cannot judge.
    """

    if app_config is None or not app_config.content_analysis.enabled or not app_config.llm.model:
        return scored, {}

    cache = _get_score_cache(db_path) if db_path else None
    asset_ids = [a.id for a, _ in scored]
    model_version = _photo_look_version(app_config.llm.model)
    cached = _cached_scores(cache, asset_ids, model_version)

    cache_hits = 0
    enhanced: list[tuple[Asset, float]] = []
    payloads: dict[str, dict] = {}
    for asset, meta_score in scored:
        # Cache hit — use stored score, and the words stored with it
        if asset.id in cached:
            row = cached[asset.id]
            enhanced.append((asset, row["combined_score"]))
            payloads[asset.id] = _payload_from_cache(row)
            cache_hits += 1
            continue

        # Cache miss — download + LLM
        look = _llm_score_photo(
            asset,
            meta_score,
            config,
            work_dir,
            download_fn,
            app_config,
            thumbnail_fn=thumbnail_fn,
            provider_circuit=provider_circuit,
        )
        effective_score = look.score if look is not None else meta_score
        enhanced.append((asset, effective_score))
        if look is not None:
            payloads[asset.id] = look.payload

        # Only successful semantic results belong to the configured model.
        if cache and look is not None and model_version:
            cache.save_asset_score(
                asset_id=asset.id,
                asset_type="photo",
                metadata_score=meta_score,
                combined_score=effective_score,
                llm_interest=_as_float(look.payload.get("interestingness")),
                llm_quality=_as_float(look.payload.get("quality")),
                llm_emotion=_as_text(look.payload.get("emotion")),
                llm_description=_as_text(look.payload.get("description")),
                model_version=model_version,
            )

    if cache_hits:
        logger.info(f"Photo score cache: {cache_hits} hits, {len(scored) - cache_hits} misses")

    return enhanced, payloads


def _llm_score_photo(
    asset: Asset,
    meta_score: float,
    config: PhotoConfig,
    work_dir: Path,
    download_fn: Any,
    app_config: Any,
    thumbnail_fn: Any = None,
    provider_circuit: Any = None,
) -> PhotoLook | None:
    """Look at a photo with the VLM, using a lightweight thumbnail.

    Uses Immich thumbnail API (~100 KB) instead of downloading the full
    HEIC (5-15 MB). Falls back to full download if no thumbnail_fn.
    """
    from immich_memories.photos.scoring import score_photo_with_llm

    if provider_circuit is not None and not provider_circuit.available:
        return None

    thumb_path = work_dir / f"{asset.id}_thumb.jpg"

    # WHY: Thumbnails are ~100 KB vs 5-15 MB for full HEICs. The VLM
    # doesn't need HDR gain maps or 4K resolution to score a photo.
    if thumbnail_fn and not thumb_path.exists():
        try:
            thumb_bytes = thumbnail_fn(asset.id, size="preview")
            thumb_path.write_bytes(thumb_bytes)
        except (ImmichAPIError, OSError, RuntimeError, ValueError):
            thumbnail_fn = None  # Fall back to full download

    if thumb_path.exists():
        try:
            return score_photo_with_llm(
                thumb_path,
                meta_score,
                config,
                app_config,
                provider_circuit=provider_circuit,
            )
        except (OSError, RuntimeError, ValueError):
            return None

    # Fallback: download full file (old behavior)
    ext = Path(asset.original_file_name).suffix if asset.original_file_name else ".jpg"
    raw_path = work_dir / f"{asset.id}{ext}"
    if not raw_path.exists():
        try:
            download_fn(asset.id, raw_path)
        except (ImmichAPIError, OSError, RuntimeError, ValueError):
            return None

    try:
        from immich_memories.photos.animator import prepare_photo_source

        prepared = prepare_photo_source(raw_path, work_dir)
        return score_photo_with_llm(
            prepared.path,
            meta_score,
            config,
            app_config,
            provider_circuit=provider_circuit,
        )
    except (OSError, RuntimeError, ValueError):
        return None


def _cached_scores(cache, asset_ids: list[str], model_version: str | None) -> dict:
    """Previously computed scores, or nothing if the cache cannot answer.

    The cache opens lazily, so an unwritable database survives construction
    and raises on the first read instead — which took photo scoring down with
    it. Losing the cache costs LLM calls, not the run.
    """
    import sqlite3

    if not cache or not model_version:
        return {}
    try:
        return cache.get_asset_scores_batch(asset_ids, model_version=model_version)
    except (OSError, sqlite3.Error) as exc:
        logger.debug("Photo score cache unreadable (%s): rescoring", exc)
        return {}


def _get_score_cache(db_path: Path):
    """Get the asset score cache, or None when it cannot be opened.

    sqlite raises OperationalError rather than OSError for an unwritable or
    missing directory, so that case escaped the guard and took photo scoring
    down with it. A cache that cannot be opened costs repeated LLM calls, not
    a failed run.
    """
    import sqlite3

    try:
        from immich_memories.cache.asset_score_cache import AssetScoreCache

        return AssetScoreCache(db_path=db_path)
    except (ImportError, OSError, sqlite3.Error) as exc:
        logger.debug("Photo score cache unavailable (%s): scores will not persist", exc)
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

        # Prepare (HEIC decode, gain map extraction for HDR).
        # WHY 1.5x (#423): the renderer samples at most output x 1.12 max zoom
        # x 1.26 pan margin = 1.41x, and it holds three float32 copies of
        # whatever it is given. Measured on a 24.5 MP HEIC at 4K: 2.0x paid
        # 0.63 s and 0.32 GB per photo for pixels its own resize discarded.
        prepared = prepare_photo_source(
            raw_path,
            work_dir,
            max_size=(round(target_w * 1.5), round(target_h * 1.5)),
        )

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
        # WHY: peak_nits = 2^headroom * 203 — varies per photo, baked into
        # the 16-bit normalization. zscale npl must match.
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
            location_name=clip_location_name(asset.exif_info),
        )

    except (ImmichAPIError, OSError, subprocess.SubprocessError, ValueError) as e:
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
    from immich_memories.processing.hdr_utilities import check_zscale_available

    has_zscale = check_zscale_available()
    encoder_args = _get_photo_encoder_args() if has_zscale else _get_sdr_encoder_args()

    if gain_map_hdr:
        pix_fmt = "rgb48le"
        if has_zscale:
            vf = (
                f"zscale=t=arib-std-b67:tin=linear"
                f":p=bt2020:pin=bt709"
                f":m=bt2020nc:min=bt709"
                f":npl={peak_nits}"
                f",format=yuv420p10le"
            )
        else:
            # WHY: Without zscale, HDR gain map data can't be properly
            # converted. Fall back to SDR: drop to 8-bit, skip HDR metadata.
            logger.warning("zscale not available — rendering photo as SDR (HDR gain map ignored)")
            pix_fmt = "rgb24"
            vf = "format=yuv420p"
    else:
        pix_fmt = "rgb24"
        if has_zscale:
            vf = (
                "zscale=t=arib-std-b67:tin=iec61966-2-1"
                ":p=bt2020:pin=bt709"
                ":m=bt2020nc:min=bt709"
                ":npl=203"
                ",format=yuv420p10le"
            )
        else:
            # WHY: Without zscale, render as plain SDR. Colors are correct
            # but no HDR metadata — the photo won't match HDR video clips.
            logger.warning("zscale not available — rendering photo as SDR")
            vf = "format=yuv420p"

    def _frames() -> Iterator[bytes]:
        use_16bit = pix_fmt == "rgb48le"
        for frame in render_ken_burns_streaming(img, target_w, target_h, params):
            if use_16bit:
                yield (np.clip(frame * 65535, 0, 65535).astype(np.uint16)).tobytes()
            else:
                yield (np.clip(frame * 255, 0, 255).astype(np.uint8)).tobytes()

    returncode, stderr_text = write_frames_to_ffmpeg(
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
        _frames(),
        wait_timeout=300,
    )

    if returncode != 0:
        raise RuntimeError(f"Photo FFmpeg encoding failed (exit {returncode}): {stderr_text}")


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
    except (OSError, subprocess.SubprocessError, ValueError):
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
            "20M",
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
        "8",
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


def _get_sdr_encoder_args() -> list[str]:
    """SDR encoder args for when zscale is unavailable."""
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]
