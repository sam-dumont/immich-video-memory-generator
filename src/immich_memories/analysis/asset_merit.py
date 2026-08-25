"""How much an asset is worth showing, on one scale for photographs and video.

#677 moved video to a content-first score: what a clip shows, adjusted by how
well it was shot. Photographs kept the additive weighted sum it replaced, which
is why a face there is worth 0.15 + 0.10 — exactly what a favourite is worth —
and three strangers rank a photograph as high as the owner starring it does.

This module holds what both must agree on. Signal availability differs: a
photograph has no motion track and a video has no EXIF maker. The priors do not.
"""

from __future__ import annotations

from typing import Any


def ranking_key(asset: Any, merit: float) -> tuple[int, float]:
    """Order assets for selection: the owner's mark first, then merit.

    A favourite is a sort key rather than a term in the score, so no
    accumulation of technical signal can buy what the owner marking an asset
    means. Video already orders this way (clip_scaler, clip_backfill); adding
    a favourite to the number instead — as photo scoring did — is what let
    three detected faces tie with a star.
    """
    return (1 if getattr(asset, "is_favorite", False) else 0, merit)
