"""What a photograph could show as motion, if the memory wants it.

A Live Photo is a photograph that MAY also render as motion. Modelling it as a
video instead requires a separate clips pool, suppression of the stills that
pool claims, a way to hand back the ones it refuses, and an invariant proving
none fell between the two — four mechanisms that exist only because of the
split, and between them a moment can end up shown neither way.

Here the burst is described once and attached to every photograph in it. Which
rendering ships is a later question about an asset that already won its place,
so no asset can be lost between two pools: there is only one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MotionRendering:
    """The stitched clip a burst could produce, and what assembly needs to make it."""

    video_ids: tuple[str, ...]
    trim_points: tuple[tuple[float, float], ...]
    shutter_timestamps: tuple[float, ...]
    duration_seconds: float
    still_ids: tuple[str, ...]

    # Whether the motion is worth the photograph it would replace arrives with
    # the code that chooses a rendering — duration alone, and deliberately.
    # Motion magnitude was measured on 64 real bursts: it correlates with
    # something having happened (median 2.04 against 0.48) but does not
    # separate it, since a baby's mouth closing scored 0.31 while the same
    # instant twice with a camera shift scored 0.63.


def motion_renderings(assets: list[Any], config: Any) -> dict[str, MotionRendering]:
    """The motion each photograph could show, keyed by every still in its burst.

    Keyed by every still rather than by the first, because any of them may be
    the one selection keeps and the motion belongs to all of them equally.
    """
    from immich_memories.processing.live_photo_merger import cluster_live_photos

    live = [a for a in assets if getattr(a, "live_photo_video_id", None)]
    if not live:
        return {}

    analysis = getattr(config, "analysis", None)
    window = getattr(analysis, "live_photo_merge_window_seconds", 10.0)

    found: dict[str, MotionRendering] = {}
    for cluster in cluster_live_photos(live, merge_window_seconds=window):
        rendering = MotionRendering(
            video_ids=tuple(cluster.video_asset_ids),
            trim_points=tuple(cluster.trim_points()),
            shutter_timestamps=tuple(a.file_created_at.timestamp() for a in cluster.assets),
            duration_seconds=cluster.estimated_duration,
            still_ids=tuple(a.id for a in cluster.assets),
        )
        for asset in cluster.assets:
            found[asset.id] = rendering
    return found
