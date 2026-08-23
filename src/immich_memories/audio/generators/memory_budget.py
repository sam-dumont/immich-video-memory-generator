"""Does this host have room for the ACE-Step profile that is about to load?

ACE-Step's local runtime allocates tens of gigabytes of unified memory before it produces
a single sample. When it does not fit, macOS settles the question with jetsam: the process
takes SIGKILL, and on a machine also serving a local LLM that took down the whole session,
kernel panics included (#573). Asking first turns that into an ordinary explained failure,
which the music pipeline already knows how to absorb — it falls through to the next backend,
then to a bundled track, and the video still gets made.
"""

from __future__ import annotations

import logging
import platform
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_GIB = 1024**3
_MEMORY_PROBE_TIMEOUT_SECONDS = 5

# Checkpoint sizes published in docs/create/pipeline/audio-and-music.md, "Model Cache &
# Disk Usage". The test is deliberately the *weights*, not the ~53 GB peak the same page
# reports for a full XL/4B render: most of that peak is cache the OS reclaims under
# pressure, so testing against it would refuse renders that complete today. Weights have
# to be resident for the model to run at all, which makes their total a floor — below it
# the render is not slow, it is dead.
_SHARED_WEIGHTS_BYTES = 2 * _GIB  # ACE VAE + embedding, ~1.4 GB observed
_DIT_WEIGHTS_BYTES = {"4B": 19 * _GIB, "2B": 5 * _GIB}
_LM_WEIGHTS_BYTES = {"4B": 8 * _GIB, "1.7B": 4 * _GIB, "0.6B": 2 * _GIB}
_LARGEST_LM_BYTES = max(_LM_WEIGHTS_BYTES.values())


@dataclass(frozen=True)
class MemoryShortfall:
    """What a render needs against what the host can actually give it."""

    required_bytes: int
    available_bytes: int
    profile: str

    def __str__(self) -> str:
        return (
            f"ACE-Step profile {self.profile} needs at least "
            f"{self.required_bytes / _GIB:.0f} GB of resident memory for its weights but "
            f"only {self.available_bytes / _GIB:.0f} GB is available"
        )


def _dit_size(dit_model: str) -> str:
    """ACE-Step's XL checkpoints are the 4B DiT; everything else is the 2B."""
    return "4B" if "xl" in dit_model.lower() else "2B"


def _lm_weights_bytes(lm_model: str | None) -> int:
    if lm_model is None:
        return 0
    for size, weights in _LM_WEIGHTS_BYTES.items():
        if lm_model.endswith(size):
            return weights
    return _LARGEST_LM_BYTES


def required_memory_bytes(dit_model: str, lm_model: str | None) -> int:
    """Resident weights this DiT and planner pair needs before it can produce anything."""
    return (
        _DIT_WEIGHTS_BYTES[_dit_size(dit_model)]
        + _lm_weights_bytes(lm_model)
        + _SHARED_WEIGHTS_BYTES
    )


def _macos_available_bytes() -> int | None:
    """Free plus reclaimable pages from vm_stat.

    Counts inactive, speculative, and purgeable alongside free: macOS hands all of those
    back under pressure, and leaving them out would under-report available memory badly
    enough to refuse renders that fit.
    """
    result = subprocess.run(
        ["vm_stat"],
        capture_output=True,
        text=True,
        check=False,
        timeout=_MEMORY_PROBE_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        return None

    page_size = 4096
    header, _, rest = result.stdout.partition("\n")
    if "page size of" in header:
        page_size = int(header.split("page size of")[1].split()[0])

    reclaimable = ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable")
    pages = 0
    for line in rest.splitlines():
        label, _, value = line.partition(":")
        if label.strip() in reclaimable:
            pages += int(value.strip().rstrip("."))
    return pages * page_size if pages else None


def _linux_available_bytes() -> int | None:
    with open("/proc/meminfo") as meminfo:
        for line in meminfo:
            if line.startswith("MemAvailable"):
                return int(line.split()[1]) * 1024
    return None


def available_memory_bytes() -> int | None:
    """Memory this host could still hand out, or None when it cannot be read."""
    try:
        if platform.system() == "Darwin":
            return _macos_available_bytes()
        if platform.system() == "Linux":
            return _linux_available_bytes()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        logger.debug("Could not read available memory: %s", exc)
    return None


def memory_shortfall(dit_model: str, lm_model: str | None) -> MemoryShortfall | None:
    """Report the gap when this host cannot fit the profile, else None.

    Returns None when memory cannot be read at all: an unknown figure is not evidence of
    pressure, and refusing on it would break renders that were working.
    """
    available = available_memory_bytes()
    if available is None:
        return None
    required = required_memory_bytes(dit_model, lm_model)
    if available >= required:
        return None
    lm_label = lm_model.rsplit("-", maxsplit=1)[-1] if lm_model else "no-lm"
    return MemoryShortfall(
        required_bytes=required,
        available_bytes=available,
        profile=f"{dit_model}+{lm_label}",
    )
