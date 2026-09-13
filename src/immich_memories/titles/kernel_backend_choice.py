"""Which kernel library the title renderer was asked for — without loading it.

Separate from gpu_kernel_backend.py, which answers the same question by actually
importing the library. Preflight has to name the right package in "install this
and titles get the GPU" without paying for the import (a kernel library costs
~0.2-0.8 s to load), and that is the whole reason this file exists.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

AUTO = "auto"
TAICHI = "taichi"
QUADRANTS = "quadrants"
KERNEL_BACKENDS = (AUTO, TAICHI, QUADRANTS)

KERNEL_BACKEND_ENV_VAR = "IMMICH_MEMORIES_TITLE_KERNELS"

# What `auto` tries, in order. Quadrants first because it is the maintained one
# and the only one with a linux-aarch64 wheel; Taichi is what the renderer ran
# on for its whole life and stays a working answer.
AUTO_PREFERENCE = (QUADRANTS, TAICHI)

# How to get each library back when it is the one missing. Quadrants ships in
# the base install, so its answer is a reinstall rather than an extra.
INSTALL_HINTS = {
    TAICHI: "pip install 'immich-memories[gpu]'",
    QUADRANTS: "pip install --upgrade immich-memories",
}


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
        logger.debug(
            "No configured title kernel backend (%s); choosing %s", type(exc).__name__, AUTO
        )
        return AUTO


def requested_kernel_backend() -> str:
    """The kernel library this process was asked for, before checking it exists.

    `auto` is an answer, not a failure to answer: it means "the best one that is
    installed", which gpu_kernel_backend resolves against AUTO_PREFERENCE.
    """
    asked = os.environ.get(KERNEL_BACKEND_ENV_VAR, "").strip().lower()
    if not asked:
        return _configured_kernel_backend()
    if asked in KERNEL_BACKENDS:
        return asked
    logger.warning(
        "%s=%r is not a title kernel backend (%s); choosing %s",
        KERNEL_BACKEND_ENV_VAR,
        asked,
        ", ".join(KERNEL_BACKENDS),
        AUTO,
    )
    return AUTO


def preference_order(requested: str) -> tuple[str, ...]:
    """The libraries to try, best first, for one value of the flag.

    An explicit choice still falls back to the other one: a config that names a
    library the machine does not have should render titles, not lose them.
    """
    if requested == AUTO:
        return AUTO_PREFERENCE
    return (requested, *(name for name in AUTO_PREFERENCE if name != requested))
