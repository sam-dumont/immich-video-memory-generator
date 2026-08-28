"""Reject messaging re-encodes without rejecting genuinely old footage.

WhatsApp and similar apps resize and recompress media while stripping camera
EXIF. Filtering on resolution alone would also throw away legitimate originals,
so size is combined with provenance: very small unknown files are rejected, as
are measured 2048px UUID JPEG exports with no camera metadata.

Measured on a real June pool of 111 motion candidates: all 17 below 1080p had
no camera make or model at all, and all 89 with camera EXIF were 1080p or
better. One clip had no EXIF and full resolution, and it survives the rule.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.analysis.editorial_contracts import SourceEvidence

if TYPE_CHECKING:
    from immich_memories.api.models import Asset, VideoClipInfo

logger = logging.getLogger(__name__)

# Before smartphones made received media an everyday part of the camera roll,
# small files without camera EXIF were normal originals, scans, and exports.
# Keep all of 2007 in that historical regime: the first iPhone existed that
# year, but the modern always-on sharing behaviour this heuristic detects did
# not yet describe a personal library.
MODERN_MOBILE_SHARING_START = date(2008, 1, 1)

# Measured on the June 2023 source wall: WhatsApp's renamed HD exports were
# progressive JPEGs capped at 2048 px, with UUID filenames and no camera
# metadata. Published race photographs that also lost their camera EXIF were
# 4000 px Lightroom exports or explicitly named files. Each fact alone is
# ordinary; their conjunction is the provenance fingerprint.
FORWARDED_MEDIA_LONG_SIDE_CAP = 2048
_UUID_JPEG = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(?:jpe?g)",
    re.IGNORECASE,
)


def predates_modern_mobile_sharing(captured_at: datetime | None) -> bool:
    """Whether modern forwarding heuristics are anachronistic for this asset."""
    return isinstance(captured_at, datetime) and captured_at.date() < MODERN_MOBILE_SHARING_START


def is_usable_source(
    *,
    width: int,
    height: int,
    has_camera_exif: bool,
    min_short_side: int,
    captured_at: datetime | None = None,
    original_file_name: str | None = None,
) -> bool:
    """Whether a clip is worth putting in a memory at full output resolution."""
    if predates_modern_mobile_sharing(captured_at):
        return True
    short_side = min(width, height) if width and height else 0
    if short_side == 0:
        # Live-photo rows frequently have no cached dimensions. Absence of
        # evidence is not evidence of a re-encode.
        return True
    if has_camera_exif:
        return True
    if likely_forwarded_media(
        width=width,
        height=height,
        has_camera_exif=has_camera_exif,
        original_file_name=original_file_name,
        captured_at=captured_at,
    ):
        return False
    return short_side >= min_short_side


def likely_forwarded_media(
    *,
    width: int,
    height: int,
    has_camera_exif: bool,
    original_file_name: str | None,
    captured_at: datetime | None = None,
) -> bool:
    """Recognize a measured renamed messaging export without judging its content."""
    if has_camera_exif or predates_modern_mobile_sharing(captured_at):
        return False
    if not width or not height or max(width, height) > FORWARDED_MEDIA_LONG_SIDE_CAP:
        return False
    name = Path(original_file_name or "").name
    return bool(_UUID_JPEG.fullmatch(name))


def grounded_source_annotations(
    asset: Asset,
    clip: VideoClipInfo | None = None,
    evidence: SourceEvidence | None = None,
) -> tuple[str, ...]:
    """Return existing technical and semantic observations without judging a source."""
    width, height = _dimensions(asset, clip)
    annotations: list[str] = []
    if width and height:
        annotations.append(f"resolution:{width}x{height}")
    if _is_reencode_suspected(
        width,
        height,
        _has_camera_exif(asset),
        original_file_name=asset.original_file_name,
        captured_at=asset.file_created_at,
    ):
        annotations.append("reencode-suspected")
    duration = clip.duration_seconds if clip is not None else asset.duration_seconds
    if duration is not None:
        annotations.append(f"duration:{duration:.3f}s")
    annotations.extend(_available_image_observations(asset, clip, evidence))
    return tuple(annotations)


def _dimensions(asset: Asset, clip: VideoClipInfo | None) -> tuple[int, int]:
    if clip is not None and clip.width and clip.height:
        return clip.width, clip.height
    return asset.width, asset.height


def _is_reencode_suspected(
    width: int,
    height: int,
    has_camera_exif: bool,
    *,
    original_file_name: str | None,
    captured_at: datetime | None,
) -> bool:
    return likely_forwarded_media(
        width=width,
        height=height,
        has_camera_exif=has_camera_exif,
        original_file_name=original_file_name,
        captured_at=captured_at,
    ) or bool(
        not predates_modern_mobile_sharing(captured_at)
        and width
        and height
        and min(width, height) < 1080
        and not has_camera_exif
    )


def _has_camera_exif(asset: Asset) -> bool:
    exif = asset.exif_info
    return bool(exif and (exif.make or exif.model))


def _available_image_observations(
    asset: Asset,
    clip: VideoClipInfo | None,
    evidence: SourceEvidence | None,
) -> list[str]:
    annotations: list[str] = []
    exif = asset.exif_info
    if exif and exif.exposure_time:
        annotations.append(f"exposure:{exif.exposure_time}")
    if clip is not None:
        annotations.append("motion:available")
        if clip.live_burst_still_ids:
            annotations.append(f"burst-members:{len(clip.live_burst_still_ids)}")
        if clip.llm_category:
            annotations.append(f"subject:{clip.llm_category}")
        if clip.llm_quality is not None:
            annotations.append(f"analysis-quality:{clip.llm_quality}")
    if evidence is not None:
        annotations.extend(_precomputed_annotations(evidence))
    return annotations


def _precomputed_annotations(evidence: SourceEvidence) -> list[str]:
    return [
        f"{label}:{value}"
        for value, label in (
            (evidence.blur, "blur"),
            (evidence.exposure, "exposure"),
            (evidence.similarity, "similarity"),
        )
        if value is not None
    ]
