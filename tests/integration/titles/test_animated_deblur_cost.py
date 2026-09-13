"""The cheap animated deblur has to draw the same title as the dear one.

A content-backed title blurs its background with a radius of a tenth of the
frame height and, until #900, did it at full resolution on every frame. That
was 85% of a title screen on a CPU backend and 70% of a whole CPU-only render.
The blur now runs on a quarter-size copy and is held across the flat tail of
the deblur curve while the picture holds still.

Both are approximations, so the bar is the picture, not the bits: the largest
per-channel difference between the cheap render and the full-resolution one
must stay under the film grain the renderer lays over the result anyway. The
grain is measured here rather than asserted from a constant, so the bar moves
if the grain ever does.

Run: make test-integration-titles
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from time import perf_counter

import numpy as np
import pytest

os.environ["IMMICH_FORCE_CPU"] = "1"

from immich_memories.titles.kernel_blur import AnimatedBlur  # noqa: E402
from immich_memories.titles.kernels import KERNELS_AVAILABLE, init_kernels  # noqa: E402
from immich_memories.titles.renderer_kernels import (  # noqa: E402
    KernelTitleConfig,
    KernelTitleRenderer,
)
from tests.integration.conftest import requires_ffmpeg  # noqa: E402

pytestmark = [
    pytest.mark.integration,
    requires_ffmpeg,
    pytest.mark.skipif(not KERNELS_AVAILABLE, reason="no kernel library wheel for this platform"),
]

# Small enough to hold five whole titles in memory, large enough that the
# decimation is a real quarter and the radius is the production tenth of the
# height. The timing test below runs at 720p, where the blur is dominant.
WIDTH, HEIGHT, FPS, DURATION = 640, 360, 30.0, 2.0
TITLE, SUBTITLE = "Summer in Lisbon", "July 2024"


@pytest.fixture(scope="module")
def _kernels_on_cpu() -> str:
    backend = init_kernels()
    assert backend is not None
    return backend


def _clip(directory: Path, width: int, height: int) -> Path:
    """A clip whose picture actually moves, so the held blur has to notice."""
    clip = directory / f"moving_{width}x{height}.mp4"
    subprocess.run(  # noqa: S603, S607 — fixed argv, no shell
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={width}x{height}:rate=30:duration=3",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            str(clip),
        ],  # fmt: skip
        check=True,
    )
    return clip


@pytest.fixture(scope="module")
def moving_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _clip(tmp_path_factory.mktemp("deblur"), WIDTH, HEIGHT)


def _config(
    clip: Path,
    *,
    reverse: bool,
    noise: bool = True,
    width: int = WIDTH,
    height: int = HEIGHT,
    duration: float = DURATION,
) -> KernelTitleConfig:
    """What the rendering service builds for a content-backed title screen."""
    from immich_memories.titles.content_background import SlowmoBackgroundReader

    reader = SlowmoBackgroundReader(clip, width, height, FPS, title_duration=duration)
    assert reader.is_active, "the slow-mo reader loaded no source frames"
    return KernelTitleConfig(
        width=width,
        height=height,
        fps=FPS,
        duration=duration,
        background_reader=reader,
        blur_radius=int(height * 0.10),
        gradient_rotation=0.0,
        color_pulse_amount=0.0,
        vignette_pulse=0.0,
        vignette_strength=0.15,
        enable_noise=noise,
        enable_bokeh=True,
        reverse_blur=reverse,
    )


def _render(config: KernelTitleConfig, *, cheap: bool, keep: bool = True):
    """Every frame of one title, and what it cost per frame."""
    blur = (
        None
        if cheap
        else AnimatedBlur(config.height, config.width, config.blur_radius, downscale=1, reuse=False)
    )
    renderer = KernelTitleRenderer(config, animated_blur=blur)
    renderer.render_frame(0, TITLE, SUBTITLE)  # warm the kernels, then time
    frames = []
    started = perf_counter()
    for number in range(renderer.total_frames):
        frame = renderer.render_frame(number, TITLE, SUBTITLE)
        if keep:
            frames.append(frame)
    seconds_per_frame = (perf_counter() - started) / renderer.total_frames
    config.background_reader.close()
    return (np.stack(frames) if keep else None), seconds_per_frame


@pytest.fixture(scope="module")
def opening_dear(_kernels_on_cpu, moving_clip: Path) -> np.ndarray:
    return _render(_config(moving_clip, reverse=False), cheap=False)[0]


@pytest.fixture(scope="module")
def opening_cheap(_kernels_on_cpu, moving_clip: Path) -> np.ndarray:
    return _render(_config(moving_clip, reverse=False), cheap=True)[0]


@pytest.fixture(scope="module")
def ending_dear(_kernels_on_cpu, moving_clip: Path) -> np.ndarray:
    return _render(_config(moving_clip, reverse=True), cheap=False)[0]


@pytest.fixture(scope="module")
def ending_cheap(_kernels_on_cpu, moving_clip: Path) -> np.ndarray:
    return _render(_config(moving_clip, reverse=True), cheap=True)[0]


@pytest.fixture(scope="module")
def grain_amplitude(_kernels_on_cpu, moving_clip: Path, opening_dear: np.ndarray) -> float:
    """How far the film grain alone moves a pixel, in levels of 255.

    This is the bar the optimisation has to stay under: a difference smaller
    than the grain is a difference nobody watching the title can see.
    """
    ungrained = _render(_config(moving_clip, reverse=False, noise=False), cheap=False)[0]
    return _worst_pixel(opening_dear, ungrained)


def _worst_pixel(one: np.ndarray, other: np.ndarray) -> float:
    return float(np.abs(one.astype(np.int16) - other.astype(np.int16)).max())


def test_the_grain_is_the_bar_and_it_is_a_real_one(grain_amplitude: float) -> None:
    """A grain amplitude of zero would make every bound below meaningless."""
    assert grain_amplitude > 1.0


def test_the_opening_title_is_the_same_picture(
    opening_cheap: np.ndarray, opening_dear: np.ndarray, grain_amplitude: float
) -> None:
    assert _worst_pixel(opening_cheap, opening_dear) < grain_amplitude


def test_the_ending_screen_is_the_same_picture(
    ending_cheap: np.ndarray, ending_dear: np.ndarray, grain_amplitude: float
) -> None:
    """The ending holds full blur for 86% of its frames, which is the longest
    run a held buffer gets in which to drift away from the picture."""
    assert _worst_pixel(ending_cheap, ending_dear) < grain_amplitude


def test_the_title_is_still_the_same_length(
    opening_cheap: np.ndarray,
    opening_dear: np.ndarray,
    ending_cheap: np.ndarray,
    ending_dear: np.ndarray,
) -> None:
    """A cheaper blur that dropped or repeated a frame would change the cut."""
    expected = (int(DURATION * FPS), HEIGHT, WIDTH, 3)

    assert opening_cheap.shape == opening_dear.shape == expected
    assert ending_cheap.shape == ending_dear.shape == expected


def test_the_opening_costs_less_than_half_what_it_did(_kernels_on_cpu, tmp_path: Path) -> None:
    """At 720p, where the blur radius is 72 px and the blur is 85% of a frame.

    A generous bound on purpose: the win measured on this machine is 4.6x, and
    a shared runner under load must not turn that into a red build.
    """
    clip = _clip(tmp_path, 1280, 720)
    shape = {"width": 1280, "height": 720, "duration": 1.0}
    _, cheap = _render(_config(clip, reverse=False, **shape), cheap=True, keep=False)
    _, dear = _render(_config(clip, reverse=False, **shape), cheap=False, keep=False)

    assert cheap < dear / 2, f"{cheap * 1000:.1f} ms/frame against {dear * 1000:.1f} ms/frame"
