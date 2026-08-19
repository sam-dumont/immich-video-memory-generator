"""Royalty-free background music bundled for offline use.

Shipped as a separate distribution so ``pip install immich-memories`` stays small
while the Docker image, which installs the ``music`` extra, has music out of the box.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["tracks_dir"]


def tracks_dir() -> Path:
    """Directory of bundled tracks, one folder per mood."""
    return Path(__file__).parent / "tracks"
