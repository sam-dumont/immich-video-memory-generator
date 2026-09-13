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

The container's output is streamed to the job log as it arrives, and the phases
it names are timed. Run 34769467426 (v0.85.1) captured that output instead and
printed it only at the end, so a 1200 s timeout reported nothing but the fact
that 1200 s had passed (#896).

Usage: python scripts/docker_smoke.py --image <ref-or-digest> [--timeout 900]
Linux-only (uses --network host so the container reaches the host's
localhost-bound fake service).
"""

from __future__ import annotations

import argparse
import contextlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.e2e.fake_immich import FakeImmichServer  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "e2e" / "fake_editorial.py"
LIBRARY_DIR = FIXTURE.parent / "fixtures" / "library"
SMOKE_MOUNT = "/smoke"

# How much of the container's log a failure repeats at the bottom of the job,
# where someone reading a red release sees it without scrolling.
TAIL_LINES = 60

# The lines the container prints on entering a phase, in the order it reaches
# them. First sight of one starts that phase; the gate reports what each cost,
# and a timeout names the one it died in.
PHASE_MARKERS: tuple[str, ...] = (
    "Connecting to Immich",
    "Preparing previews",
    "Reading dates, places and people",
    "Reading event evidence",
    "Reading the period account",
    "Building editorial cards",
    "Editing the memory",
    "Editorial selection complete",
    "Downloading clips",
    "Clips downloaded",
    "Rendering memory",
    "Generating title screens",
    "Video saved to:",
)

# What the gate asks the image for, and why each is free to ask. On trial here
# is the image -- that its OpenCV, FFmpeg, encoders and title kernels resolve
# and cut a real month -- not a point on the quality curve.
#
# Both levers aim at the phase that actually costs the release: rendering, and
# inside it the title screens -- 331 s of the 570 s that run 34769467426's own
# arm64 image took on a 2-CPU cap here, to make 10.5 seconds of video.
#
# 720p is 44% of 1080p's pixels. `quality: fast` keeps the balanced picture and
# buys its speed from the encoder effort preset (libx264 `veryfast` rather than
# `medium`), which is the only thing that tier trades; `hardware.encoder_preset`
# carries the same answer to the hardware encoders, so a runner that grows a GPU
# does not quietly go back to `medium`.
#
# Nothing here touches `editorial.preparation`. The mounted route replaces the
# pipeline builder, so no tier's producers ever run: the 133 previews of a
# release smoke are scripted and cost 0.15 s end to end.
PINNED_CONFIG: dict[str, str] = {
    "IMMICH_MEMORIES_OUTPUT__RESOLUTION": "720p",
    "IMMICH_MEMORIES_OUTPUT__QUALITY": "fast",
    "IMMICH_MEMORIES_HARDWARE__ENCODER_PRESET": "fast",
}

PINNED_HEIGHT = PINNED_CONFIG["IMMICH_MEMORIES_OUTPUT__RESOLUTION"].removesuffix("p")

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
    pinned = [arg for name, value in PINNED_CONFIG.items() for arg in ("-e", f"{name}={value}")]
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
        "-e",
        # WHY: the CLI's own prints otherwise sit in a pipe buffer, and a
        # streamed log that arrives in one lump at the end is the captured log
        # this gate just stopped having.
        "PYTHONUNBUFFERED=1",
        *pinned,
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


@dataclass(frozen=True)
class ContainerRun:
    """What the container did, as the job log saw it happen."""

    returncode: int
    elapsed: float
    tail: tuple[str, ...]
    phases: tuple[tuple[str, float], ...]
    timed_out: bool

    @property
    def last_phase(self) -> str:
        """The phase the container was in when it stopped."""
        return self.phases[-1][0] if self.phases else "no phase reached"


def _phase_of(line: str) -> str | None:
    return next((marker for marker in PHASE_MARKERS if marker in line), None)


def stream_container(argv: list[str], *, container: str, timeout: int) -> ContainerRun:
    """Run the container, echoing each line as it arrives and timing the phases.

    The job log is the only witness a failed release leaves behind, so nothing
    here waits for the process to exit before printing.
    """
    started = time.monotonic()
    tail: deque[str] = deque(maxlen=TAIL_LINES)
    phases: list[tuple[str, float]] = []
    expired = threading.Event()
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )

    def _stop() -> None:
        expired.set()
        # Killing the client alone leaves the container running on the daemon;
        # killing only the container can leave the client waiting. Both, in
        # order, and tolerant of a host with no docker on PATH.
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(["docker", "kill", container], capture_output=True, timeout=60)  # noqa: S607
        proc.kill()

    watchdog = threading.Timer(timeout, _stop)
    watchdog.start()
    try:
        for line in proc.stdout or ():
            elapsed = time.monotonic() - started
            print(f"[{elapsed:7.1f}s] {line}", end="", flush=True)
            tail.append(line.rstrip("\n"))
            phase = _phase_of(line)
            if phase is not None and phase not in {name for name, _ in phases}:
                phases.append((phase, elapsed))
        returncode = proc.wait()
    finally:
        watchdog.cancel()
    return ContainerRun(
        returncode=returncode,
        elapsed=time.monotonic() - started,
        tail=tuple(tail),
        phases=tuple(phases),
        timed_out=expired.is_set(),
    )


def print_phase_timings(run: ContainerRun) -> None:
    """Print each phase the container named, when it started and what it cost."""
    if not run.phases:
        print(f"\nphases: the container named none in {run.elapsed:.1f}s")
        return
    print(f"\nphases over {run.elapsed:.1f}s (reached at, then what it cost)")
    ends = [at for _, at in run.phases[1:]] + [run.elapsed]
    for (name, at), end in zip(run.phases, ends, strict=True):
        print(f"  {at:7.1f}s  {name:<34}{end - at:7.1f}s")


def report_failure(run: ContainerRun, timeout: int) -> None:
    """Repeat the container's last lines and name the phase it stopped in."""
    print("\n".join(run.tail), file=sys.stderr)
    if run.timed_out:
        print(f"FAIL: generation hung past {timeout}s in '{run.last_phase}'", file=sys.stderr)
    else:
        print(f"FAIL: exit {run.returncode} in '{run.last_phase}'", file=sys.stderr)


def _verify(video: Path) -> int:
    """Check the cut decodes and came back at the size the gate pinned."""
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=height",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1",
            str(video),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    probed = dict(line.split("=", 1) for line in probe.stdout.splitlines() if "=" in line)
    if probe.returncode != 0 or float(probed.get("duration") or 0) <= 0:
        print(f"FAIL: {video.name} does not decode", file=sys.stderr)
        return 1
    if probed.get("height") != PINNED_HEIGHT:
        # A pin the loader has stopped reading is how this gate would quietly go
        # back to costing a release twenty minutes.
        print(
            f"FAIL: {video.name} came back {probed.get('height')} lines tall; "
            f"the pinned {PINNED_HEIGHT}p never reached the container",
            file=sys.stderr,
        )
        return 1
    print(f"OK: {video.name} ({float(probed['duration']):.1f}s, {PINNED_HEIGHT}p)")
    return 0


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
                run = stream_container(
                    generate_argv(
                        image=args.image,
                        container=container,
                        immich_url=server.base_url,
                        api_key=server.api_key,
                        out_dir=out_dir,
                        editorial_dir=editorial_dir,
                    ),
                    container=container,
                    timeout=args.timeout,
                )
            finally:
                server.close()

            print_phase_timings(run)
            if run.timed_out or run.returncode != 0:
                report_failure(run, args.timeout)
                return 1

            videos = sorted(out_dir.rglob("*.mp4"))
            if not videos:
                print("FAIL: no MP4 in the output volume", file=sys.stderr)
                return 1
            return _verify(videos[0])
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
