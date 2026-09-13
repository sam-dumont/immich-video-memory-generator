"""Which kernel library the title renderer was asked for — without loading it.

Separate from gpu_kernel_backend.py, which answers the same question by actually
importing the library. Preflight has to name the right package in "install this
and titles get the GPU" without paying for the import (Quadrants costs ~0.8 s to
load), and that is the whole reason this file exists.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

TAICHI = "taichi"
QUADRANTS = "quadrants"
KERNEL_BACKENDS = (TAICHI, QUADRANTS)

KERNEL_BACKEND_ENV_VAR = "IMMICH_MEMORIES_TITLE_KERNELS"

# The extra that installs each library, for messages that tell a user what to do.
KERNEL_BACKEND_EXTRAS = {TAICHI: "gpu", QUADRANTS: "titles-quadrants"}


def _configured_kernel_backend() -> str:
    """Read the config key, treating any failure to read it as "not set".

    Imported here rather than at module scope: the config package is far heavier
    than this choice, and a config that cannot be read is not a reason for titles
    to stop rendering.
    """
    try:
        from immich_memories.config import get_config

        return str(get_config().hardware.title_kernel_backend)
    except Exception as exc:  # noqa: BLE001 — any unreadable config means "default"
        logger.debug("No configured title kernel backend (%s); using Taichi", type(exc).__name__)
        return TAICHI


def requested_kernel_backend() -> str:
    """The kernel library this process was asked for, before checking it exists."""
    asked = os.environ.get(KERNEL_BACKEND_ENV_VAR, "").strip().lower()
    if not asked:
        return _configured_kernel_backend()
    if asked in KERNEL_BACKENDS:
        return asked
    logger.warning(
        "%s=%r is not a title kernel backend (%s); using %s",
        KERNEL_BACKEND_ENV_VAR,
        asked,
        ", ".join(KERNEL_BACKENDS),
        TAICHI,
    )
    return TAICHI
