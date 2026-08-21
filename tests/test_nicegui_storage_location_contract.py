"""Sessions must live on the persisted volume, not the container's root.

NiceGUI writes session storage to `.nicegui` relative to the working directory,
which in the image is `/app` -- part of the read-only root under the documented
hardening recipe. The write fails silently, `prune_user_storage` drops the
in-memory dict about 10s after the last tab closes, and the user experiences
"it keeps logging me out" with nothing in the logs.

tmpfs would fix the crash and still log everyone out on restart. The config
volume is already mounted, already writable, and already the place durable state
belongs.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_VOLUME = "/home/immich/.immich-memories"


def _dockerfile() -> str:
    return (REPO_ROOT / "docker" / "Dockerfile").read_text()


def _compose() -> dict:
    return yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())


def test_the_image_points_nicegui_storage_at_the_persisted_volume():
    """Set in the image, so it holds however the container is started."""
    match = re.search(r"ENV\s+NICEGUI_STORAGE_PATH=(\S+)", _dockerfile())

    assert match, "the image does not set NICEGUI_STORAGE_PATH"
    assert match.group(1).startswith(_CONFIG_VOLUME), (
        f"sessions would be written to {match.group(1)}, which is not on the config volume"
    )


def test_that_path_is_a_mounted_volume_in_compose():
    """A path the image writes to must be one compose actually persists."""
    service = _compose()["services"]["immich-memories"]
    mounted = [v.split(":")[1] for v in service["volumes"] if ":" in v]

    assert _CONFIG_VOLUME in mounted


def test_the_recipe_does_not_park_sessions_on_a_tmpfs():
    """tmpfs is the tempting fix; it trades a silent failure for a restart wipe.

    The hardening block is commented guidance rather than active YAML, so it is
    checked as text.
    """
    compose_text = (REPO_ROOT / "docker-compose.yml").read_text()
    hardening = compose_text[compose_text.index("Security hardening") :]

    assert "read_only: true" in hardening
    assert ".nicegui" not in hardening, "session storage belongs on a volume, not tmpfs"
