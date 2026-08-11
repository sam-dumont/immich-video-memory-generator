"""HDR detection and conversion utilities."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from immich_memories.processing.encoding_plan import HdrTransfer
from immich_memories.security import validate_video_path

__all__ = [
    "RequiredColorConversionUnavailable",
    "_detect_hdr_type",
    "_detect_color_primaries",
    "_get_dominant_hdr_type",
    "_get_colorspace_filter",
    "_get_hdr_conversion_filter",
    "_get_clip_hdr_types",
    "_resolve_clip_hdr",
    "detect_dominant_hdr_transfer",
    "quality_to_crf",
]

logger = logging.getLogger(__name__)


class RequiredColorConversionUnavailable(RuntimeError):
    """A required transfer conversion cannot be performed by this FFmpeg build."""


def _detect_hdr_type(video_path: Path) -> str | None:
    """Detect the HDR type of a video file.

    Cross-checks BOTH transfer function AND color primaries to avoid
    misdetecting Apple Shared Album videos (which carry bt2020nc tags
    but contain SDR content) as HDR.

    Returns:
        "hlg" for HLG (iPhone Dolby Vision 8.4)
        "pq" for HDR10/HDR10+ (Samsung, Pixel, etc.)
        None if SDR or unknown
    """
    video_path = validate_video_path(video_path, must_exist=True)
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=color_transfer,color_primaries",
                "-of",
                "json",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            import json

            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            if streams:
                color_trc = streams[0].get("color_transfer", "")
                primaries = streams[0].get("color_primaries", "")

                # HDR requires wide-gamut primaries — bt709 primaries
                # means the content is SDR even if transfer says otherwise
                # (common in Apple Shared Album re-encodes)
                if primaries != "bt2020":
                    return None

                if color_trc == "arib-std-b67":
                    return "hlg"
                elif color_trc in ("smpte2084", "bt2020-10", "bt2020-12"):
                    return "pq"
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        logger.debug(f"HDR detection failed for {video_path}: {e}")
    return None


def _detect_color_primaries(video_path: Path | str) -> str | None:
    """Detect the color primaries of a video file.

    Returns primaries string like "bt709", "smpte432" (Display P3),
    "bt2020", or None if detection fails.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=color_primaries",
                "-of",
                "json",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            import json

            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            if streams:
                return streams[0].get("color_primaries") or None
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        logger.debug(f"Color primaries detection failed for {video_path}: {e}")
    return None


def _get_dominant_hdr_type(clips: list) -> str:
    """Detect the dominant HDR type from a list of clips.

    Returns "hlg" or "pq" based on what most clips use.
    Defaults to "hlg" if detection fails (iPhone is most common).
    """
    transfer = detect_dominant_hdr_transfer(clips)
    if transfer is HdrTransfer.NONE:
        logger.info("No HDR detected, defaulting to HLG colorspace")
        return HdrTransfer.HLG.value
    return transfer.value


def detect_dominant_hdr_transfer(clips: list) -> HdrTransfer:
    """Return the exact dominant source transfer, or NONE for all-SDR input."""
    counts = {HdrTransfer.HLG: 0, HdrTransfer.PQ: 0}
    for clip in clips:
        path = clip.path if hasattr(clip, "path") else clip
        hdr_type = _detect_hdr_type(path)
        if hdr_type == HdrTransfer.HLG.value:
            counts[HdrTransfer.HLG] += 1
        elif hdr_type == HdrTransfer.PQ.value:
            counts[HdrTransfer.PQ] += 1

    if counts[HdrTransfer.PQ] > counts[HdrTransfer.HLG]:
        logger.info(
            "Detected HDR10/PQ format (Android/Samsung/Pixel) - %d clips",
            counts[HdrTransfer.PQ],
        )
        return HdrTransfer.PQ
    if counts[HdrTransfer.HLG] > 0:
        logger.info("Detected HLG format (iPhone) - %d clips", counts[HdrTransfer.HLG])
        return HdrTransfer.HLG
    return HdrTransfer.NONE


