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
