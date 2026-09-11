"""The generate run's timeline, before selection budgets it and after it settles.

`plan_timeline` sets the strict content budget selection must respect; once the
edit exists the editorial route may already have bound its own timing, and only
then can the final plan be read or re-budgeted.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from immich_memories.analysis.smart_pipeline import PipelineConfig
    from immich_memories.config_loader import Config
    from immich_memories.processing.assembly_config import TitleScreenSettings
    from immich_memories.processing.timeline_budget import TimelinePlan
    from immich_memories.timeperiod import DateRange

logger = logging.getLogger(__name__)


def configure_timeline(
    pipeline_config: PipelineConfig,
    *,
    clips: list,
    photo_assets: list | None,
    output_path: Path,
    config: Config,
    memory_type: str | None,
    person_names: list[str],
    date_range: DateRange,
    memory_preset_params: dict | None,
    duration: float,
    transition: str,
) -> tuple[TimelinePlan, TitleScreenSettings | None]:
    """Resolve one plan and apply its strict content budget to selection."""
    from immich_memories.generate import GenerationParams
    from immich_memories.generate_settings import _build_title_settings
    from immich_memories.processing.timeline_budget import plan_timeline

    planning_params = GenerationParams(
        clips=clips,
        output_path=output_path,
        config=config,
        memory_type=memory_type,
        person_name=person_names[0] if person_names else None,
        date_start=date_range.start,
        date_end=date_range.end,
        memory_preset_params=memory_preset_params or {},
    )
    planning_titles = _build_title_settings(planning_params, config, [])
    planning_sources = [*clips, *(list(photo_assets) if photo_assets else [])]
    timeline = plan_timeline(
        planning_sources,
        planning_titles,
        duration,
        memory_type,
        expected_clip_duration=pipeline_config.avg_clip_duration,
        transition_mode=transition,
        transition_duration=config.defaults.transition_duration,
    )
    pipeline_config.target_duration_seconds = timeline.content_budget
    pipeline_config.target_clips = max(
        1,
        math.ceil(timeline.content_budget / pipeline_config.avg_clip_duration),
    )
    return timeline, planning_titles


def final_timeline(
    timeline_plan: TimelinePlan,
    *,
    timing_binding: dict | None,
    selected_clips: list,
    clip_segments: dict,
    planning_titles: TitleScreenSettings | None,
    memory_type: str | None,
    transition: str,
    config: Config,
) -> TimelinePlan:
    """Adopt the editorial timing when the route bound one; otherwise re-budget."""
    selected_duration = sum(end - start for start, end in clip_segments.values())
    if timing_binding is not None:
        from immich_memories.processing.editorial_timing import read_editorial_timeline

        plan = read_editorial_timeline(timing_binding)
    else:
        from immich_memories.processing.timeline_budget import finalize_selected_timeline

        plan = finalize_selected_timeline(
            timeline_plan,
            selected_clips,
            selected_duration=selected_duration,
            title_settings=planning_titles,
            memory_type=memory_type,
            transition_mode=transition,
            transition_duration=config.defaults.transition_duration,
        )
    if plan.divider_policy in {"all", "none"}:
        logger.info(
            "Final timeline: month dividers=%s (%d/%d), %.1fs estimated, %.1fs soft maximum",
            plan.divider_policy,
            plan.max_dividers,
            plan.eligible_dividers,
            min(selected_duration, plan.content_budget)
            + plan.title_budget
            - plan.transition_budget,
            plan.soft_max_duration,
        )
    else:
        logger.info(
            "Final timeline: %.1fs content + %.1fs titles (%d dividers capped)",
            selected_duration,
            plan.title_budget,
            plan.max_dividers,
        )
    return plan
