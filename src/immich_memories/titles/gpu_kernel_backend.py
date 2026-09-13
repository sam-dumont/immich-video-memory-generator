"""The one place this project imports its GPU kernel library.

Every title kernel compiles against Quadrants, and this module is the single
`import quadrants as ti` that gives it to them. Two things it owns:

* **The optional-dependency guard.** Quadrants publishes no wheel for macOS
  x86_64 or Python 3.14, so `ti` is None there and the title renderer falls back
  to PIL, with a line in the log saying why. Nothing else in the package may
  import the library directly: `KERNELS_AVAILABLE` is the answer to "is there a
  GPU renderer here".
* **The banner env vars.** Quadrants prints a version banner to stdout as its
  C++ runtime loads, which corrupts a Rich Live display. Importing this module
  is what sets the variables that silence it, so it has to be the first thing
  any kernel-touching module pulls in.

"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

KERNEL_LIBRARY = "quadrants"

_SILENT_BANNERS = {
    "ENABLE_QUADRANTS_HEADER_PRINT": "0",
    "QD_LOG_LEVEL": "error",
}


def _silence_kernel_banners() -> None:
    """Set the library's quiet-mode variables before its runtime loads."""
    for name, value in _SILENT_BANNERS.items():
        os.environ.setdefault(name, value)


_silence_kernel_banners()

try:
    import quadrants  # noqa: I001 — must follow the env vars set above

    # `ti` is deliberately Any. The kernel modules write `ti.f32` and
    # `ti.types.ndarray(...)` in annotation position, which type-checks only
    # against the Any an unstubbed library produces. `ti` is the alias because
    # that is the name Quadrants' own API docs and every kernel signature use.
    ti: Any = quadrants
    KERNELS_AVAILABLE = True
except ModuleNotFoundError as _exc:
    # Not an error: no wheel for this platform (macOS x86_64, Python 3.14) means
    # PIL titles, which is a poorer picture and not a missing feature. A library
    # that is installed but cannot load its own runtime is a different thing and
    # is left to raise.
    if _exc.name != KERNEL_LIBRARY:
        raise
    logger.info(
        "%s is not installed (%s); title screens use the PIL renderer",
        KERNEL_LIBRARY,
        _exc,
    )
    ti = None
    KERNELS_AVAILABLE = False
