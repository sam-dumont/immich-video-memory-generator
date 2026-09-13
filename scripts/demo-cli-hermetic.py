#!/usr/bin/env python3
"""Run the real CLI against the hermetic fakes, for the demo's terminal recording.

Nothing here is simulated: `immich-memories generate`, `runs story` and
`runs why` run in this process, the same way the e2e tests run them, against
the fake Immich server and the scripted editorial route from `tests/e2e`. The
pictures are the six CC0 fixture photographs; the memory is the fixture's June.

VHS records the terminal (`make demo-cli`); the Remotion `CliScene` plays it.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# The people roster resolves through the home directory, so the recording must
# not read the developer's own. Every provider override goes too.
_HOME = Path(tempfile.mkdtemp(prefix="demo-cli-home-"))
for key in list(os.environ):
    if key.startswith(("IMMICH_MEMORIES_", "NICEGUI_")):
        del os.environ[key]
os.environ.update(
    {
        "HOME": str(_HOME),
        "USERPROFILE": str(_HOME),
        "IMMICH_MEMORIES_AUTH__ENABLED": "false",
        "IMMICH_MEMORIES_STORAGE_SECRET": "demo-recording-storage-secret",
        "TI_LOG_LEVEL": "error",
    }
)

import yaml  # noqa: E402
from tests.e2e.fake_editorial import install_fake_editorial_route  # noqa: E402
from tests.e2e.fake_immich import FakeImmichServer  # noqa: E402

import immich_memories.config_loader as config_loader  # noqa: E402

# Slow enough for the counted stage to last a few seconds, so the bar and the
# remaining-time estimate are on screen long enough to read.
STAGE_SECONDS = 0.9


def _prompt(*words: str) -> None:
    print()
    print("\033[1;32m❯\033[0m " + " ".join(words))


def _run(argv: list[str]) -> None:
    from immich_memories.cli import main

    sys.argv = ["immich-memories", *argv]
    try:
        main()
    except SystemExit as exit_:  # Click always exits; a non-zero code is the real signal.
        if exit_.code not in (None, 0):
            raise


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="demo-cli-"))
    server = FakeImmichServer.start(root / "fake-immich")
    try:
        cache = root / "cache"
        cache.mkdir()
        # Where a person would put it: the recording prints ~/Videos/june-2024.mp4.
        out = _HOME / "Videos"
        out.mkdir()
        (root / "state").mkdir()
        config_path = root / "config.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "immich": {
                        "url": server.base_url,
                        "api_key": server.api_key,
                        "api_version": "auto",
                    },
                    "output": {
                        "directory": str(out),
                        "format": "mp4",
                        "resolution": "720p",
                        "codec": "h264",
                        "hdr_mode": "sdr",
                        "quality": "low",
                    },
                    "cache": {
                        "directory": str(cache),
                        "database": str(cache / "demo.db"),
                        "video_cache_enabled": True,
                        "video_cache_max_size_gb": 1,
                        "video_cache_max_age_days": 1,
                    },
                    "upload": {"enabled": False},
                    "photos": {"enabled": True},
                    "advanced": {
                        "hardware": {"enabled": False, "backend": "none", "gpu_decode": False},
                        "musicgen": {"enabled": False},
                        "ace_step": {"enabled": False},
                    },
                },
                sort_keys=False,
            )
        )
        config_loader.Config.get_default_path = classmethod(lambda _cls: config_path)  # type: ignore[method-assign]
        config_loader.init_config_dir = lambda: root / "state"  # type: ignore[assignment]
        install_fake_editorial_route(stage_seconds=STAGE_SECONDS)

        _prompt(
            "immich-memories generate --memory-type monthly_highlights",
            "--year 2024 --month 6 --no-music --output ~/Videos/june-2024.mp4",
        )
        _run(
            [
                "generate",
                "--memory-type",
                "monthly_highlights",
                "--year",
                "2024",
                "--month",
                "6",
                "--no-music",
                "--output",
                str(out / "june-2024.mp4"),
            ]
        )

        _prompt("immich-memories runs story")
        _run(["runs", "story"])

        plans = sorted(
            cache.glob("editorial-runs/*/attempts/*/plan.private.json"),
            key=lambda p: p.stat().st_mtime,
        )
        carriers = json.loads(plans[-1].read_text()).get("carriers") or [] if plans else []
        if carriers:
            asset = carriers[0]["asset_id"]
            _prompt("immich-memories runs why", asset)
            _run(["runs", "why", asset])
        # Typed, not run: VHS cannot show a video player. The demo cuts from
        # this line to the film itself. The name is the file the run wrote.
        written = sorted(out.rglob("*.mp4"), key=lambda p: p.stat().st_mtime)
        if written:
            _prompt("open", "~/" + str(written[-1].relative_to(_HOME)))
    finally:
        server.close()


if __name__ == "__main__":
    main()
