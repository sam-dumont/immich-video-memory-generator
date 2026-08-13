"""Final generation guards for the resolved content/title timeline."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from immich_memories.generate_clips import MIN_CLIP_DURATION

if TYPE_CHECKING:
    from immich_memories.generate import GenerationParams
    from immich_memories.processing.assembly_config import AssemblyClip

logger = logging.getLogger(__name__)


def validate_final_duration(params: GenerationParams, actual_duration: float) -> None:
    """Reject a completed artifact that violates the requested final runtime."""
    target = params.target_duration_seconds
    if target is None:
        return

    limit = target + 1.0
    plan = params.timeline_plan
    if (
        plan is not None
        and plan.divider_policy == "all"
        and plan.eligible_dividers > 0
        and plan.soft_max_duration is not None
    ):
        limit = plan.soft_max_duration + 1.0

    if actual_duration > limit:
        from immich_memories.generate import GenerationError

        raise GenerationError(
            f"Final artifact exceeds duration budget: {actual_duration:.1f}s > {limit:.1f}s"
        )


def _sample_for_minimum_duration(
    clips: list[AssemblyClip],
    budget: float,
) -> list[AssemblyClip]:
    """Evenly sample a long selection so clips do not become unusably short."""
    max_clip_count = max(1, int(budget // MIN_CLIP_DURATION))
    if len(clips) <= max_clip_count:
        return clips
    if max_clip_count == 1:
        return [clips[len(clips) // 2]]
    last_index = len(clips) - 1
    indices = [round(i * last_index / (max_clip_count - 1)) for i in range(max_clip_count)]
    return [clips[index] for index in indices]


def apply_final_content_budget(
    params: GenerationParams,
    assembly_clips: list[AssemblyClip],
) -> list[AssemblyClip]:
    """Resolve a timeline when needed and trim every clip proportionally to its content budget."""
    if params.target_duration_seconds is None or not assembly_clips:
        return assembly_clips
    if params.timeline_plan is None:
        from immich_memories.generate_settings import _build_title_settings
        from immich_memories.processing.timeline_budget import plan_timeline

        title_settings = _build_title_settings(params, params.config, assembly_clips)
        params.timeline_plan = plan_timeline(
            assembly_clips,
            title_settings,
            params.target_duration_seconds,
            params.memory_type,
        )

    budget = params.timeline_plan.content_budget
    total = sum(clip.duration for clip in assembly_clips)
    if total <= budget or total <= 0.0:
        return assembly_clips

    assembly_clips = _sample_for_minimum_duration(assembly_clips, budget)
    total = sum(clip.duration for clip in assembly_clips)
    ratio = budget / total
    logger.info(
        "Trimming selected content from %.1fs to %.1fs across %d clips",
        total,
        budget,
        len(assembly_clips),
    )
    return [replace(clip, duration=clip.duration * ratio) for clip in assembly_clips]
