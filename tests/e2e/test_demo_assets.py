"""Cut the docs demo's output clip on the hermetic launch.

The demo's "and here is the video it made" scene used to play a real family
video with every face blurred. That is a privacy problem, and blurred it was
also a poor advertisement -- half the demo was unreadable mush. This renders
the same scene out of the fixture library instead: the real product, driven
through the real browser flow, over the CC0 fixture library (one household's June).

Run it with `make demo-output` after the fixture library or the renderer
changes, then re-render the demo with `make demo-ui`. It is kept out of every
automatic suite by its own marker, because it renders a second video into the
session workspace and the launch smoke counts what is in there.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.fake_library import THESIS
from tests.e2e.test_launch_smoke import _choose

pytestmark = [pytest.mark.e2e, pytest.mark.visual, pytest.mark.demo, pytest.mark.slow]

_PREVIEW = "output-preview.mp4"
_POSTER = "output-frame.jpg"
# Far enough in that the poster shows a picture rather than the opening title.
_POSTER_FRACTION = 0.45


@pytest.fixture
def demo_public_dir() -> Path:
    """Where the Remotion demo reads its static assets from."""
    return Path(__file__).resolve().parents[2] / "docs-site" / "remotion" / "public"


def _render_at_1080p(page: Page, launch_app_url: str) -> None:
    page.goto(launch_app_url, wait_until="domcontentloaded", timeout=30_000)
    expect(page.get_by_role("combobox", name="Memory type")).to_be_visible(timeout=30_000)
    _choose(page, "Memory type", "Monthly Highlights")
    _choose(page, "Month", "June")
    page.get_by_role("button", name="Cut", exact=True).click()

    expect(page.get_by_text(THESIS)).to_be_visible(timeout=180_000)
    page.get_by_role("button", name="Export", exact=True).click()
    page.wait_for_url("**/step4", timeout=30_000)
    page.get_by_role("button", name="Back to Generation Options").click()
    page.wait_for_url("**/step3", timeout=30_000)

    _choose(page, "Resolution", "1080p")
    _choose(page, "Output Format", "MP4 (H.264)")
    # WHY none: the demo composition lays its own track over this clip and mutes
    # the video, and a hermetic launch has no music provider to ask anyway.
    _choose(page, "Background music", "None")
    page.get_by_role("button", name="Next: Preview & Export").click()
    page.get_by_role("button", name="Generate Video").click()
    expect(page.get_by_text("Your memory video is ready!", exact=True)).to_be_visible(
        timeout=900_000
    )


def _poster_from(video: Path, destination: Path) -> None:
    duration = subprocess.run(  # noqa: S603
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(video),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(  # noqa: S603
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(round(float(duration) * _POSTER_FRACTION, 2)),
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            "-update",
            "1",
            str(destination),
        ],
        check=True,
        capture_output=True,
    )


def test_cut_the_demo_output_clip(
    page: Page, launch_app_url: str, launch_workspace, demo_public_dir: Path
) -> None:
    """One real 1080p memory over the fixture library, saved for the demo to play."""
    before = set(launch_workspace.output_dir.rglob("*.mp4"))

    _render_at_1080p(page, launch_app_url)

    rendered = set(launch_workspace.output_dir.rglob("*.mp4")) - before
    assert len(rendered) == 1, f"expected one new render, found {sorted(rendered)}"
    video = rendered.pop()
    shutil.copyfile(video, demo_public_dir / _PREVIEW)
    _poster_from(video, demo_public_dir / _POSTER)
