"""Speech evidence for retained videos, projected onto the actual stitched timeline."""

from __future__ import annotations

import logging
from collections.abc import Callable

from immich_memories.processing.live_material import LiveRenderMaterial
from immich_memories.speech.cuts import safe_end, set_duration
from immich_memories.speech.facts import SpeechMeasurementUnavailable

logger = logging.getLogger(__name__)


def _merged_ranges(ranges, duration, buffer):
    merged: list[list[float]] = []
    for start, end in sorted(ranges):
        left, right = max(0.0, start - buffer), min(duration, end + buffer)
        if right <= left:
            continue
        if merged and left <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], right)
        else:
            merged.append([left, right])
    return merged


def _stitched_ranges(material: LiveRenderMaterial, regions_for):
    ranges = []
    offset = 0.0
    for entry in material.segments:
        for start, end in regions_for(entry.video_id):
            left, right = max(start, entry.start), min(end, entry.end)
            if right > left:
                ranges.append((offset + left - entry.start, offset + right - entry.start))
        offset += entry.end - entry.start
    return ranges


def resolve_speech_cuts(
    carriers: list[dict],
    regions_for: Callable[[str], list[tuple[float, float]]],
    *,
    buffer: float,
) -> list[dict]:
    """Finish the current utterance before timing fit; never extend beyond real material."""
    result = []
    for original in carriers:
        carrier = original.copy()
        if carrier["kind"] not in {"video", "live-motion"}:
            result.append(carrier)
            continue
        try:
            if carrier["kind"] == "live-motion":
                material = LiveRenderMaterial.from_dict(carrier["live_material"])
                ranges = _stitched_ranges(material, regions_for)
                duration = material.duration_seconds
            else:
                duration = carrier["raw_seconds"]
                ranges = regions_for(carrier["asset_id"])
        except SpeechMeasurementUnavailable as error:
            # WHY: the detector measured nothing for this one source. Speech
            # protection is optional refinement; the editor's selected interval
            # stands and the remaining carriers keep theirs.
            logger.warning(
                "Speech measurement unavailable for %s (%s); keeping the selected cut as is",
                carrier["asset_id"],
                error,
            )
            result.append(carrier)
            continue
        carrier["speech_regions"] = _merged_ranges(ranges, duration, buffer)
        start = carrier.get("start_time", 0.0)
        for left, right in carrier["speech_regions"]:
            if left < start < right:
                start = left
        carrier["start_time"] = start
        end = min(duration, safe_end(carrier, carrier["seconds"], expand=True))
        set_duration(carrier, end - start)
        result.append(carrier)
    return result
