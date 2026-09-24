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
from PIL import Image, ImageStat
from playwright.sync_api import Page, expect

from tests.e2e.conftest import _REPO_ROOT, _build_launch_environment
from tests.e2e.fake_library import CARRIERS, THESIS, summary_line
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
    # The Remotion owner unticks the first picture and cuts again before export.
    page.get_by_role("button", name="Review the pool", exact=True).click()
    box = page.get_by_role("checkbox", name="Include").first
    expect(box).to_be_checked()
    box.click()
    expect(box).not_to_be_checked()
    page.get_by_role("button", name="Cut again", exact=True).click()
    tab = page.get_by_role("tab", name="Story", exact=True)
    expect(tab).to_be_visible(timeout=120_000)
    tab.click()
    expected = summary_line().replace(f"{len(CARRIERS)} pictures", f"{len(CARRIERS) - 1} pictures")
    expect(page.get_by_text(expected, exact=True)).to_be_visible(timeout=120_000)
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


# The trip film the trip docs page plays. It is cut by the real CLI, not the
# browser: the Memory page detects trips from videos only, and the fixture's
# lake week photographs four of its seven days, so the page finds nothing to
# offer. `immich-memories generate --memory-type trip` reads every asset type.
_TRIP_CLIP = "trip-preview.mp4"
_TRIP_POSTER = "trip-map-flyover.jpg"
# Inside the opening title, which runs 3.5 s at the default: late enough that
# the map has finished travelling and both pins are on the lake, which is the
# still the trip page shows.
_FLYOVER_SECONDS = 3.0
_TRIP_MAX_BYTES = 8 * 1024 * 1024
# CRF 26 measured 6.3 MB against the 10.5 MB the renderer writes at quality
# low. The docs site serves this file to every reader of the trip page.
_TRIP_WEB_CRF = "26"

_TRIP_CLI_BOOTSTRAP = """
import sys
from pathlib import Path

import immich_memories.config_loader as config_loader

config_path = Path(sys.argv[1])
state_dir = Path(sys.argv[2])
config_loader.Config.get_default_path = classmethod(lambda cls: config_path)
config_loader.init_config_dir = lambda: state_dir

from tests.e2e.fake_editorial import install_fake_editorial_route

install_fake_editorial_route(stage_seconds=0.05)

from immich_memories.cli import main

sys.argv = ["immich-memories", *sys.argv[3:]]
main()
"""


@pytest.fixture
def demo_static_dir() -> Path:
    """Where the docs site serves the demo's own videos from."""
    return Path(__file__).resolve().parents[2] / "docs-site" / "static" / "demo"


@pytest.fixture
def demo_image_dir() -> Path:
    """Where the docs site serves the demo's own stills from."""
    return Path(__file__).resolve().parents[2] / "docs-site" / "static" / "img"


def _duration_of(video: Path) -> float:
    return float(
        subprocess.run(  # noqa: S603
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
    )


def _frame_at(video: Path, seconds: float, destination: Path) -> None:
    subprocess.run(  # noqa: S603
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(seconds),
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


def _encode_for_the_web(source: Path, destination: Path) -> None:
    """Re-encode the render for a docs page: same picture, a size a page can carry."""
    subprocess.run(  # noqa: S603
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-c:v",
            "libx264",
            "-crf",
            _TRIP_WEB_CRF,
            "-preset",
            "slow",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(destination),
        ],
        check=True,
        capture_output=True,
    )


def _trip_environment(home: Path) -> dict[str, str]:
    # WHY: a default run makes no outside call (#1037), so without these two
    # opt-ins the trip opens on its title over the first picture, not the map.
    env = _build_launch_environment(home)
    env["IMMICH_MEMORIES_NETWORK__GEOCODING"] = "true"
    env["IMMICH_MEMORIES_NETWORK__MAP_TILES"] = "true"
    return env


def _run_trip_cli(workspace, output_dir: Path) -> Path:
    """Cut the fixture's lake week through the real CLI, and return what it wrote."""
    state_dir = workspace.root / "state"
    state_dir.mkdir(exist_ok=True)
    completed = subprocess.run(  # noqa: S603
        [
            str(_REPO_ROOT / ".venv" / "bin" / "python"),
            "-c",
            _TRIP_CLI_BOOTSTRAP,
            str(workspace.config_path),
            str(state_dir),
            "generate",
            "--memory-type",
            "trip",
            "--year",
            "2024",
            "--trip-index",
            "1",
            "--no-music",
            "--quiet",
            "--output",
            str(output_dir / "trip.mp4"),
        ],
        cwd=_REPO_ROOT,
        env=_trip_environment(workspace.root),
        capture_output=True,
        text=True,
        timeout=1800,
    )
    rendered = sorted(output_dir.rglob("*.mp4"))
    assert completed.returncode == 0, (
        f"the trip CLI exited {completed.returncode}:\n{completed.stdout[-4000:]}"
        f"\n{completed.stderr[-4000:]}"
    )
    assert len(rendered) == 1, f"expected one trip render, found {rendered}"
    return rendered[0]


def test_cut_the_trip_memory_and_its_map(
    launch_workspace, tmp_path: Path, demo_static_dir: Path, demo_image_dir: Path
) -> None:
    """The fixture's week by the lake, cut as a trip, opening on its map fly-over.

    Needs the network: the fly-over's satellite tiles come from ArcGIS World
    Imagery and the trip's name from Nominatim, and neither has an offline
    stand-in. Without them the opening is a flat blue panel and the assertion
    on the fly-over frame is what says so.
    """
    output_dir = tmp_path / "trip-output"
    output_dir.mkdir()

    rendered = _run_trip_cli(launch_workspace, output_dir)

    clip = demo_static_dir / _TRIP_CLIP
    _encode_for_the_web(rendered, clip)
    poster = demo_image_dir / _TRIP_POSTER
    _frame_at(clip, _FLYOVER_SECONDS, poster)

    duration = _duration_of(clip)
    assert 10.0 < duration < 40.0, f"the trip film runs {duration:.1f}s"
    assert clip.stat().st_size <= _TRIP_MAX_BYTES, f"{clip.name} is {clip.stat().st_size} bytes"

    flyover = Image.open(poster).convert("L")
    assert ImageStat.Stat(flyover).mean[0] > 20, "the fly-over frame is black"
    assert ImageStat.Stat(flyover).stddev[0] > 10, "the fly-over frame is a flat panel"
