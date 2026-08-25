"""What happens to a Live Photo on its way into the pool.

Almost nothing, now. A Live Photo is a photograph: its still arrives with the
photographs and competes as one, and whether the burst is worth showing as
motion is a rendering question asked later
(analysis/motion_rendering.py). All this has to do is keep the video half out
of the video pool, where it would compete as footage against its own still.

Shared between CLI and UI — no NiceGUI imports allowed here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.api.models import Asset

logger = logging.getLogger(__name__)


def drop_live_photo_components(videos: list[Asset], photos: list[Asset]) -> list[Asset]:
    """Remove the video half of a Live Photo from the video pool.

    A Live Photo's video component is part of a photograph, not footage
    somebody shot. Left in the pool it competes as a video against the still it
    belongs to, and the same instant can ship twice.

    The exclusion set comes from the photographs themselves, so nothing has to
    be fetched twice to learn it.
    """
    components = {p.live_photo_video_id for p in photos if p.live_photo_video_id}
    if not components:
        return videos
    kept = [v for v in videos if v.id not in components]
    if len(kept) != len(videos):
        logger.info(
            "Live Photos: %d video components dropped from the video pool",
            len(videos) - len(kept),
        )
    return kept
