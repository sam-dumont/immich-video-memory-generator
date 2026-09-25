"""The opening title's two text blocks: where they land, and that they never touch.

The film that produced these strings was a multi-person memory over a date
range, rendered 4K portrait: the title wrapped to two lines, the subtitle to
three, and the constant shift the compositor used put them on top of each other.
"""

from typing import Any, cast

import numpy as np
import pytest

from immich_memories.titles.kernel_text import TitleTextRenderer
from immich_memories.titles.renderer_kernels import KernelTitleConfig
from immich_memories.titles.safe_zones import safe_text_height
from immich_memories.titles.text_layout import (
    min_text_size,
    stack_text_blocks,
    text_blocks_overlap,
)

# The real film: a date-range title over three companions, 4K portrait.
TITLE = "Février 2024 à Septembre 2026"
SUBTITLE = "Person A · Person B · Person C"
PORTRAIT_4K = {"width": 2160, "height": 3840}

# What rendering_service.py hands the kernel renderer for a styled title:
# the style's own ratio, and the subtitle at 0.75 of it.
STYLE_TITLE_RATIO = 0.14
STYLE_SUBTITLE_RATIO = 0.75 * 0.14


@pytest.fixture
def pinned_font(monkeypatch: pytest.MonkeyPatch) -> None:
    """Draw with Pillow's own face so line counts do not depend on the machine.

    # WHY: the installed font set is an external boundary — a mac has
    # Helvetica, a CI box may have nothing at all, and an absent face silently
    # falls back to a bitmap font that ignores the requested size. Pointing the
    # lookup at a path that does not exist takes the renderer's own fallback,
    # which is Pillow's bundled scalable face, identical everywhere.
    """
    monkeypatch.setattr(
        "immich_memories.titles.kernel_text._get_system_font",
        lambda _family: "/nonexistent/no-such-font.ttf",
    )


def _renderer(**overrides: Any) -> TitleTextRenderer:
    config = KernelTitleConfig(
        **PORTRAIT_4K,
        title_size_ratio=STYLE_TITLE_RATIO,
        subtitle_size_ratio=STYLE_SUBTITLE_RATIO,
        **overrides,
    )
    # WHY None buffers: planning rasterizes with PIL on the host; nothing is
    # uploaded until a frame is composited, which these tests never do.
    return TitleTextRenderer(config, cast(Any, None))


@pytest.mark.usefixtures("pinned_font")
def test_a_two_line_title_and_a_three_line_subtitle_do_not_overlap() -> None:
    plan = _renderer().plan_text_layers(TITLE, SUBTITLE)

    assert plan.stack.title_lines >= 2
    assert plan.stack.subtitle_lines >= 2
    assert not text_blocks_overlap(
        plan.title_layer,
        plan.subtitle_layer,
        plan.stack.title_shift,
        plan.stack.subtitle_shift,
    )

    half_safe = safe_text_height(PORTRAIT_4K["height"]) / 2
    assert plan.stack.top >= -half_safe
    assert plan.stack.bottom <= half_safe


def test_one_line_title_and_subtitle_keep_todays_distance() -> None:
    """One line each has to land exactly where the old constant put it."""
    title_size = 200
    subtitle_size = int(title_size * 0.75)  # TitleStyle.subtitle_size_ratio

    stack = stack_text_blocks(
        "Septembre 2026",
        "Person A",
        title_size,
        subtitle_size,
        frame_height=PORTRAIT_4K["height"],
        # A short string is one line however it is measured; this keeps the
        # spacing assertion about the layout and not about a font's metrics.
        count_lines=lambda _text, _size: 1,
    )

    assert abs((stack.subtitle_shift - stack.title_shift) - 1.3 * title_size) <= 1.0


@pytest.mark.usefixtures("pinned_font")
def test_an_overlong_pair_shrinks_until_it_fits() -> None:
    """Forty companions cannot be drawn at the size three of them are."""
    crowd = " · ".join(f"Person {number}" for number in range(1, 41))
    renderer = _renderer()

    crowded = renderer.plan_text_layers(TITLE, crowd)
    roomy = renderer.plan_text_layers(TITLE, SUBTITLE)

    assert crowded.stack.title_size < roomy.stack.title_size
    assert crowded.stack.subtitle_size < roomy.stack.subtitle_size
    assert min(crowded.stack.title_size, crowded.stack.subtitle_size) >= min_text_size(
        PORTRAIT_4K["height"]
    )
    assert crowded.stack.bottom - crowded.stack.top <= safe_text_height(PORTRAIT_4K["height"])
    assert not text_blocks_overlap(
        crowded.title_layer,
        crowded.subtitle_layer,
        crowded.stack.title_shift,
        crowded.stack.subtitle_shift,
    )


@pytest.mark.usefixtures("pinned_font")
def test_the_gate_detects_an_overlap() -> None:
    """The shift the renderer used to apply is exactly the collision it has to catch."""
    plan = _renderer().plan_text_layers(TITLE, SUBTITLE)

    assert text_blocks_overlap(
        plan.title_layer,
        plan.subtitle_layer,
        0.0,
        plan.stack.title_size * 1.3,
    )


PORTRAIT_1080 = {"width": 1080, "height": 1920}


@pytest.mark.usefixtures("pinned_font")
def test_a_long_trip_title_in_portrait_stays_within_three_lines() -> None:
    title = "DEUX SEMAINES DANS L'UTAH ET AU NEVADA, ÉTATS-UNIS, ÉTÉ 2025"
    renderer = TitleTextRenderer(
        KernelTitleConfig(**PORTRAIT_1080, title_size_ratio=STYLE_TITLE_RATIO), cast(Any, None)
    )

    plan = renderer.plan_text_layers(title, None)

    assert plan.stack.title_lines <= 3


@pytest.mark.usefixtures("pinned_font")
def test_a_word_wider_than_the_frame_shrinks_until_it_is_inside() -> None:
    renderer = TitleTextRenderer(
        KernelTitleConfig(width=1920, height=1080, title_size_ratio=STYLE_TITLE_RATIO),
        cast(Any, None),
    )

    plan = renderer.plan_text_layers("LLANFAIRPWLLGWYNGYLLGOGERYCHWYRNDROBWLL", None)

    inked_columns = np.nonzero(plan.title_layer[..., 3].max(axis=0) > 0)[0]
    assert inked_columns.min() > 0
    assert inked_columns.max() < 1919
