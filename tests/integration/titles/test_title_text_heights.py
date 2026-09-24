"""The subtitle reads smaller than the title, measured in the rendered ink.

The title service hands the kernel renderer the style's title ratio and a
subtitle at ``TitleStyle.subtitle_size_ratio`` (0.75) of it. The renderer then
shrinks the title to make room for a second line; if that shrink skips the
subtitle, the date under "Summer in Lisbon" comes out as large as the title.

Heights are taken from the layers the compositor uploads, so this is what lands
on the frame. The strings are capitals on purpose: every capital here stands on
the baseline and reaches cap height, so one line's ink height is proportional
to its font size and the comparison is about size, not about which letters
have descenders.

Run: make test-integration-titles
"""

from __future__ import annotations

from typing import Any, cast

import numpy as np
import pytest

from immich_memories.titles.kernel_text import TitleTextRenderer
from immich_memories.titles.renderer_kernels import KernelTitleConfig
from immich_memories.titles.styles import PRESET_STYLES, TitleStyle

pytestmark = pytest.mark.integration

LANDSCAPE_1080P = (1920, 1080)
PORTRAIT_1080P = (1080, 1920)
SHORT_TITLE = "SUMMER IN LISBON"
LONG_TITLE = "SUMMER IN LISBON AND THE ALGARVE COAST WITH THE WHOLE FAMILY"
SUBTITLE = "JULY TWENTY"

# A rasterised line gains or loses a pixel of ink at its edges; this is the
# slack around the style's ratio, not a second ratio.
RATIO_TOLERANCE = 0.06


def _renderer(style: TitleStyle, size: tuple[int, int]) -> TitleTextRenderer:
    """The kernel text renderer, configured the way rendering_service.py builds it."""
    width, height = size
    config = KernelTitleConfig(
        width=width,
        height=height,
        title_size_ratio=style.title_size_ratio,
        subtitle_size_ratio=style.subtitle_size_ratio * style.title_size_ratio,
        font_family="Montserrat",
        use_sdf_text=False,
        enable_shadow=False,
    )
    # WHY None buffers: planning rasterises with PIL on the host; nothing is
    # uploaded until a frame is composited, which this test never does.
    return TitleTextRenderer(config, cast(Any, None))


def _line_heights(layer: np.ndarray) -> list[int]:
    """Ink height of each text line in an RGBA layer, top to bottom."""
    inked = layer[..., 3].max(axis=1) > 0.5
    heights: list[int] = []
    run = 0
    for row in inked:
        if row:
            run += 1
        elif run:
            heights.append(run)
            run = 0
    if run:
        heights.append(run)
    return heights


@pytest.mark.parametrize("size", [LANDSCAPE_1080P, PORTRAIT_1080P], ids=["landscape", "portrait"])
@pytest.mark.parametrize("title", [SHORT_TITLE, LONG_TITLE], ids=["short", "long"])
def test_the_subtitle_is_drawn_at_the_style_ratio_of_the_title(
    title: str, size: tuple[int, int]
) -> None:
    style = PRESET_STYLES["modern_warm"]
    plan = _renderer(style, size).plan_text_layers(title, SUBTITLE)
    assert plan.subtitle_layer is not None

    title_line = max(_line_heights(plan.title_layer))
    subtitle_line = max(_line_heights(plan.subtitle_layer))
    measured = subtitle_line / title_line

    assert measured == pytest.approx(style.subtitle_size_ratio, abs=RATIO_TOLERANCE), (
        f"subtitle cap height {subtitle_line}px against the title's {title_line}px "
        f"is {measured:.2f} of it; the style asks for {style.subtitle_size_ratio}"
    )
