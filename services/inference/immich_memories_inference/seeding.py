"""Put the pinned artifacts on a cold cache volume, from the app's own pin table.

A fresh PVC arrives empty, and `kubectl cp` is not a deployment step: with
downloads allowed the service fetches what it is missing on first use, from the
same URLs and digests `immich-memories models fetch` reads. With downloads off
it fetches nothing and the loader's own message stands.
"""

from __future__ import annotations

import logging
from pathlib import Path

from immich_memories.pinned_models import PinnedModel, fetch_pinned_model

logger = logging.getLogger(__name__)


class SeedFailed(RuntimeError):
    """The artifact was absent, downloads were allowed, and the fetch did not work."""


def seed(pin: PinnedModel, destination: Path, *, allow_downloads: bool) -> None:
    """Fetch ``pin`` to ``destination`` when it is absent and downloads are allowed.

    A file that is already there is left alone whatever it hashes to: the loaders
    verify the digest themselves, and re-downloading 88 MB on every reload to
    learn what they are about to tell you is not a check, it is a bill.
    """
    if destination.is_file() or not allow_downloads:
        return
    logger.info("inference: fetching %s into %s", pin.label, destination)
    try:
        fetch_pinned_model(url=pin.url, destination=destination, sha256=pin.sha256)
    except (OSError, ValueError) as exc:
        raise SeedFailed(f"{pin.label} could not be fetched from {pin.url}: {exc}") from exc
    logger.info("inference: %s is now at %s", pin.label, destination)
