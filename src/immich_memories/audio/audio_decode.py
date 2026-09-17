"""Decode a media file to a mono float32 numpy array via ffmpeg.

One decoder shared by the two numpy-only analysis passes (tempo and quality).
librosa would be one line, but it is not a dependency of this project: it
arrives transitively with the torch extras, so importing it would fail
`make dep-check` and break a plain install that has music but no GPU stack.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np


def decode_mono_float(path: Path, sample_rate: int = 22050) -> np.ndarray | None:
    """Samples of ``path`` as mono float32 at ``sample_rate``, or None when unreadable."""
    proc = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    return np.frombuffer(proc.stdout, dtype=np.float32)
