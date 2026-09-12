"""Video probing and metadata extraction utilities."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.processing.probe_cache import ProbeCache, VideoProbe

logger = logging.getLogger(__name__)


def _source_probe(video_path: Path, probe_cache: ProbeCache | None) -> VideoProbe:
    from immich_memories.processing.probe_cache import ProbeCache

    return (probe_cache or ProbeCache()).get(video_path)


def get_video_duration(video_path: Path, *, probe_cache: ProbeCache | None = None) -> float:
    """Return format duration, optionally reusing a caller-owned run cache."""
    from immich_memories.processing.probe_cache import ProbeError

    try:
        return _source_probe(video_path, probe_cache).duration_seconds
    except ProbeError as exc:
        logger.error("FFprobe error: %s", exc)
        return 0.0


def get_main_video_stream_map(video_path: Path, *, probe_cache: ProbeCache | None = None) -> str:
    """Find the main (highest-resolution) video stream in a file.

    iPhone Live Photo videos can embed a depth map as stream 0
    (512x512, 1fps, hevc). This function probes all video streams
    and returns the ffmpeg -map argument for the largest one.

    Returns:
        ffmpeg map string like "0:v:0" or "0:1" for use with -map.
    """
    with contextlib.suppress(Exception):
        probe = _source_probe(video_path, probe_cache)
        if probe.video_stream_index:
            logger.debug(
                "Selected video stream %d (%dx%d)",
                probe.video_stream_index,
                probe.width,
                probe.height,
            )
            return f"0:{probe.video_stream_index}"
    return "0:v:0"
