"""Reject messaging re-encodes without rejecting genuinely old footage.

WhatsApp and similar apps re-encode to a few hundred pixels and strip the
camera EXIF while doing it. Filtering on resolution alone would also throw away
legitimately small originals from an older camera, so the two signals are used
together: a small clip is only rejected when nothing says a camera made it.

Measured on a real June pool of 111 motion candidates: all 17 below 1080p had
no camera make or model at all, and all 89 with camera EXIF were 1080p or
better. One clip had no EXIF and full resolution, and it survives the rule.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.api.models import Asset, VideoClipInfo

logger = logging.getLogger(__name__)


def is_usable_source(
    *,
    width: int,
    height: int,
    has_camera_exif: bool,
    min_short_side: int,
) -> bool:
    """Whether a clip is worth putting in a memory at full output resolution."""
    short_side = min(width, height) if width and height else 0
    if short_side == 0:
        # Live-photo rows frequently have no cached dimensions. Absence of
        # evidence is not evidence of a re-encode.
        return True
    if short_side >= min_short_side:
        return True
    return has_camera_exif


def grounded_source_annotations(
    asset: Asset,
    clip: VideoClipInfo | None = None,
) -> tuple[str, ...]:
    """Return existing technical and semantic observations without judging a source."""
    width, height = _dimensions(asset, clip)
    annotations: list[str] = []
    if width and height:
        annotations.append(f"resolution:{width}x{height}")
    if _is_reencode_suspected(width, height, _has_camera_exif(asset)):
        annotations.append("reencode-suspected")
    duration = clip.duration_seconds if clip is not None else asset.duration_seconds
    if duration is not None:
        annotations.append(f"duration:{duration:.3f}s")
    annotations.extend(_available_image_observations(asset, clip))
    return tuple(annotations)


def _dimensions(asset: Asset, clip: VideoClipInfo | None) -> tuple[int, int]:
    if clip is not None and clip.width and clip.height:
        return clip.width, clip.height
    return asset.width, asset.height


def _is_reencode_suspected(width: int, height: int, has_camera_exif: bool) -> bool:
    return bool(width and height and min(width, height) < 1080 and not has_camera_exif)


def _has_camera_exif(asset: Asset) -> bool:
    exif = asset.exif_info
    return bool(exif and (exif.make or exif.model))


def _available_image_observations(asset: Asset, clip: VideoClipInfo | None) -> list[str]:
    annotations: list[str] = []
    exif = asset.exif_info
    if exif and exif.exposure_time:
        annotations.append(f"exposure:{exif.exposure_time}")
    if clip is None:
        return annotations
    annotations.append("motion:available")
    if clip.live_burst_still_ids:
        annotations.append(f"burst-members:{len(clip.live_burst_still_ids)}")
    if clip.llm_category:
        annotations.append(f"subject:{clip.llm_category}")
    annotations.extend(_available_metrics(clip))
    return annotations


def _available_metrics(clip: VideoClipInfo) -> list[str]:
    annotations: list[str] = []
    for attribute, label in (
        ("blur_score", "blur"),
        ("exposure_score", "exposure"),
        ("similarity_group_id", "similarity"),
    ):
        value = getattr(clip, attribute, None)
        if value is not None:
            annotations.append(f"{label}:{value}")
    return annotations
