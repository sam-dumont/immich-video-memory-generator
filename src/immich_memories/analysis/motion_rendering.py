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

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Any, cast

from immich_memories.api.models import Asset
from immich_memories.processing.live_material import LiveRenderMaterial, LiveSourceEntry

# Given a burst's companion video ids in shutter order, one measured clock
# offset per join, or None where the measurement refused to commit.
ClockOffsetProbe = Callable[[Sequence[str]], "list[float | None]"]


@dataclass(frozen=True)
class MotionRendering:
    """The stitched clip a burst could produce, and what assembly needs to make it."""

    video_ids: tuple[str, ...]
    trim_points: tuple[tuple[float, float], ...]
    shutter_timestamps: tuple[float, ...]
    duration_seconds: float
    still_ids: tuple[str, ...]
    minimum_seconds: float
    material: LiveRenderMaterial | None = None

    @property
    def beats_a_still(self) -> bool:
        """Whether a STITCH is worth the photograph it would replace.

        Duration alone, and deliberately. A lone Live Photo stitches to exactly
        the raw 3.0s with nothing merged, while the smallest genuine merge of
        two reaches 4.0s, so the threshold sits between them.

        Motion magnitude is NOT part of this. Measured on 64 real bursts it
        correlates with something having happened (median 2.04 against 0.48)
        but does not separate it — a baby's mouth closing scored 0.31 while the
        same instant twice with a camera shift scored 0.63 — so a gate on it
        would drop the quiet moments a memory is for.
        """
        return self.duration_seconds >= self.minimum_seconds

    @property
    def may_play(self) -> bool:
        """Whether this rendering may be offered as motion at all.

        A join of two or more recordings has to earn its length, which is what `beats_a_still`
        asks. A lone Live Photo is not a join: it is the single recording the camera made, and
        every one of them is under the stitch minimum by construction, so that rule vetoed the
        whole kind before the motion discriminant was ever consulted. Measured over four films
        (#1066): 30 of the 38 Live Photos in the cuts were lone ones, 1.7-3.3s, none of which
        reached the residual gate; the 8 joins did, and 5 of the 6 with a measurement played.
        Whether a lone Live Photo plays is the discriminant's question, not the minimum's.
        """
        return self.beats_a_still or len(self.video_ids) <= 1


def motion_renderings(
    assets: list[Any],
    config: Any,
    *,
    companion_assets: Mapping[str, Asset] | None = None,
    clock_offsets: ClockOffsetProbe | None = None,
) -> dict[str, MotionRendering]:
    """The motion each photograph could show, keyed by every still in its burst.

    Keyed by every still rather than by the first, because any of them may be
    the one selection keeps and the motion belongs to all of them equally.

    `clock_offsets` measures how each burst's companions relate in time. When
    every join is measured, its windows are placed on the measured source clocks
    (#1012). An unmeasurable join leaves the burst's stills as photographs.
    """
    from immich_memories.processing.live_photo_merger import cluster_live_photos

    # A motion rendering belongs to a photograph. A video-typed asset with a (borrowed or
    # malformed) companion link is never Live material.
    live = [
        a
        for a in assets
        if getattr(a, "live_photo_video_id", None) and not getattr(a, "is_video", False)
    ]
    durations = None
    if companion_assets is not None:
        durations = _companion_durations(live, companion_assets)
        live = [asset for asset in live if asset.id in durations]
    if not live:
        return {}

    analysis = config.analysis
    window = analysis.live_photo_merge_window_seconds
    minimum = analysis.live_photo_min_clip_seconds

    found: dict[str, MotionRendering] = {}
    for cluster in cluster_live_photos(
        sorted(live, key=lambda asset: (asset.file_created_at, asset.id)),
        merge_window_seconds=window,
        clip_durations=durations,
    ):
        material, members = _cluster_material(cluster, clock_offsets)
        if len(members) < cluster.count:
            # Removed aliases must neither trim nor connect the surviving footage.
            # Re-cluster with the same measurements so projection reproduces the cuts.
            found.update(
                motion_renderings(
                    members,
                    config,
                    companion_assets=companion_assets,
                    clock_offsets=clock_offsets,
                )
            )
            continue
        if material is None:
            continue
        rendering = MotionRendering(
            video_ids=material.video_ids,
            trim_points=material.trim_points,
            shutter_timestamps=material.shutter_timestamps,
            duration_seconds=material.duration_seconds,
            still_ids=material.still_ids,
            minimum_seconds=minimum,
            material=material,
        )
        for asset in members:
            found[asset.id] = rendering
    return found


