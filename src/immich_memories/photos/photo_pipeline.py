"""Photo pipeline — fetches, processes, and renders photos as video clips.

Streams frames directly to FFmpeg via stdin pipe — never holds more than
one frame in memory at a time. Pre-caps the number of photos BEFORE
rendering to avoid wasting time on photos that will be dropped.
"""

from __future__ import annotations

import hashlib
import logging
import random
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from immich_memories.analysis.asset_merit import ranking_key
from immich_memories.api.immich import ImmichAPIError
from immich_memories.api.models import Asset
from immich_memories.config_models_render import PhotoConfig
from immich_memories.generate_privacy import clip_location_name
from immich_memories.photos.animator import prepare_photo_source
from immich_memories.photos.renderer import (
    KenBurnsParams,
    face_aware_pan,
    render_ken_burns_streaming,
)
from immich_memories.photos.scoring import _enhance_with_llm, score_photo
from immich_memories.processing.assembly_config import AssemblyClip
from immich_memories.processing.ffmpeg_runner import write_frames_to_ffmpeg

logger = logging.getLogger(__name__)
# How many previews the tie-break may fetch when nothing cached them. The
# photos in contention are what it is for, and a library's worth of previews
# is the cost the LLM shortlist exists to avoid.
_QUALITY_FETCH_BUDGET = 120

# Scoring one photo costs an LLM round trip, so the shortlist is what actually
# determines how long a run takes. A real library produced a 1194-photo
# shortlist to place a few dozen photos, which ran for hours.
LLM_SHORTLIST_CEILING = 200

# Undated photos sort first rather than dropping out of the grouping.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# What counts as one moment when sampling photos for a content look.
_MOMENT_WINDOW_MINUTES = 10.0

# How much of a photo's score the pixels are allowed to decide. The metadata
# terms keep their meaning, but they no longer decide the whole ordering on
# their own.
_QUALITY_SHARE = 0.35
_SHARPNESS_SHARE = 0.6
_CONTRAST_SHARE = 0.2
_EXPOSURE_SHARE = 0.2


