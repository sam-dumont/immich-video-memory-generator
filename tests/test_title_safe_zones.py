"""A centred title in 9:16 runs under the platform's action rail.

Measured on a real portrait render: the title spanned 13%-87% of the width at
50% of the height. Reels, Shorts and TikTok paint a column of action buttons
down the right side across the middle of the frame — exactly there. The renderer
already shrinks text to fit a safe margin, but the margin was 10% regardless of
orientation, and 10% of a narrow frame is not enough.
"""

from __future__ import annotations

from immich_memories.titles.safe_zones import safe_margin_ratio, safe_text_width


def test_portrait_reserves_more_than_landscape() -> None:
    assert safe_margin_ratio(1080, 1920) > safe_margin_ratio(1920, 1080)


def test_a_portrait_title_clears_the_action_rail() -> None:
    """The rail occupies roughly the outer 15% of a vertical frame."""
    margin = safe_margin_ratio(1080, 1920)

    assert margin >= 0.15


def test_landscape_is_unchanged() -> None:
    """No reason to shrink titles on a 16:9 render; nothing overlays it."""
    assert safe_margin_ratio(1920, 1080) == 0.10


def test_square_is_treated_as_landscape() -> None:
    """A square render is not a short-form format and carries no chrome."""
    assert safe_margin_ratio(1080, 1080) == 0.10


def test_the_measured_overlap_would_now_shrink() -> None:
    """The real case: a 4K portrait title 1588px wide sat under the action rail.

    Under the old flat 0.8 budget (1728px at 2160 wide) it did not shrink, and
    measured 12.9%-86.4% of the width at half the height. The portrait budget
    is narrower than the title, so it now shrinks to fit.
    """
    measured_title_width = 1588

    assert safe_text_width(2160, 3840) < measured_title_width < 2160 * 0.8


def test_a_landscape_title_of_the_same_width_is_left_alone() -> None:
    assert safe_text_width(3840, 2160) > 1588