def _companion_durations(
    live: list[Any], companion_assets: Mapping[str, Asset]
) -> dict[str, float]:
    """The playable length each Live still's companion offers, when it offers one."""
    durations: dict[str, float] = {}
    for asset in live:
        companion = companion_assets.get(asset.live_photo_video_id)
        if companion is None:
            continue  # No motion offer; the ordinary photograph stays selectable.
        if companion.id != asset.live_photo_video_id or not companion.is_video:
            raise ValueError("Live companion metadata disagrees with its source link")
        duration = companion.duration_seconds
        if duration is None:
            continue
        if type(duration) not in {int, float} or not isfinite(duration) or duration < 0:
            raise ValueError("Live companion duration must be finite and positive")
        if duration == 0:
            continue
        durations[asset.id] = duration
    return durations


def _cluster_material(
    cluster: Any, clock_offsets: ClockOffsetProbe | None = None
) -> tuple[LiveRenderMaterial | None, list[Any]]:
    """The stitchable material of one burst, or ``None`` when it offers none.

    One picture, two files: a shared album can hold a second still of the same Live Photo,
    pointing at the same video. The video is offered once, by its earliest still; the later
    still stays an ordinary photograph. A cluster the material guard still refuses is skipped
    rather than ending the plan: its stills remain selectable, without a motion offer.

    A burst whose joins cannot all be measured is refused the same way: the
    midpoint estimate is not good enough to stitch with, so the stills stay
    photographs rather than showing a stitch that rewinds or skips (#1012).
    """
    trims = _measured_trims(cluster, clock_offsets)
    if trims is None:
        return None, list(cluster.assets)
    entries: list[LiveSourceEntry] = []
    offered_videos: set[str] = set()
    members: list[Any] = []
    for a, (start, end) in zip(cluster.assets, trims, strict=True):
        video_id = cast(str, a.live_photo_video_id)
        if video_id in offered_videos and end > start:
            continue
        try:
            entry = LiveSourceEntry(a.id, video_id, a.file_created_at.timestamp(), start, end)
        except ValueError:
            continue  # a reversed or negative interval: no motion offer, the still stays a photograph
        # An empty shutter slice retains its still alias but has not offered
        # the companion's footage; a later positive slice must still play.
        if end > start:
            offered_videos.add(video_id)
        entries.append(entry)
        members.append(a)
    try:
        return LiveRenderMaterial(tuple(entries)), members
    except ValueError:
        return None, members


def _measured_trims(
    cluster: Any, clock_offsets: ClockOffsetProbe | None
) -> list[tuple[float, float]] | None:
    """Windows re-placed by measured content clocks, or ``None`` to refuse the burst.

    Every production path injects the engine, so a multi-member burst is
    always stitched from measurement — and refused entirely when a join cannot
    be measured, because the midpoint estimate is not good enough to stitch
    with (#1012). A caller that injects no engine keeps the cluster's own
    window arithmetic; that is the unit-level planning contract, never a
    production runtime.
    """
    if clock_offsets is None:
        return cluster.trim_points()
    if cluster.count < 2 or cluster.clip_durations is None:
        return cluster.trim_points()
    from immich_memories.processing.stitch_alignment import aligned_trims

    video_ids = [cast(str, asset.live_photo_video_id) for asset in cluster.assets]
    measured = list(clock_offsets(video_ids))
    if len(measured) != len(video_ids) - 1:
        return None
    deltas: list[float] = []
    for value in measured:
        if value is None:
            return None
        deltas.append(value)
    durations = cluster.source_durations()
    aligned = aligned_trims(cluster.trim_points(), durations, deltas)
    # An alignment that cannot place every member of a genuinely short burst
    # is the same refusal: better a photograph than a stutter.
    return aligned
