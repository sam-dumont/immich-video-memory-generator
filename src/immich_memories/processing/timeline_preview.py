"""Lay out a film without rendering its cards or opening media files."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    TitleScreenSettings,
    TransitionType,
)
from immich_memories.processing.assembly_engine import decide_transitions
from immich_memories.processing.timeline_budget import TimelinePlan
from immich_memories.processing.title_divider_planner import TitleDividerPlanner
from immich_memories.titles.generator import GeneratedScreen


class _PreviewCards:
    """The divider planner only needs card paths; previews never open them."""

    def generate_month_divider(
        self, month: int, year: int | None = None, is_birthday_month: bool = False
    ) -> GeneratedScreen:
        return GeneratedScreen(Path(), 0, "month_divider")

    def generate_year_divider(self, year: int) -> GeneratedScreen:
        return GeneratedScreen(Path(), 0, "year_divider")

    def generate_location_card_screen(
        self, location_name: str, lat: float | None = None, lon: float | None = None
    ) -> GeneratedScreen:
        return GeneratedScreen(Path(), 0, "location")


def preview_timeline(
    clips: list[AssemblyClip],
    plan: TimelinePlan,
    titles: TitleScreenSettings,
    transition: str,
    transition_duration: float,
) -> tuple[dict[str, tuple[float, float]], float]:
    """Content starts and holds, plus film length, using the assembler's boundary policy."""
    total = sum(clip.duration for clip in clips)
    ratio = min(1.0, plan.content_budget / total) if total > 0 else 1.0
    content = [replace(clip, duration=clip.duration * ratio) for clip in clips]
    settings = replace(
        titles, month_divider_duration=plan.divider_duration, max_dividers=plan.max_dividers
    )
    sequence = TitleDividerPlanner(_PreviewCards(), settings).select_divider_strategy(
        content, None, titles.memory_type == "trip"
    )
    content_backed = titles.title_background == "content_backed"
    if plan.title_duration > 0:
        map_intro = titles.memory_type == "trip" and any(c.latitude is not None for c in content)
        sequence.insert(
            0,
            AssemblyClip(
                Path(),
                plan.title_duration,
                asset_id="title_screen",
                is_title_screen=True,
                outgoing_transition="cut" if content_backed and not map_intro else None,
            ),
        )
    if plan.ending_duration > 0:
        if sequence and content_backed:
            sequence[-1] = replace(sequence[-1], outgoing_transition="cut")
        sequence.append(
            AssemblyClip(
                Path(), plan.ending_duration, asset_id="ending_screen", is_title_screen=True
            )
        )
    transitions = decide_transitions(sequence, TransitionType(transition), transition_duration)
    positions = {}
    start = 0.0
    for index, clip in enumerate(sequence):
        if not clip.is_title_screen:
            positions[clip.asset_id] = (start, clip.duration)
        start += clip.duration
        if index < len(transitions) and transitions[index] == "fade":
            start -= transition_duration
    return positions, max(0.0, start)