def score_photos(
    assets: list[Asset],
    config: PhotoConfig,
    work_dir: Path,
    download_fn: Any,
    db_path: Path | None = None,
    app_config: Any = None,
    thumbnail_fn: Any = None,
    provider_circuit: Any = None,
    thumbnail_cache: Any = None,
    alongside: list[Any] | None = None,
) -> list[tuple[Asset, float]]:
    """Score photos (metadata + LLM) without rendering.

    Runs Phases 1 (metadata scoring) and 2 (LLM enhancement) only.
    Pre-caps shortlist to avoid excessive LLM calls.

    `alongside` is everything else in the same moments — videos, and the Live
    Photos already claimed as motion. They are never candidates here, but a
    contact sheet built without them describes a fragment of a day and then
    judges from it.
    """
    if not assets:
        return []

    # Phase 1: Fast metadata scoring (no I/O). Keep this complete pool so the
    # final optimizer can use unshortlisted photos when preferred media is sparse.
    metadata_scored = [(a, score_photo(a, config)) for a in assets]

    # A held shutter yields near-identical frames seconds apart, and on a real
    # June pool 21% of the photos were one. Collapsing bursts here rather than
    # after scoring means each one dropped is also an LLM call saved.
    metadata_scored = _drop_burst_duplicates(metadata_scored, config, thumbnail_cache)

    # The metadata score alone ties hundreds of photos onto a handful of
    # values; what the pixels say breaks those ties (#489).
    metadata_scored = _apply_frame_quality(
        metadata_scored, thumbnail_cache, thumbnail_fn=thumbnail_fn
    )

    # One look per moment, not a few per shippable slot. Sizing the shortlist
    # from ship-count meant the model only ever saw what metadata had already
    # picked, so content could confirm that ranking and never overturn it.
    read = _read_the_moments(
        metadata_scored, config, app_config, thumbnail_fn, thumbnail_cache, alongside
    )
    moments = (
        read if read is not None else one_photo_per_moment(metadata_scored, _MOMENT_WINDOW_MINUTES)
    )
    shortlist = moments
    if len(shortlist) > LLM_SHORTLIST_CEILING:
        shortlist = _select_distributed(shortlist, LLM_SHORTLIST_CEILING)
    logger.info(
        "Photo scoring: %d available -> %d moments -> %d shortlisted for LLM",
        len(metadata_scored),
        len(moments),
        len(shortlist),
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


def look_at_selected_photos(
    assets: list[Asset],
    *,
    config: Any,
    client: Any,
    provider_circuit: Any = None,
) -> dict[str, tuple[float, dict]]:
    """A VLM look at the handful of photos that reached a cut without one.

    The shortlist is a budget — thirty of nearly two thousand — and selection
    picks from all of them, so most stills in a finished cut have no
    description and the review cannot judge them. Bounded to what shipped and
    cached, so a rerun pays nothing. Returns the blended score beside the
    description: both come out of the same look, and handing back only the
    words let a look change what the review reads but never what selection
    ranked on.
    """
    if not assets:
        return {}
    work_dir = config.cache.cache_path / "photo-looks"
    work_dir.mkdir(parents=True, exist_ok=True)
    scored = [(asset, score_photo(asset, config.photos)) for asset in assets]
    enhanced, payloads = _enhance_with_llm(
        scored,
        config.photos,
        work_dir,
        client.download_asset,
        db_path=config.cache.database_path,
        app_config=config,
        thumbnail_fn=client.get_asset_thumbnail,
        provider_circuit=provider_circuit,
    )
    looked = {asset.id: score for asset, score in enhanced}
    return {
        asset_id: (looked[asset_id], payload)
        for asset_id, payload in payloads.items()
        if asset_id in looked
    }


def _frames_for_quality(
    scored: list[tuple[Asset, float]],
    thumbnail_cache: Any,
    thumbnail_fn: Any,
    budget: int,
) -> list[bytes | None]:
    """The thumbnail behind each photo, fetching where nothing cached one.

    Reading the cache alone was enough on the UI path and empty on the CLI
    one, where the only writers are the video prefetcher and burst dedup — so
    nearly every photo arrived unmeasured and kept the flat share, while the
    log claimed quality decided a third of the score.

    Fetching is bounded and spread across the timeline, the same way the LLM
    shortlist is drawn: what the tie-break is for is the photos in contention,
    and a library's worth of previews is the cost the shortlist exists to
    avoid.
    """
    frames: list[bytes | None] = [
        thumbnail_cache.get(asset.id, "preview") if thumbnail_cache else None
        for asset, _score in scored
    ]
    if thumbnail_fn is None:
        return frames

    missing = [index for index, data in enumerate(frames) if not data]
    if len(missing) > budget:
        chosen = _select_distributed([scored[index] for index in missing], budget)
        wanted = {asset.id for asset, _score in chosen}
        missing = [index for index in missing if scored[index][0].id in wanted]

    fetched = 0
    for index in missing:
        try:
            frames[index] = thumbnail_fn(scored[index][0].id, size="preview")
            fetched += 1
        except (ImmichAPIError, OSError, RuntimeError, ValueError) as exc:  # noqa: PERF203
            logger.debug("No preview for %s: %s", scored[index][0].id, type(exc).__name__)
    if fetched:
        logger.info("Frame quality: fetched %d preview(s) to break the score ties", fetched)
    return frames


def _apply_frame_quality(
    scored: list[tuple[Asset, float]],
    thumbnail_cache: Any,
    thumbnail_fn: Any = None,
    budget: int = _QUALITY_FETCH_BUDGET,
) -> list[tuple[Asset, float]]:
    """Re-weight scores so a share of each is decided by the image itself.

    Without this the pool arrives at selection in a handful of tie groups and
    "best N" means "first N of the largest group". Measured on four months:
    227-648 photos collapsing onto 5-8 distinct scores, all inside 0.24-0.48.
    """
    if (thumbnail_cache is thumbnail_fn is None) or len(scored) < 2:
        return scored

    from immich_memories.photos.frame_quality import measure, rank

    frames = _frames_for_quality(scored, thumbnail_cache, thumbnail_fn, budget)
    measured = [measure(data) if data else None for data in frames]

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
    rescored = []
    for i, (asset, score) in enumerate(scored):
        share = quality.get(i, 0.5)
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
                is_favorite=asset.is_favorite,
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


def one_photo_per_moment(scored: list[tuple], window_minutes: float) -> list[tuple]:
    """One representative per moment, so every moment gets exactly one look.

    The shortlist used to be sized from how many photos the memory could ship,
    which meant the model only ever saw what metadata had already chosen.
    Measured on one month: 295 available, 3 shortlisted. Content could only
    agree with the metadata ranking; a better photo ranked fourth was never
    looked at, so it could not win.

    Sampling by moment means nothing is skipped at the start. Note this bounds
    the looking, not the selecting: score_photos still returns every photo, so
    two members of one moment can still both be selected downstream.

    A favourite represents its moment -- the user already said this one
    mattered. Otherwise the best metadata score stands in until content can say
    better.
    """
    return [
        max(g, key=lambda item: ranking_key(item[0], item[1]))
        for g in moments_of(scored, window_minutes)
    ]


def moments_of(scored: list[tuple], window_minutes: float) -> list[list[tuple]]:
    """The photographs grouped into moments, in time order.

    Named separately from picking a representative because reading a moment
    from contact sheets needs every member of it, not the one photograph that
    stands in for it. Two callers, one definition of what a moment is.
    """
    if not scored:
        return []
    ordered = sorted(scored, key=lambda item: item[0].file_created_at or _EPOCH)
    groups: list[list[tuple]] = []
    for item in ordered:
        when = item[0].file_created_at
        opened = groups[-1][0][0].file_created_at if groups else None
        if (
            opened is not None
            and when is not None
            and (when - opened).total_seconds() <= window_minutes * 60
        ):
            groups[-1].append(item)
            continue
        groups.append([item])
    return groups


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
        best = max(bucket, key=lambda item: ranking_key(item[0], item[1]))
        if best[0].id not in seen:
            selected.append(best)
            seen.add(best[0].id)

    return selected


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
            primaries=getattr(prepared, "primaries", "bt709"),
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


def _photo_filter_chain(
    *, gain_map_hdr: bool, has_zscale: bool, peak_nits: int, primaries: str
) -> tuple[str, str]:
    """The pipe format and zscale chain for one photo clip.

    A gain-mapped source arrives as 16-bit linear at its own peak; everything
    else arrives 8-bit sRGB at 203 nits. Both leave as PQ.
    """
    if not has_zscale:
        # WHY: without zscale neither transfer conversion is possible. Render
        # plain SDR rather than tagging untransformed pixels as HDR.
        logger.warning("zscale not available — rendering photo as SDR")
        return "rgb24", "format=yuv420p"
    if gain_map_hdr:
        return "rgb48le", (
            f"zscale=t=smpte2084:tin=linear"
            f":p=bt2020:pin={primaries}"
            f":m=bt2020nc:min=bt709"
            f":npl={peak_nits}"
            f",format=yuv420p10le"
        )
    # A photograph with no gain map still gets PQ, at SDR reference white.
    return "rgb24", (
        "zscale=t=smpte2084:tin=iec61966-2-1"
        ":p=bt2020:pin=bt709"
        ":m=bt2020nc:min=bt709"
        ":npl=203"
        ",format=yuv420p10le"
    )


def _stream_render_to_mp4(
    img: np.ndarray,
    params: KenBurnsParams,
    output_path: Path,
    target_w: int,
    target_h: int,
    gain_map_hdr: bool = False,
    peak_nits: int = 203,
    primaries: str = "bt709",
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
    # WHY: photo clips are PQ, gain-mapped or not. HLG is relative -- its OETF
    # is sqrt(3L) below 1/12 of peak, so a 12-nit shadow encodes at 0.173 and
    # any link that skips the display OOTF shows it near 200 nits. That washed
    # out every photograph, HDR and SDR alike. PQ names an absolute luminance,
    # so nothing downstream can lift the shadows. Video clips stay HLG, which
    # is what iPhone video is, and the assembler converts between the two.
    transfer = "smpte2084"
    encoder_args = _get_photo_encoder_args(transfer) if has_zscale else _get_sdr_encoder_args()
    pix_fmt, vf = _photo_filter_chain(
        gain_map_hdr=gain_map_hdr, has_zscale=has_zscale, peak_nits=peak_nits, primaries=primaries
    )

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


def _get_photo_encoder_args(transfer: str = "arib-std-b67") -> list[str]:
    """Encoder args for a 10-bit BT.2020 HEVC photo clip.

    WHY: iPhone videos are HEVC HLG 10-bit BT.2020, and a photo clip has to
    reach the assembler already in BT.2020 or the SDR->HDR zscale conversion
    tints it red. The TRANSFER differs by source: a gain-mapped photograph is
    natively PQ and says so, while everything else stays HLG to match video.
    The assembler converts between the two where a cut needs it.
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
            transfer,
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
        transfer,
        "-x265-params",
        f"hdr-opt=1:repeat-headers=1:colorprim=bt2020:transfer={transfer}:colormatrix=bt2020nc",
    ]


def _get_sdr_encoder_args() -> list[str]:
    """SDR encoder args for when zscale is unavailable."""
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]


def _frames_for_reading(
    moment: list[Any], thumbnail_cache: Any, thumbnail_fn: Any
) -> dict[str, Any]:
    """The thumbnails a moment's sheet is tiled from, skipping what will not open."""
    import io

    from PIL import Image

    frames: dict[str, Any] = {}
    for asset in moment:
        data = thumbnail_cache.get(asset.id, "preview") if thumbnail_cache else None
        if data is None and thumbnail_fn is not None:
            try:
                data = thumbnail_fn(asset.id, size="preview")
            except (ImmichAPIError, OSError, RuntimeError, ValueError):
                # A Live Photo's video component has no thumbnail of its own
                # and answers 404. Measured on one day: 34 of 42 "videos" were
                # components. One missing tile must not lose the sheet.
                data = None
        if not data:
            continue
        try:
            frames[asset.id] = Image.open(io.BytesIO(data)).convert("RGB")
        except (OSError, ValueError):
            continue
    return frames


def _read_the_moments(
    metadata_scored: list[tuple],
    config: PhotoConfig,
    app_config: Any,
    thumbnail_fn: Any,
    thumbnail_cache: Any,
    alongside: list[Any] | None = None,
) -> list[tuple] | None:
    """What each moment's contact sheet chose, or None to fall back to sampling.

    Returns None rather than an empty list when it cannot read: no thumbnails
    and no model means no reading, and an empty shortlist would silently ship
    a memory chosen on metadata alone while claiming to have looked.
    """
    if not getattr(config, "read_moments", False) or app_config is None:
        return None
    if thumbnail_cache is thumbnail_fn is None:
        return None

    from immich_memories.analysis.moment_grouping import moments_to_read
    from immich_memories.analysis.moment_reading import read_moment

    by_id = {asset.id: (asset, score) for asset, score in metadata_scored}
    kept: list[tuple] = []
    # The sheet shows the whole moment — videos and claimed Live Photos
    # included — but only candidates can be chosen from it. A sheet built
    # from the photo pool alone describes a fragment of a day and then
    # judges from it.
    everything = [asset for asset, _score in metadata_scored] + list(alongside or [])
    # Sheets are asked about EPISODES, not ten-minute moments: a moment is
    # often one photograph, and a sheet of one photograph says nothing.
    for episode in moments_to_read(everything, app_config):
        frames = _frames_for_reading(episode, thumbnail_cache, thumbnail_fn)
        if not frames:
            continue
        reading = read_moment(episode, frames, app_config.llm)
        kept.extend(by_id[a.id] for a in reading.keep if a.id in by_id)
    if not kept:
        logger.info("Moment reading returned nothing; sampling one photo per moment instead")
        return None
    logger.info(
        "Moment reading: %d photos -> %d kept by what the sheets showed",
        len(metadata_scored),
        len(kept),
    )
    return kept