def has_any_hdr_clip(clips: list) -> bool:
    """Check if at least one clip has HDR metadata.

    Used to decide whether title screens should be HDR or SDR.

    Args:
        clips: List of clips (AssemblyClip or Path objects).

    Returns:
        True if at least one clip is HDR (HLG or PQ), False otherwise.
    """
    for clip in clips:
        path = clip.path if hasattr(clip, "path") else clip
        hdr_type = _detect_hdr_type(path)
        if hdr_type is not None:
            return True
    return False


def _get_colorspace_filter(hdr_type: str) -> str:
    """Get the setparams filter string for the given HDR type.

    Args:
        hdr_type: "hlg" for HLG, "pq" for HDR10/HDR10+

    Returns:
        FFmpeg setparams filter string
    """
    if hdr_type in ("sdr", HdrTransfer.NONE.value):
        return ",setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709"
    if hdr_type == "pq":
        # HDR10/HDR10+ (Samsung, Pixel, etc.) - uses PQ/SMPTE2084 transfer
        return ",setparams=colorspace=bt2020nc:color_primaries=bt2020:color_trc=smpte2084"
    # HLG (iPhone Dolby Vision 8.4) - uses ARIB STD-B67 transfer
    return ",setparams=colorspace=bt2020nc:color_primaries=bt2020:color_trc=arib-std-b67"


_zscale_cache: bool | None = None


def check_zscale_available() -> bool:
    """Return True if FFmpeg has the zscale filter available. Result is cached."""
    global _zscale_cache  # noqa: PLW0603
    if _zscale_cache is not None:
        return _zscale_cache
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
        )
        _zscale_cache = "zscale" in result.stdout
    except (OSError, subprocess.SubprocessError, ValueError):
        _zscale_cache = False
    return _zscale_cache


# Keep private alias for backward compat within this module
_check_zscale_available = check_zscale_available


def _get_sdr_to_hdr_filter(
    target_type: str,
    source_primaries: str | None,
    has_zscale: bool,
) -> str:
    """Return zscale filter string for SDR-to-HDR upscale, or empty string.

    Uses npl=203 (SDR reference white per BT.2408), explicit TV range,
    and agamma=false for accurate gamma — prevents red/warm color cast.
    """
    if not has_zscale:
        logger.warning("zscale not available - SDR to HDR conversion may look washed out")
        return ""
    src_pri = source_primaries or "bt709"
    src_matrix = "bt709" if src_pri in ("bt709", "smpte432") else src_pri
    if target_type == "hlg":
        logger.debug(f"Converting SDR ({src_pri}) -> HLG")
        return (
            f",zscale=tin=bt709:t=arib-std-b67"
            f":pin={src_pri}:p=bt2020:min={src_matrix}:m=bt2020nc"
            f":rin=tv:r=tv:npl=203:agamma=false"
        )
    if target_type == "pq":
        logger.debug(f"Converting SDR ({src_pri}) -> PQ/HDR10")
        return (
            f",zscale=tin=bt709:t=smpte2084"
            f":pin={src_pri}:p=bt2020:min={src_matrix}:m=bt2020nc"
            f":rin=tv:r=tv:npl=203:agamma=false"
        )
    return ""


def _get_hdr_to_hdr_filter(source_type: str, target_type: str, has_zscale: bool) -> str:
    """Return zscale filter string for HLG<->PQ conversion, or empty string."""
    if not has_zscale:
        logger.warning("zscale not available - HDR conversion may not be accurate")
        return ""
    if source_type == "hlg" and target_type == "pq":
        return (
            ",zscale=tin=arib-std-b67:t=smpte2084"
            ":pin=bt2020:p=bt2020:min=bt2020nc:m=bt2020nc"
            ":npl=203:agamma=false"
        )
    if source_type == "pq" and target_type == "hlg":
        return (
            ",zscale=tin=smpte2084:t=arib-std-b67"
            ":pin=bt2020:p=bt2020:min=bt2020nc:m=bt2020nc"
            ":npl=203:agamma=false"
        )
    return ""


def _get_hdr_to_sdr_filter(source_type: str, has_zscale: bool) -> str:
    """Return a deterministic HDR-to-BT.709 tone-map filter."""
    if not has_zscale:
        logger.warning("zscale not available - HDR to SDR tone mapping is unavailable")
        return ""
    transfer = "smpte2084" if source_type == "pq" else "arib-std-b67"
    return (
        f",zscale=t=linear:tin={transfer}:pin=bt2020:min=bt2020nc:rin=tv:npl=100"
        ",format=gbrpf32le,tonemap=tonemap=hable:desat=0"
        ",zscale=t=bt709:p=bt709:m=bt709:r=tv,format=yuv420p"
    )


