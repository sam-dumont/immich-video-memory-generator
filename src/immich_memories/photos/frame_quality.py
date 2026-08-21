"""Per-image measurements that separate photos the metadata score ties together.

The metadata score is a lattice: base, favorite, faces, face count, camera
EXIF. Measured on four real months it produces five to eight distinct values
for hundreds of photos, always inside 0.24-0.48, with up to 67% of a month
sharing one number. Ranking on it means taking the largest tie group and
slicing it in whatever order the API returned.

Inside one such group — 232 photos on the same 0.4267 — sharpness spans 4.1
to 60.1 and brightness 24 to 151. The pixels say plenty; nothing was asking.

These run on the thumbnail the burst-dedup pass already fetched, so they add
measurement, not I/O.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Mid-grey with room either side. Beyond this a photo is crushed or blown, and
# no amount of sharpness makes it worth showing.
_IDEAL_BRIGHTNESS = 118.0
_BRIGHTNESS_TOLERANCE = 92.0


@dataclass(frozen=True)
class FrameQuality:
    """What a thumbnail can say about itself."""

    sharpness: float
    contrast: float
    exposure: float


def measure(thumbnail: bytes) -> FrameQuality | None:
    """Measure one thumbnail, or None when it cannot be read."""
    try:
        import numpy as np
        from PIL import Image

        with Image.open(io.BytesIO(thumbnail)) as img:
            grey = np.asarray(img.convert("L"), dtype="float32")
    except Exception as exc:  # WHY: a truncated thumbnail costs this photo its
        logger.debug("Thumbnail unreadable: %s", exc)  # measurements, not the run
        return None

    if grey.size == 0 or min(grey.shape) < 3:
        return None

    import numpy as np

    # Neighbour differences stand in for a Laplacian: same signal, no scipy.
    edges = np.abs(np.diff(grey, axis=0)[:, :-1]) + np.abs(np.diff(grey, axis=1)[:-1, :])
    brightness = float(grey.mean())
    off_ideal = abs(brightness - _IDEAL_BRIGHTNESS) / _BRIGHTNESS_TOLERANCE
    return FrameQuality(
        sharpness=float(edges.var()),
        contrast=float(grey.std()),
        exposure=max(0.0, 1.0 - off_ideal),
    )


def rank(values: list[float]) -> list[float]:
    """Position of each value within the pool, 0.0 (lowest) to 1.0 (highest).

    Rank rather than the raw number because the raw scales are arbitrary and
    vary by era: a 2007 compact and a modern phone do not produce comparable
    sharpness, but "sharp for this month" is meaningful in both.
    """
    if not values:
        return []
    if len(values) == 1:
        return [1.0]
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranked = [0.0] * len(values)
    last = len(values) - 1
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        # Tied values share the middle of the span they occupy, so a tie never
        # decides an ordering by accident.
        shared = (position + end) / 2 / last
        for i in range(position, end + 1):
            ranked[order[i]] = shared
        position = end + 1
    return ranked
