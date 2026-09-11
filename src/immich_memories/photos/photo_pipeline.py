"""Render one photograph as an animated video clip.

Streams frames directly to FFmpeg via stdin pipe — never holds more than
one frame in memory at a time.
"""

from __future__ import annotations

import hashlib
import logging
import random
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import cv2
import numpy as np

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
from immich_memories.processing.assembly_config import AssemblyClip
from immich_memories.processing.ffmpeg_runner import write_frames_to_ffmpeg

logger = logging.getLogger(__name__)


# How many previews the tie-break may fetch when nothing cached them. The
# photos in contention are what it is for, and a library's worth of previews
# is the cost the LLM shortlist exists to avoid.
# Scoring one photo costs an LLM round trip, so the shortlist is what actually
# determines how long a run takes. A real library produced a 1194-photo
# shortlist to place a few dozen photos, which ran for hours.
# Undated photos sort first rather than dropping out of the grouping.
# What counts as one moment when sampling photos for a content look.
# How much of a photo's score the pixels are allowed to decide. The metadata
# terms keep their meaning, but they no longer decide the whole ordering on
# their own.
def _source_photo_path(asset: Asset, work_dir: Path) -> Path:
    ext = Path(asset.original_file_name).suffix if asset.original_file_name else ".jpg"
    return work_dir / f"{asset.id}{ext}"


def _prepared_photo_pixels(raw_path: Path, target_w: int, target_h: int, work_dir: Path):
    """Decode the source into normalized float RGB, or report it unreadable."""
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
    return prepared, img


def _ken_burns_params(asset: Asset, prepared: Any, fps: int, duration: float) -> KenBurnsParams:
    """Reproducible per-asset move: the same photograph always pans the same way."""
    face_target = face_aware_pan(asset.people, prepared.width, prepared.height)
    seed = int(hashlib.sha256(asset.id.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    return KenBurnsParams(
        zoom_start=1.0,
        zoom_end=1.0 + rng.uniform(0.05, 0.12),
        pan_start=(rng.uniform(0.3, 0.7), rng.uniform(0.3, 0.7)),
        pan_end=face_target,
        fps=fps,
        duration=duration,
    )


def _render_single_photo(
    asset: Asset,
    config: PhotoConfig,
    target_w: int,
    target_h: int,
    work_dir: Path,
    download_fn: Any,
    fps: int = 30,
    *,
    source_path: Path | None = None,
) -> AssemblyClip | None:
    """Download, prepare, render (streaming), and encode a single photo."""
    try:
        raw_path = _source_photo_path(asset, work_dir) if source_path is None else source_path
        if source_path is None and not raw_path.exists():
            download_fn(asset.id, raw_path)  # Download from Immich
        loaded = _prepared_photo_pixels(raw_path, target_w, target_h, work_dir)
        if loaded is None:
            return None
        prepared, img = loaded
        params = _ken_burns_params(asset, prepared, fps, config.duration)

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
