"""Title text must not exceed HLG graphics white in an HDR run.

#506: graphics drawn at full white sit above the diffuse white of the picture
behind them, so on an HDR display the title glows instead of reading as paper
white. The measured ceiling is 0xBF — plain white lands at 872/1023 in the
10-bit pipe, ceilinged it lands at 721. The per-clip captions already do this
(processing/clip_caption.py:39); the title stack did not.

Run: make test-integration-titles
"""

from __future__ import annotations

import os

import numpy as np
import pytest

os.environ["IMMICH_FORCE_CPU"] = "1"

from immich_memories.titles.colors import HDR_GRAPHICS_WHITE  # noqa: E402
from immich_memories.titles.renderer_taichi import (  # noqa: E402
    TaichiTitleConfig,
    TaichiTitleRenderer,
)
from immich_memories.titles.taichi_kernels import TAICHI_AVAILABLE, init_taichi  # noqa: E402

requires_taichi = pytest.mark.skipif(not TAICHI_AVAILABLE, reason="Taichi not installed")
pytestmark = [pytest.mark.integration]

_CEILING = HDR_GRAPHICS_WHITE / 255.0

# Quantisation and the renderer's own blending cost a code value or two; this is
# about a 25% difference in level, not about the last bit.
_TOLERANCE = 0.01


@pytest.fixture(scope="module")
def _taichi_cpu():
    if not TAICHI_AVAILABLE:
        pytest.skip("Taichi not installed")
    backend = init_taichi()
    assert backend is not None
    return backend


def _config(*, hdr: bool) -> TaichiTitleConfig:
    return TaichiTitleConfig(
        width=320,
        height=180,
        fps=10.0,
        duration=3.5,
        # A near-black background so the brightest pixel in the frame is text
        # and nothing else — no bokeh, no gradient ramp to confuse the peak.
        bg_color1="#0A0A0A",
        bg_color2="#0A0A0A",
        enable_bokeh=False,
        blur_radius=3,
        use_sdf_text=False,
        text_color="#FFFFFF",
        hdr=hdr,
    )


def _peak_level(hdr: bool) -> float:
    """Brightest pixel in a fully-faded-in title frame, as a fraction of full scale."""
    renderer = TaichiTitleRenderer(_config(hdr=hdr))
    # 1.5s in: past the 0.6s fade-in, well before the fade-out.
    frame = renderer.render_frame(15, "Title", "Subtitle")
    full_scale = 65535.0 if hdr else 255.0
    return float(frame.max()) / full_scale


@requires_taichi
def test_sdr_title_text_still_reaches_full_white(_taichi_cpu) -> None:
    """Control: the ceiling is an HDR-only concession, not a global dimmer."""
    peak = _peak_level(hdr=False)

    assert peak > _CEILING + _TOLERANCE, (
        f"SDR text peaked at {peak:.3f} — the ceiling leaked into SDR output"
    )


@requires_taichi
def test_hdr_title_text_stays_at_or_below_graphics_white(_taichi_cpu) -> None:
    peak = _peak_level(hdr=True)

    assert peak <= _CEILING + _TOLERANCE, (
        f"HDR title text peaked at {peak:.3f} of full scale, above HLG graphics "
        f"white ({_CEILING:.3f}) — it will glow on an HDR display"
    )


@requires_taichi
def test_the_hdr_frame_still_has_readable_text(_taichi_cpu) -> None:
    """The ceiling must dim the text, not erase it."""
    renderer = TaichiTitleRenderer(_config(hdr=True))
    frame = renderer.render_frame(15, "Title", "Subtitle")

    background = float(np.median(frame)) / 65535.0
    peak = float(frame.max()) / 65535.0

    assert peak - background > 0.3, (
        f"text ({peak:.3f}) barely stands out from the background ({background:.3f})"
    )


def _pil_peak(hdr: bool) -> float:
    """Brightest pixel of a PIL-rendered title, as a fraction of full scale.

    The PIL renderer is the fallback used when Taichi is unavailable — a
    CPU-only deployment renders every title through it, and `title_color_filter`
    still maps its output into HLG, so the glow reproduces there too.
    """
    from immich_memories.titles.renderer_pil import RenderSettings, TitleRenderer
    from immich_memories.titles.styles import TitleStyle

    style = TitleStyle(name="probe", background_type="solid", background_colors=["#0A0A0A"])
    settings = RenderSettings(
        width=320, height=180, fps=10.0, duration=3.5, animated_background=False, hdr=hdr
    )
    frame = np.array(TitleRenderer(style, settings).render_frame("Title", "Subtitle", 30))
    return float(frame.max()) / 255.0


def test_pil_fallback_pulls_hdr_text_down_from_full_white() -> None:
    """The PIL path composites text with a 'screen' blend over the background,
    which lifts the result a few levels back up — measured 193/255 on this
    near-black background against a ceiling of 191. So this asserts the drop is
    real and lands near the ceiling, not that it hits it exactly."""
    sdr_peak = _pil_peak(hdr=False)
    hdr_peak = _pil_peak(hdr=True)

    assert sdr_peak > 0.99, f"SDR text no longer reaches full white ({sdr_peak:.3f})"
    assert hdr_peak < 0.80, (
        f"HDR text peaked at {hdr_peak:.3f} — still glowing above graphics white"
    )
    assert hdr_peak > 0.6, f"HDR text at {hdr_peak:.3f} is dimmer than the ceiling asked for"


def _brightest_text_channel(overlay) -> int:
    """Peak RGB level among pixels the overlay actually painted.

    Transparent pixels carry junk colour, so alpha gates what counts.
    """
    pixels = np.array(overlay.convert("RGBA"))
    painted = pixels[pixels[:, :, 3] > 0]
    assert painted.size > 0, "overlay painted nothing"
    return int(painted[:, :3].max())


class TestMapFlyOverText:
    """The fly-over composites its own text straight onto the map tiles — no
    dimming pass stands between it and the output, unlike the static map, which
    the Taichi path dims to 55% before compositing."""

    def test_title_overlay_is_ceilinged_in_hdr(self) -> None:
        from immich_memories.titles.map_animation import _render_title_overlay

        overlay = _render_title_overlay("Lisbon", 640, 360, hdr=True)

        assert _brightest_text_channel(overlay) <= HDR_GRAPHICS_WHITE

    def test_title_overlay_still_full_white_in_sdr(self) -> None:
        from immich_memories.titles.map_animation import _render_title_overlay

        overlay = _render_title_overlay("Lisbon", 640, 360, hdr=False)

        assert _brightest_text_channel(overlay) == 255

    def test_pin_labels_are_ceilinged_in_hdr(self) -> None:
        from PIL import Image

        from immich_memories.titles.map_animation import _draw_pins, _FlyConfig, _PinData

        frame = Image.new("RGB", (640, 360), (0, 0, 0))
        pins = [_PinData(lat=38.72, lon=-9.14, name="Lisbon")]
        cfg = _FlyConfig(pins=pins, width=640, height=360, dest_zoom=9.0, hdr=True)

        drawn = np.array(_draw_pins(frame, 38.72, -9.14, 12.0, cfg)).astype(int)

        # The pin's own marker is a saturated orange and keeps its punch; only
        # the near-neutral pixels — the white ring and the label — are graphics.
        peak = drawn.max(axis=2)
        neutral = (peak - drawn.min(axis=2)) <= 20
        assert int(peak[neutral].max()) <= HDR_GRAPHICS_WHITE, (
            "a pin label or ring is still drawn above graphics white"
        )
