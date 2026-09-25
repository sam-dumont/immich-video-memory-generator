"""Where the public E2E library keeps what never goes in git: the index, fetched files, snapshots."""

from __future__ import annotations

import os
from pathlib import Path

REPO_HOUSEHOLDS = Path(__file__).parent / "households"
# A generic agent string: every fetch identifies the project, never a person.
USER_AGENT = (
    "immich-memories-public-e2e/1.0 (+https://github.com/sam-dumont/immich-video-memory-generator)"
)


def work_root() -> Path:
    """The maintainer's working folder, `PUBLIC_E2E_HOME` or ~/.immich-memories-public-e2e."""
    root = Path(os.environ.get("PUBLIC_E2E_HOME", Path.home() / ".immich-memories-public-e2e"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def household_dir(name: str) -> Path:
    return REPO_HOUSEHOLDS / name


def household_work(name: str) -> Path:
    path = work_root() / "households" / name
    path.mkdir(parents=True, exist_ok=True)
    return path
