"""Which kernel library the title renderer compiles against.

Taichi 1.7.4 is terminal upstream. Quadrants is Genesis AI's live fork of it and
exposes every `ti.*` symbol these kernels use, so swapping one for the other is
a single import — this module is that import. Nothing else in the package names
either distribution; they all take `ti` from here.

Two things this module owns that callers must not work around:

* **Only one of the two may ever be imported.** Both link their own LLVM and
  register the same command-line options with it, so importing the second one
  aborts the interpreter outright ("Option already exists!") rather than raising.
  Anything that wants to know whether the GPU renderer is available must ask
  `KERNEL_LIBRARY_AVAILABLE` here, never `import taichi` on its own.
* **The banner env vars.** Both libraries print a version banner to stdout as
  their C++ runtime loads, which corrupts a Rich Live display (taichi#8334).
  Importing this module is what sets the variables that silence them, so it has
  to be the first thing any kernel-touching module pulls in.

Which one is asked for is decided in kernel_backend_choice.py, which answers
that without importing anything. Taichi is the default and the fallback: an
install that asked for Quadrants and does not have it renders titles exactly as
before, with a line in the log saying so.
"""

import importlib
import logging
import os
from types import ModuleType
from typing import Any

from .kernel_backend_choice import (
    KERNEL_BACKEND_ENV_VAR,
    KERNEL_BACKEND_EXTRAS,
    TAICHI,
    requested_kernel_backend,
)

logger = logging.getLogger(__name__)

_SILENT_BANNERS = {
    "ENABLE_TAICHI_HEADER_PRINT": "0",
    "TI_LOG_LEVEL": "error",
    "ENABLE_QUADRANTS_HEADER_PRINT": "0",
    "QD_LOG_LEVEL": "error",
}


def _silence_kernel_banners() -> None:
    """Set both libraries' quiet-mode variables before either runtime loads."""
    for name, value in _SILENT_BANNERS.items():
        os.environ.setdefault(name, value)


def load_kernel_library(requested: str) -> tuple[ModuleType | None, str | None]:
    """Import the requested kernel library, or Taichi, or say neither is here.

    Returns the module and the name of what was actually loaded. `(None, None)`
    means no kernel library is installed at all, which is not an error: the
    title renderer falls back to PIL. An installed-but-broken library raises,
    because that is a machine to fix rather than a feature to skip.
    """
    for name in dict.fromkeys((requested, TAICHI)):
        try:
            return importlib.import_module(name), name
        except ModuleNotFoundError as exc:
            # Only "this distribution is not here" is a fallback. A library that
            # is installed but cannot load its own runtime is a broken machine,
            # and quietly rendering titles on the CPU would hide it.
            if exc.name != name:
                raise
            _log_missing_library(name, requested, exc)
    return None, None


def _log_missing_library(name: str, requested: str, exc: ModuleNotFoundError) -> None:
    """Say what was asked for and what it costs, once, at the right level."""
    if name != requested:
        logger.debug("Neither %s nor %s is installed (%s)", requested, name, exc)
        return
    if name == TAICHI:
        logger.debug("Taichi is not installed (%s); titles use the PIL renderer", exc)
        return
    logger.warning(
        "Title kernel backend %r is not installed (%s); falling back to %s. "
        "Install it with pip install 'immich-memories[%s]'",
        requested,
        exc,
        TAICHI,
        KERNEL_BACKEND_EXTRAS[requested],
    )


def _pin_backend_for_child_processes(name: str) -> None:
    """Make every child of this process load the same library the parent did.

    The backend probe runs in a child interpreter, and a child that re-read the
    config could pick the other library and then probe something the parent will
    never run.
    """
    os.environ[KERNEL_BACKEND_ENV_VAR] = name


_silence_kernel_banners()

# `ti` is deliberately Any. The kernel modules write `ti.f32` and
# `ti.types.ndarray(...)` in annotation position — legal only because a kernel
# library has no stubs, which is exactly what `import taichi as ti` used to give
# them. Typing this as ModuleType would make every kernel signature an error.
ti: Any
ti, KERNEL_BACKEND = load_kernel_library(requested_kernel_backend())
KERNEL_LIBRARY_AVAILABLE = ti is not None

if KERNEL_BACKEND is not None:
    _pin_backend_for_child_processes(KERNEL_BACKEND)
