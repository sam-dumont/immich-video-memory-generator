"""Text must clear the platform chrome in vertical output (#313).

A 9:16 render is for Reels, Shorts and Stories, and all three paint their own
UI over the frame: captions, handles and action buttons across roughly the
bottom sixth, status and progress across the top. Text inset by a percentage of
the *short* side — which is what a landscape-shaped rule gives you — lands under
that chrome and is simply not readable.
"""

from __future__ import annotations

import re

from immich_memories.processing.clip_caption import ClipCaption, caption_filters


def caption_filter(text: str, w: int, h: int) -> str:
    (only,) = caption_filters(ClipCaption(date=text), w, h)
    return only


# 1080x1920 is the canonical vertical render.
_W, _H = 1080, 1920


def _inset_from_bottom(vf: str) -> int:
    match = re.search(r"y=h-th-(\d+)", vf)
    assert match, f"no bottom inset in {vf}"
    return int(match.group(1))


def test_a_vertical_caption_clears_the_bottom_chrome() -> None:
    """~15% of height is where the caption and action rail sit."""
    inset = _inset_from_bottom(caption_filter("5 Jan 2026", _W, _H))

    assert inset >= _H * 0.15, f"inset {inset}px of {_H} leaves the date under the platform UI"


def test_a_landscape_caption_is_not_pushed_inwards() -> None:
    """No chrome to dodge on a TV or a laptop; a large inset would just look odd."""
    inset = _inset_from_bottom(caption_filter("5 Jan 2026", 1920, 1080))

    assert inset < 1080 * 0.10
