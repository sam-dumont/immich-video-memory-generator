"""Translate the final selected cut into the worker's path-free render envelope."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import uuid4

from immich_memories.api.models import AssetType
from immich_memories.processing.editorial_timing import (
    bind_editorial_timeline,
    prepare_certified_timeline,
    timing_policy_for_params,
)
from immich_memories.processing.output_canvas import resolve_generation_canvas

if TYPE_CHECKING:
    from immich_memories.generate import GenerationParams


def build_render_request(params: GenerationParams) -> dict:
    """Freeze the kept intervals without running selection or reading any media."""
    from immich_memories.generate import build_memory_key
    from immich_memories.generate_clips import _validated_render_directives
    from immich_memories.processing.encoding_plan import resolve_output_selection

    output = resolve_output_selection(
        config_codec=params.config.output.codec,
        config_container=params.config.output.format,
        format_override=params.output_format,
    )
    if output.container != "mp4":
        raise ValueError("Render workers support H.264 or H.265 MP4; use local rendering for MOV")
    prepare_certified_timeline(params)
    directives = _validated_render_directives(params)
    clips = [_clip_request(clip, params, directives.get(clip.asset.id)) for clip in params.clips]
    params, binding = _timing_binding(params, clips)
    canvas = resolve_generation_canvas(params)
    titles = params.config.title_screens.model_dump()
    return {
        "version": 1,
        "render_attempt": str(uuid4()),
        "memory_key": build_memory_key(params) or binding["sha256"],
        "immich": {"url": params.config.immich.url, "api_key": params.config.immich.api_key},
        "plan": {
            "clips": clips,
            "transition": params.transition,
            "transition_duration": params.transition_duration,
        },
        "memory": {
            "memory_type": params.memory_type,
            "target_duration_seconds": params.target_duration_seconds,
            "date_start": params.date_start.isoformat() if params.date_start else None,
            "date_end": params.date_end.isoformat() if params.date_end else None,
            "person_name": params.person_name,
            "preset_params": params.memory_preset_params,
        },
        "titles": titles | {"title": params.title or "", "subtitle": params.subtitle or ""},
        "timing": binding,
        "certified_content_intervals": {
            clip["asset_id"]: clip["live"]["selected_interval"]
            for clip in clips
            if clip["live"] is not None
        },
        "output": {
            "codec": output.codec.value,
            "resolution": {720: "720p", 1080: "1080p", 2160: "4k"}[
                min(canvas.width, canvas.height)
            ],
            "orientation": canvas.orientation,
            "crf": params.output_crf
            if params.output_crf is not None
            else params.config.output.effective_crf,
            "hdr_mode": params.config.output.hdr_mode.value,
            "codec_policy": params.config.output.codec_policy,
            "quality": params.config.output.quality,
        },
        "options": {
            "scale_mode": params.scale_mode or params.config.defaults.scale_mode,
            "add_date_overlay": params.add_date_overlay,
            "add_place_overlay": params.add_place_overlay,
            "privacy_mode": params.privacy_mode,
            "photo_duration": params.config.photos.duration,
            "homebase_latitude": params.config.trips.homebase_latitude,
            "homebase_longitude": params.config.trips.homebase_longitude,
        },
    }


def _live_certificate(clip, mode: str, start: float, end: float) -> dict | None:
    if clip.editorial_live_manifest is not None:
        return clip.editorial_live_manifest
    if mode != "motion" or clip.asset.type != AssetType.IMAGE:
        return None
    from immich_memories.processing.editorial_live_render import (
        RENDER_VERSION,
        validate_editorial_live_clip,
    )

    certificate = {
        "version": RENDER_VERSION,
        "material": clip.live_burst_material,
        "selected_interval": [start, end],
    }
    validate_editorial_live_clip(clip.model_copy(update={"editorial_live_manifest": certificate}))
    return certificate


def _clip_request(clip, params, directive) -> dict:
    still = clip.asset.type == AssetType.IMAGE and not clip.live_burst_video_ids
    duration = params.config.photos.duration if still else (clip.duration_seconds or 5.0)
    interval = (
        directive.start_time if directive and directive.start_time is not None else 0.0,
        directive.end_time if directive and directive.end_time is not None else duration,
    )
    start, end = params.clip_segments.get(clip.asset.id, interval)
    mode = (directive.render_mode if directive else None) or ("still" if still else "motion")
    return {
        "asset_id": clip.asset.id,
        "start": start,
        "end": end,
        "render_mode": mode,
        "render_frame_seconds": directive.render_frame_seconds if directive else None,
        "live": _live_certificate(clip, mode, start, end),
        "rotation_override": params.clip_rotations.get(clip.asset.id),
        "audio_categories": clip.audio_categories,
        "llm_emotion": clip.llm_emotion,
    }


def _timing_binding(params, clips: list[dict]):
    if params.target_duration_seconds is None:
        title_config = params.config.title_screens
        overhead = (
            (
                title_config.title_duration
                + title_config.ending_duration
                + max(0, len(clips) - 1) * title_config.month_divider_duration
            )
            if title_config.enabled
            else 0.0
        )
        # An unbudgeted manual film keeps its holds and full title timings. The
        # planner reserves at most 20% for titles, so fund that reserve as well.
        params = replace(
            params,
            target_duration_seconds=max(
                sum(row["end"] - row["start"] for row in clips) + overhead,
                overhead * 5,
            ),
        )
    binding = params.editorial_render_timing
    if binding is None:
        policy = timing_policy_for_params(params)
        timeline = policy.resolve(
            [{"asset_id": row["asset_id"], "seconds": row["end"] - row["start"]} for row in clips],
            {clip.asset.id: clip for clip in params.clips},
        )
        binding = bind_editorial_timeline(
            policy, timeline, [clip.asset.id for clip in params.clips]
        )
    return params, binding
