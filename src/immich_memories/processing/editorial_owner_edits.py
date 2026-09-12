"""Project explicit review edits onto chosen material without selecting anything."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from immich_memories.analysis.editorial_planner import EditorialSelection
from immich_memories.processing.editorial_live_render import validate_editorial_live_clip
from immich_memories.processing.editorial_timing import (
    EditorialTimingPolicy,
    bind_editorial_timeline,
    read_editorial_timeline,
)

if TYPE_CHECKING:
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.processing.timeline_budget import TimelinePlan


@dataclass(frozen=True)
class EditorialOwnerEditProjection:
    clips: tuple[VideoClipInfo, ...]
    selections: tuple[EditorialSelection, ...]
    segments: dict[str, tuple[float, float]]
    timeline: TimelinePlan
    binding: dict
    record: dict | None


def _interval(value: Sequence[float]) -> tuple[float, float]:
    if (
        not isinstance(value, (tuple, list))
        or len(value) != 2
        or any(
            isinstance(part, bool) or not isinstance(part, (int, float)) or not math.isfinite(part)
            for part in value
        )
    ):
        raise ValueError("Review trim must contain two finite times")
    start, end = value
    if start < 0 or end <= start:
        raise ValueError("Review trim must have a positive duration and nonnegative start")
    return start, end


def _original_selections(clips, selections, binding):
    ids = binding["source_ids"]
    by_id = {clip.asset.id: clip for clip in clips}
    decisions = {row.asset_id: row for row in selections}
    if (
        len(by_id) != len(clips)
        or len(decisions) != len(selections)
        or set(ids) != set(by_id)
        or set(ids) != set(decisions)
    ):
        raise ValueError("Original editorial review material does not match its timing binding")
    for asset_id in ids:
        clip, decision = by_id[asset_id], decisions[asset_id]
        _interval((decision.start_time, decision.end_time))
        if clip.editorial_live_manifest is not None:
            validate_editorial_live_clip(clip)
            if decision.render_mode != "motion" or (
                decision.start_time,
                decision.end_time,
            ) != tuple(clip.editorial_live_manifest["selected_interval"]):
                raise ValueError("Original editorial review interval changed its Live certificate")
    return by_id, decisions


def _reviewed_scope(
    by_id: Mapping[str, VideoClipInfo],
    selected_ids: Sequence[str],
    requested_segments: Mapping[str, tuple[float, float]],
) -> set[str]:
    selected = set(selected_ids)
    if len(selected) != len(selected_ids) or not selected.issubset(by_id):
        raise ValueError("Review cannot add or duplicate material outside the chosen memory")
    if not selected:
        raise ValueError("Keep at least one picture or clip before generating")
    if not set(requested_segments).issubset(by_id):
        raise ValueError("Review trim refers to material outside the chosen memory")
    return selected


def _reviewed_row(
    clip: VideoClipInfo, decision: EditorialSelection, requested: Sequence[float]
) -> tuple[VideoClipInfo, EditorialSelection, tuple[float, float]]:
    """Apply one owner interval, keeping a still's hold and a Live certificate honest."""
    before = (decision.start_time, decision.end_time)
    interval = _interval(requested)
    if decision.render_mode == "still":
        # A still range controls its hold, never a new frame or source interval.
        interval = (0.0, interval[1] - interval[0])
    elif interval[1] > clip.duration_seconds:
        raise ValueError("Review trim exceeds the available source duration")
    if clip.editorial_live_manifest is not None:
        material = validate_editorial_live_clip(clip)
        material.displayed_interval(*interval)
        if interval != before:
            clip = clip.model_copy(
                update={
                    "editorial_live_manifest": {
                        **clip.editorial_live_manifest,
                        "selected_interval": list(interval),
                    }
                }
            )
    if interval != before:
        decision = replace(decision, start_time=interval[0], end_time=interval[1])
    return clip, decision, interval


def project_editorial_owner_edits(
    *,
    original_clips: Sequence[VideoClipInfo],
    original_selections: Sequence[EditorialSelection],
    original_binding: dict,
    selected_ids: Sequence[str],
    requested_segments: Mapping[str, tuple[float, float]],
    policy: EditorialTimingPolicy,
) -> EditorialOwnerEditProjection:
    """Rebind owner removals, intervals and settings; retain the original story order.

    This is an explicit UI boundary, never an automatic repair inside generation.
    The original selection and canonical Live material remain untouched.
    """
    original_timeline = read_editorial_timeline(original_binding)
    by_id, decisions = _original_selections(original_clips, original_selections, original_binding)
    selected = _reviewed_scope(by_id, selected_ids, requested_segments)

    clips, selections, segments, edits = [], [], {}, []
    ordered_ids = [asset_id for asset_id in original_binding["source_ids"] if asset_id in selected]
    for asset_id in ordered_ids:
        decision = decisions[asset_id]
        before = (decision.start_time, decision.end_time)
        clip, decision, interval = _reviewed_row(
            by_id[asset_id], decision, requested_segments.get(asset_id, before)
        )
        if interval != before:
            edits.append(
                {
                    "asset_id": asset_id,
                    "render_mode": decision.render_mode,
                    "original_interval": list(before),
                    "selected_interval": list(interval),
                }
            )
        clips.append(clip)
        selections.append(decision)
        segments[asset_id] = interval

    removed = [key for key in original_binding["source_ids"] if key not in selected]
    settings_changed = policy.as_dict() != original_binding["policy"]
    if not removed and not edits and not settings_changed:
        return EditorialOwnerEditProjection(
            tuple(clips),
            tuple(selections),
            segments,
            original_timeline,
            original_binding,
            None,
        )

    carriers = [{"asset_id": key, "seconds": end - start} for key, (start, end) in segments.items()]
    timeline = policy.resolve(carriers, {key: by_id[key].asset for key in ordered_ids})
    seconds = sum(row["seconds"] for row in carriers)
    if seconds > timeline.content_budget + 1e-6:
        raise ValueError(
            f"Reviewed material needs {seconds:.1f}s, but the current titles leave "
            f"{timeline.content_budget:.1f}s. Shorten your edits or increase the duration."
        )
    binding = bind_editorial_timeline(policy, timeline, ordered_ids)
    record = {
        "version": "editorial-owner-edits-v1",
        "original_timing_sha256": original_binding["sha256"],
        "result_timing_sha256": binding["sha256"],
        "removed_asset_ids": removed,
        "interval_edits": edits,
        "timing_policy_changed": settings_changed,
        "original_policy": original_binding["policy"],
        "requested_policy": policy.as_dict(),
    }
    return EditorialOwnerEditProjection(
        tuple(clips),
        tuple(selections),
        segments,
        timeline,
        binding,
        record,
    )
