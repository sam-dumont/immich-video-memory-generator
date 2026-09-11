"""Record the Memory page walkthrough as one video, on the hermetic launch.

One .webm under docs-site/static/demo/raw/ (gitignored): the brief, the cut in
progress, the story it produced, and Export. It shows the flow in motion for a
reviewer; the docs demo itself is the Remotion recreation under
docs-site/remotion, never a screen recording. Part of `make e2e-full`.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
from playwright.sync_api import BrowserContext, Page, Playwright, expect

from tests.e2e.redaction import redact_page
from tests.e2e.test_launch_smoke import _choose

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

_VIDEO_SIZE = {"width": 1440, "height": 900}
_THESIS = re.compile(r"^A month of short test-pattern captures")


def _recording_context(playwright: Playwright, raw_dir: Path) -> BrowserContext:
    browser = playwright.chromium.launch()
    return browser.new_context(
        viewport=_VIDEO_SIZE, record_video_dir=str(raw_dir), record_video_size=_VIDEO_SIZE
    )


def _smooth_scroll(page: Page, total: int, step: int = 40, delay: int = 60) -> None:
    for _ in range(0, total, step):
        page.mouse.wheel(0, step)
        page.wait_for_timeout(delay)


def _save_recording(context: BrowserContext, page: Page, raw_dir: Path, name: str) -> None:
    """Close the page to finalize the recording, then give it the segment's name."""
    page.close()
    video_path = page.video.path() if page.video else None
    context.close()
    if video_path and Path(video_path).exists():
        shutil.move(str(video_path), str(raw_dir / f"{name}.webm"))


def test_record_memory_walkthrough(
    playwright: Playwright, launch_app_url: str, demo_raw_dir: Path
) -> None:
    """Brief, Advanced, Cut, the story, Export -- one take, real pauses between clicks."""
    context = _recording_context(playwright, demo_raw_dir)
    page = context.new_page()

    page.goto(launch_app_url, wait_until="domcontentloaded", timeout=30_000)
    expect(page.get_by_role("combobox", name="Memory type")).to_be_visible(timeout=30_000)
    page.wait_for_timeout(1200)
    _choose(page, "Memory type", "Monthly Highlights")
    page.wait_for_timeout(800)
    _choose(page, "Month", "June")
    page.wait_for_timeout(1200)

    advanced = page.get_by_text("Advanced", exact=True)
    advanced.click()
    expect(page.get_by_role("button", name="Open the media pool")).to_be_visible()
    redact_page(page)
    page.wait_for_timeout(2000)
    advanced.click()
    page.wait_for_timeout(800)

    page.get_by_role("button", name="Cut", exact=True).click()
    expect(page.get_by_text(_THESIS)).to_be_visible(timeout=120_000)
    page.wait_for_timeout(2000)
    _smooth_scroll(page, 600)
    page.wait_for_timeout(1500)
    page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
    page.wait_for_timeout(1200)

    page.get_by_role("button", name="Export", exact=True).click()
    page.wait_for_url("**/step4", timeout=30_000)
    expect(page.get_by_role("button", name="Generate Video")).to_be_visible(timeout=30_000)
    # WHY: the output line names a pytest temp root that carries the developer's user name.
    redact_page(page)
    page.wait_for_timeout(2500)

    _save_recording(context, page, demo_raw_dir, "memory-walkthrough")