def _get_hdr_conversion_filter(
    source_type: str | None,
    target_type: str,
    source_primaries: str | None = None,
    *,
    required: bool = False,
) -> str:
    """Get filter to convert between HDR formats (HLG <-> PQ) or SDR -> HDR.

    Uses zscale for proper colorspace and transfer function conversion.
    Falls back to colorspace filter if zscale unavailable.

    Args:
        source_type: Source HDR type ("hlg", "pq", "sdr", or None for unknown)
        target_type: Target dynamic range ("hlg", "pq", or "sdr")
        source_primaries: Source color primaries (e.g. "bt709", "smpte432" for
            Display P3). When None, defaults to "bt709" for SDR sources.
        required: Raise a typed error instead of returning an empty filter when
            conversion is required but zscale is unavailable.

    Returns:
        FFmpeg filter string for conversion, or empty string if no conversion needed
    """
    normalized_source = (
        "sdr" if source_type is None or source_type == HdrTransfer.NONE.value else source_type
    )
    if normalized_source == target_type:
        return ""

    has_zscale = _check_zscale_available()
    if required and not has_zscale:
        raise RequiredColorConversionUnavailable(
            f"Required {normalized_source}-to-{target_type} color conversion "
            "needs FFmpeg with the zscale filter"
        )

    if target_type == "sdr":
        return _get_hdr_to_sdr_filter(normalized_source, has_zscale)

    if normalized_source == "sdr":
        return _get_sdr_to_hdr_filter(target_type, source_primaries, has_zscale)

    return _get_hdr_to_hdr_filter(normalized_source, target_type, has_zscale)


def _get_clip_hdr_types(clips: list) -> list[str | None]:
    """Get HDR type for each clip in the list.

    Returns:
        List of HDR types ("hlg", "pq", or None) for each clip
    """
    hdr_types = []
    for clip in clips:
        path = clip.path if hasattr(clip, "path") else clip
        hdr_type = _detect_hdr_type(path)
        hdr_types.append(hdr_type)
    return hdr_types


def quality_to_crf(quality: str) -> int:
    """Map quality preset to CRF value.

    Lower CRF = higher quality = bigger file.
    These values are calibrated for near-transparent quality at "high".
    """
    return {"high": 12, "medium": 18, "low": 28}.get(quality, 12)


def _resolve_clip_hdr(
    clip_idx: int, ctx: Any | None, hdr_type: str | None
) -> tuple[str, str, str, str, bool]:
    """Resolve per-clip HDR settings from AssemblyContext.

    Returns (hdr_conversion, colorspace_filter, output_pix_fmt, sdr_to_hdr_filter, clip_is_hdr).
    """
    target_type = hdr_type or "sdr"
    source_type: str | None = None
    source_primaries: str | None = None
    output_pix_fmt = ""
    colorspace_filter = _get_colorspace_filter(target_type)

    if ctx is not None:
        target_type = getattr(ctx, "hdr_type", target_type)
        pix_fmt = getattr(ctx, "pix_fmt", "")
        output_pix_fmt = f",format={pix_fmt}" if pix_fmt else ""
        colorspace_filter = getattr(ctx, "colorspace_filter", "") or _get_colorspace_filter(
            target_type
        )
        clip_hdr_types = getattr(ctx, "clip_hdr_types", [])
        clip_primaries = getattr(ctx, "clip_primaries", [])
        if clip_idx < len(clip_hdr_types):
            source_type = clip_hdr_types[clip_idx]
        if clip_idx < len(clip_primaries):
            source_primaries = clip_primaries[clip_idx]

    clip_is_hdr = source_type in {HdrTransfer.HLG.value, HdrTransfer.PQ.value}
    normalized_source = source_type or "sdr"
    hdr_conversion = _get_hdr_conversion_filter(
        normalized_source,
        target_type,
        source_primaries=source_primaries,
        required=True,
    )
    return hdr_conversion, colorspace_filter, output_pix_fmt, "", clip_is_hdr
