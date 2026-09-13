"""Run one CPU-only generation inside the built Docker image (#346).

Both launch blockers of 2026-08-18 — OpenCV 5 resolving into the image
(#339) and NVENC selected on GPU-less hosts (#343) — were image-only
problems the unit suite and native E2E cannot see by construction. This
gate starts the fake Immich on the host, runs `immich-memories generate`
inside the exact image the release would publish, and fails on a non-zero
exit, a missing/undecodable MP4, or a hang (hard timeout).

The story-first route needs two things a release runner has not got: a text
model to read the period with, and an annotation store already prepared for
the library. Without them `generate` stops on the blank-model guard before
anything is rendered, so the gate would fail every release for a reason that
says nothing about the image. The hermetic route from `tests/e2e/fake_editorial.py`
— the same one the launch smoke uses — is mounted into the container together
with the picture library it reads, and installed by a bootstrap that then hands
over to the real CLI. Everything downstream of selection (downloads, timing,
FFmpeg, output validation) is the production code the release would ship.

Usage: python scripts/docker_smoke.py --image <ref-or-digest> [--timeout 900]
Linux-only (uses --network host so the container reaches the host's
localhost-bound fake service).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.e2e.fake_immich import FakeImmichServer  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "e2e" / "fake_editorial.py"
LIBRARY_DIR = FIXTURE.parent / "fixtures" / "library"
SMOKE_MOUNT = "/smoke"

# Installed before the CLI is imported, so the route is already replaced by the
# time `generate` builds a pipeline. The kernel library's banner settings come
# first for the same reason they do in the CLI's own __init__.
#
# `_count_the_month` states the gate's two preconditions where the container can
# still see them: an empty pool and an empty fixture both end in a legitimate
# looking "selected no clips", which is how v0.85.0 spent a release reading a
# staging mistake as an editorial verdict.
_BOOTSTRAP = f'''import json
import os
import sys
import urllib.error
import urllib.request

os.environ.setdefault("ENABLE_QUADRANTS_HEADER_PRINT", "0")
os.environ.setdefault("QD_LOG_LEVEL", "error")
sys.path.insert(0, "{SMOKE_MOUNT}")

from tests.e2e.fake_editorial import install_fake_editorial_route
from tests.e2e.fake_library import CARRIERS, LIBRARY


def _count_the_month():
    """Refuse a pool or a fixture that cannot produce a cut, before anything selects."""
    if not LIBRARY or not CARRIERS:
        raise SystemExit(
            "smoke: the mounted fixture holds %d pictures and %d carriers; its "
            "pictures did not travel with the module" % (len(LIBRARY), len(CARRIERS))
        )
    url = os.environ.get("IMMICH_URL", "")
    request = urllib.request.Request(
        url + "/api/timeline/buckets",
        headers={{"x-api-key": os.environ.get("IMMICH_API_KEY", "")}},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            served = sum(bucket["count"] for bucket in json.load(response))
    except (OSError, ValueError) as error:
        raise SystemExit("smoke: cannot read the pool at %s: %s" % (url, error))
    if not served:
        raise SystemExit("smoke: %s serves no assets; there is nothing to select" % url)
    print("smoke: %d assets served, %d carriers scripted" % (served, len(CARRIERS)))


# WHY only for a generate: the release gate never runs anything else, and the
# contract test proves the bootstrap's imports resolve inside an image by running
# it with --help, when there is no service to count and nothing to select.
if "generate" in sys.argv:
    _count_the_month()

install_fake_editorial_route(stage_seconds=0.0)

from immich_memories.cli import main

main()
'''


def prepare_editorial_fixture(root: Path) -> Path:
    """Lay out the hermetic route, its picture library and its bootstrap to mount."""
    if not FIXTURE.is_file():
        raise FileNotFoundError(f"{FIXTURE} is missing; the smoke cannot stand up a cut")
    directory = root / "editorial"
    package = directory / "tests" / "e2e"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").touch()
    (package / "__init__.py").touch()
    # Preserve the fixture's package imports, including its shared library.
    for fixture in (FIXTURE, FIXTURE.with_name("fake_library.py")):
        shutil.copy(fixture, package / fixture.name)
    # WHY the pictures and not just the module: since #876 `fake_library` globs
    # this directory to decide which pictures exist, so the module alone builds
    # an empty month and the scripted editor keeps none of what the fake Immich
    # serves -- the release read that as "Pipeline selected no clips" (#881).
    shutil.copytree(LIBRARY_DIR, package / "fixtures" / "library")
    (directory / "smoke_bootstrap.py").write_text(_BOOTSTRAP)
    # The container runs as UID 1000 and only reads these.
    directory.chmod(0o755)
    for entry in directory.rglob("*"):
        entry.chmod(0o755 if entry.is_dir() else 0o644)
    return directory


def generate_argv(
    *,
    image: str,
    container: str,
    immich_url: str,
    api_key: str,
    out_dir: Path,
    editorial_dir: Path,
) -> list[str]:
    """The exact `docker run` that renders one monthly cut inside the image."""
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        container,
        "--network",
        "host",
        "--cpus",
        "2",
        "-e",
        f"IMMICH_URL={immich_url}",
        "-e",
        # WHY the server's own key: FakeImmichServer rejects anything
        # else with "Invalid API key", which failed every release.
        f"IMMICH_API_KEY={api_key}",
        "-v",
        f"{out_dir}:/app/output",
        "-v",
        f"{editorial_dir}:{SMOKE_MOUNT}:ro",
        image,
        "python",
        f"{SMOKE_MOUNT}/smoke_bootstrap.py",
        "generate",
        "--memory-type",
        "monthly_highlights",
        "--year",
        "2024",
        "--month",
        "6",
        "--duration",
        # WHY 60 and not the 20 the six-picture fixture used: the scripted cut is
        # 18 carriers, and a budget under MIN_CLIP_DURATION each makes the final
        # content budget sample seven of them out of a selection production has
        # already certified, which assembly refuses outright (#881). 60 is also
        # what `monthly_highlights` asks for when nobody overrides it.
        "60",
        "--no-music",
        "--output",
        "/app/output/smoke.mp4",
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    # The container runs as UID 1000 and leaves files the runner user cannot
    # delete; a cleanup failure after a PASSED smoke must not fail the release
    # (#525 follow-up: the first run to ever reach teardown died exactly there).
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        try:
            root = Path(tmp)
            out_dir = root / "output"
            out_dir.mkdir()
            out_dir.chmod(0o777)  # the container runs as UID 1000
            editorial_dir = prepare_editorial_fixture(root)
            server = FakeImmichServer.start(root / "immich")
            container = f"immich-memories-smoke-{uuid.uuid4().hex[:8]}"
            print(f"fake Immich at {server.base_url}; image {args.image}")
            try:
                proc = subprocess.run(
                    generate_argv(
                        image=args.image,
                        container=container,
                        immich_url=server.base_url,
                        api_key=server.api_key,
                        out_dir=out_dir,
                        editorial_dir=editorial_dir,
                    ),
                    timeout=args.timeout,
                    capture_output=True,
                    text=True,
                )
            except subprocess.TimeoutExpired:
                subprocess.run(["docker", "kill", container], capture_output=True)
                print(f"FAIL: generation hung past {args.timeout}s", file=sys.stderr)
                return 1
            finally:
                server.close()

            print(proc.stdout[-4000:])
            if proc.returncode != 0:
                # WHY stderr AND stdout: the CLI logs to stdout (StreamHandler +
                # Rich), so stderr alone hid the actual cause.
                print(proc.stdout[-4000:], file=sys.stderr)
                print(proc.stderr[-4000:], file=sys.stderr)
                print(f"FAIL: exit {proc.returncode}", file=sys.stderr)
                return 1

            videos = sorted(out_dir.rglob("*.mp4"))
            if not videos:
                print("FAIL: no MP4 in the output volume", file=sys.stderr)
                return 1
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "csv=p=0",
                    str(videos[0]),
                ],
                capture_output=True,
                text=True,
            )
            if probe.returncode != 0 or float(probe.stdout.strip() or 0) <= 0:
                print(f"FAIL: {videos[0].name} does not decode", file=sys.stderr)
                return 1
            print(f"OK: {videos[0].name} ({probe.stdout.strip()}s)")
            return 0
        finally:
            # WHY sudo: the container (UID 1000) owns the output tree, the
            # runner user cannot delete it, and Python 3.12's
            # ignore_cleanup_errors still re-raises EPERM from its retry path --
            # a release died on that AFTER printing "OK". GitHub runners have
            # passwordless sudo; anywhere without it, ignore_cleanup_errors
            # stays as the second line of defence.
            subprocess.run(["sudo", "rm", "-rf", str(root / "output")], check=False)


if __name__ == "__main__":
    raise SystemExit(main())
