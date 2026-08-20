"""How much of a title frame the platform's own UI will cover.

Reels, Shorts and TikTok paint a column of action buttons down the right side of
a vertical video, across the middle of the frame — which is exactly where a
centred title sits. Measured on a real portrait render, a title spanned 13%-86%
of the width at half the height, straight under the rail.

Landscape output carries no such chrome, so it keeps the tighter margin.
"""

from __future__ import annotations

_LANDSCAPE = 0.10
_PORTRAIT = 0.16


def safe_margin_ratio(width: int, height: int) -> float:
    """Fraction of the width to keep clear on each side of a title."""
    return _PORTRAIT if height > width else _LANDSCAPE


def safe_text_width(width: int, height: int) -> float:
    """Widest a title may be before it has to shrink."""
    return width * (1.0 - 2 * safe_margin_ratio(width, height))
