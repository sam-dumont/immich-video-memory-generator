"""Final generation guards for the resolved content/title timeline."""

from __future__ import annotations

import logging
import math
from dataclasses import replace
from typing import TYPE_CHECKING

from immich_memories.generate_clips import MIN_CLIP_DURATION
from immich_memories.processing.output_contract import check_output

if TYPE_CHECKING:
    from pathlib import Path

    from immich_memories.generate import GenerationParams
    from immich_memories.processing.assembly_config import AssemblyClip
    from immich_memories.processing.encoding_plan import EncodingPlan

logger = logging.getLogger(__name__)


# Crossfades and frame rounding move the final runtime by a fraction of a second
# per clip, so a fixed one-second budget is a tighter bound on a 150s memory made
# of 30 clips than on a 20s one. Past a point an overshoot is a planning bug
# rather than jitter, and only then is discarding a finished render the right
# answer.
_DURATION_JITTER_FLOOR = 1.0
_DURATION_JITTER_RATIO = 0.02
# A floor on the hard limit too, so short memories keep a band where an
# overshoot is reported rather than thrown away.
_DURATION_HARD_FLOOR = 3.0
_DURATION_HARD_RATIO = 0.05


def _duration_limits(target: float) -> tuple[float, float]:
    """Return the tolerated limit and the limit past which we refuse the render."""
    tolerated = target + max(_DURATION_JITTER_FLOOR, target * _DURATION_JITTER_RATIO)
    return tolerated, target + max(_DURATION_HARD_FLOOR, target * _DURATION_HARD_RATIO)


def validate_final_duration(params: GenerationParams, actual_duration: float) -> str | None:
    """Check a render against its runtime budget.

    Returns a warning for an overshoot worth reporting, or None. Raises only
    when the artifact is far enough over that it indicates a planning fault --
    a finished render is expensive, and throwing one away for jitter costs the
    whole run.
    """
    target = params.target_duration_seconds
    if target is None:
        return None

    budget = target
    plan = params.timeline_plan
    if (
        plan is not None
        and plan.divider_policy == "all"
        and plan.eligible_dividers > 0
        and plan.soft_max_duration is not None
    ):
        budget = plan.soft_max_duration

    tolerated, hard_limit = _duration_limits(budget)
    if actual_duration > hard_limit:
        from immich_memories.generate import GenerationError

        raise GenerationError(
            f"Final artifact exceeds duration budget: {actual_duration:.1f}s > {hard_limit:.1f}s"
        )
    if actual_duration > tolerated:
        return (
            f"Final artifact ran {actual_duration:.1f}s against a {budget:.1f}s budget "
            f"({actual_duration - budget:.1f}s over)"
        )
    return None


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


def _assert_bound_membership(binding: dict, assembly_clips: list[AssemblyClip]) -> None:
    """Name exactly how a bound selection changed, instead of a bare mismatch."""
    from collections import Counter

    from immich_memories.processing.editorial_timing import read_editorial_timeline

    read_editorial_timeline(binding)
    expected = binding["source_ids"]
    actual = [clip.asset_id for clip in assembly_clips]
    if actual != expected:
        missing = [key for key in expected if key not in actual]
        extra = [key for key in actual if key not in expected]
        duplicated = [key for key, count in Counter(actual).items() if count > 1]
        order_changed = not (missing or extra or duplicated)
        raise ValueError(
            "Editorial selected content changed before assembly: "
            f"missing={missing}; extra={extra}; duplicated={duplicated}; "
            f"order_changed={order_changed}"
        )


def _assert_certified_interval(source, assembly_clips: list[AssemblyClip]) -> None:
    from immich_memories.processing.editorial_live_render import validate_editorial_live_clip

    asset_id = source.asset.id
    validate_editorial_live_clip(source)
    matches = [clip for clip in assembly_clips if clip.asset_id == asset_id]
    if len(matches) != 1:
        raise ValueError("Certified editorial Live source was lost or duplicated before assembly")
    assert source.editorial_live_manifest is not None
    start, end = source.editorial_live_manifest["selected_interval"]
    rendered = matches[0]
    if (
        isinstance(rendered.duration, bool)
        or not math.isfinite(rendered.duration)
        or rendered.duration != end - start
        or rendered.input_seek != 0.0
    ):
        raise ValueError("Certified editorial Live interval changed before assembly")


def validate_certified_content(
    params: GenerationParams, assembly_clips: list[AssemblyClip]
) -> set[str]:
    """Preserve bound selection membership and every certified Live interval."""
    if params.editorial_render_timing is not None:
        _assert_bound_membership(params.editorial_render_timing, assembly_clips)

    certified = [clip for clip in params.clips if clip.editorial_live_manifest is not None]
    source_ids = {clip.asset.id for clip in certified}
    if len(source_ids) != len(certified):
        raise ValueError("Certified editorial Live source is duplicated in the render request")
    for source in certified:
        _assert_certified_interval(source, assembly_clips)
    return source_ids


def apply_final_content_budget(
    params: GenerationParams,
    assembly_clips: list[AssemblyClip],
) -> list[AssemblyClip]:
    """Resolve a timeline when needed and trim every clip proportionally to its content budget."""
    certified = validate_certified_content(params, assembly_clips)
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
            expected_content_clips=len(assembly_clips),
            transition_mode=params.transition,
            transition_duration=params.transition_duration,
        )

    budget = params.timeline_plan.content_budget
    total = sum(clip.duration for clip in assembly_clips)
    if total <= budget or total <= 0.0:
        return assembly_clips
    if certified:
        raise ValueError(
            "Final timeline budget contradicts certified editorial Live intervals; "
            "the selection must be planned within the render budget"
        )

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


def check_rendered_film(
    params: GenerationParams,
    staged_path: Path,
    plan: EncodingPlan,
) -> tuple[dict[str, object], str | None]:
    """Check the rendered film's container against its plan and its runtime against the budget.

    Nothing is decoded or published here: the film is decoded once, after its
    last write. The runtime is checked now rather than after the music phase:
    it is already final (music remuxes audio with `-c:v copy`), and rejecting
    later means discarding a render that has also paid for ACE-Step, Demucs
    and the mix.
    """
    probe = check_output(staged_path, plan)
    return probe.render_metrics(plan), validate_final_duration(params, probe.duration_seconds)
