"""A real video's measured motion, read off the frames the exposure head already sampled.

A Live Photo's motion sentence counts only once its clip measured motion (#1118), but a real
video counted as moving by what it is: a caption of an action on a clip of an empty room was
trusted as evidence. The residual is the Live Photo one (median-flow optical flow with the
camera's own motion taken away), run on the eight frames `detector_frames` samples across the
clip for the exposure and frame heads. No playback is fetched for it.

The frames sit seconds apart rather than a twelfth of a Live Photo apart, so the number is
banked under its own producer and never answers for a Live Photo's, nor the other way round.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing
from pathlib import Path

from immich_memories.analysis.editorial_bound_sample import source_metadata_digest
from immich_memories.analysis.editorial_motion_facts import flow_residual
from immich_memories.api.models import Asset
from immich_memories.store.cut_measurements import (
    banked_motion_residuals,
    open_cut_measurements,
    remember_motion_residual,
)

VIDEO_METHOD = "median-flow-v1-detector-frames-320x240"
VIDEO_RESIDUAL_PRODUCER = f"motion-residual-v1@{VIDEO_METHOD}"
STAGE = "video_motion"


def measure_frame_motion(paths: Sequence[Path]) -> dict:
    """The residual over these frames, in order; a frame that cannot be decoded is skipped."""
    import cv2

    frames = []
    for path in paths:
        image = cv2.imread(str(path))
        if image is not None:
            frames.append(cv2.cvtColor(cv2.resize(image, (320, 240)), cv2.COLOR_BGR2GRAY))
    return flow_residual(frames)


def videos_owing_motion(
    connection: sqlite3.Connection, assets: Sequence[Asset], video_ids: frozenset[str]
) -> dict[str, Asset]:
    """The videos among these that no pass has measured yet in their current version, by id."""
    videos = [asset for asset in assets if asset.id in video_ids]
    digests = {video.id: source_metadata_digest(video) for video in videos}
    banked = banked_motion_residuals(connection, digests, VIDEO_RESIDUAL_PRODUCER)
    return {video.id: video for video in videos if video.id not in banked}


def bank_video_motion(
    *,
    store_path: Path,
    videos: Mapping[str, Asset],
    frame_paths: Mapping[str, Sequence[Path]],
) -> dict[str, str]:
    """Bank each video's residual over its sampled frames, with what it was measured on.

    Returns the videos whose frames could not be measured, with why. They stay unmeasured,
    which is what every video was before: their motion is still taken on what they are.
    """
    failures: dict[str, str] = {}
    with closing(open_cut_measurements(store_path)) as connection:
        for asset_id, paths in frame_paths.items():
            fact = measure_frame_motion(paths)
            if "residual" not in fact:
                failures[asset_id] = f"{fact.get('frames', 0)} of {len(paths)} frames readable"
                continue
            remember_motion_residual(
                connection,
                asset_id=asset_id,
                producer=VIDEO_RESIDUAL_PRODUCER,
                source_digest=source_metadata_digest(videos[asset_id]),
                measured=fact
                | {"producer": VIDEO_RESIDUAL_PRODUCER, "sampled_by": "detector_frames"},
            )
    return failures
