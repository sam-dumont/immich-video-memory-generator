"""Run one CPU-only generation inside the built Docker image (#346).

Both launch blockers of 2026-08-18 — OpenCV 5 resolving into the image
(#339) and NVENC selected on GPU-less hosts (#343) — were image-only
problems the unit suite and native E2E cannot see by construction. This
gate starts the fake Immich on the host, runs `immich-memories generate`
inside the exact image the release would publish, and fails on a non-zero
exit, a missing/undecodable MP4, or a hang (hard timeout).

Usage: python scripts/docker_smoke.py --image <ref-or-digest> [--timeout 900]
Linux-only (uses --network host so the container reaches the host's
localhost-bound fake service).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.e2e.fake_immich import FakeImmichServer  # noqa: E402


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
            server = FakeImmichServer.start(root / "immich")
            container = f"immich-memories-smoke-{uuid.uuid4().hex[:8]}"
            print(f"fake Immich at {server.base_url}; image {args.image}")
            try:
                proc = subprocess.run(
                    [
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
                        f"IMMICH_URL={server.base_url}",
                        "-e",
                        # WHY the server's own key: FakeImmichServer rejects anything
                        # else with "Invalid API key", which failed every release.
                        f"IMMICH_API_KEY={server.api_key}",
                        "-v",
                        f"{out_dir}:/app/output",
                        args.image,
                        "immich-memories",
                        "generate",
                        "--memory-type",
                        "monthly_highlights",
                        "--year",
                        "2024",
                        "--month",
                        "6",
                        "--duration",
                        "20",
                        "--no-music",
                        "--output",
                        "/app/output/smoke.mp4",
                    ],
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
