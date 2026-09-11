"""The story a cut tells, read from the attempt's plan and nothing else.

This is the only reader of `plan.private.json` on the UI side. It turns the
planner's record into a frozen view the page renders: the thesis, the stories
in the order the memory weighs them, each story's carriers in capture order,
and the one reason the editor wrote for every carrier.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from immich_memories.analysis.editorial_story_replies import WEIGHTS

PLAN_FILE = "plan.private.json"

# Carrier kinds the planner writes for moving pictures; anything else is held as a still.
MOTION_KINDS = frozenset({"video", "live-motion", "motion"})

# The editor answers in its own vocabulary; readers get these words instead. Display only —
# `StoryEntry.weight` keeps the stored word for the Details disclosure. A weight with no
# reader word (`none`, or anything a later schema adds) gets no badge rather than a raw one.
WEIGHT_LABELS = {
    "dominant": "Main story",
    "major": "Important",
    "minor": "Supporting",
    "glimpse": "Small moment",
}


@dataclass(frozen=True)
class CarrierView:
    asset_id: str
    seconds: float
    taken: str
    reason: str
    standing: str
    motion: bool

    @property
    def render_label(self) -> str:
        return "Motion" if self.motion else "Still"


@dataclass(frozen=True)
class StoryEntry:
    key: str
    title: str
    weight: str
    purpose: str
    granted: int
    day: str
    carriers: tuple[CarrierView, ...]

    @property
    def weight_label(self) -> str:
        return WEIGHT_LABELS.get(self.weight, "")

    @property
    def granted_label(self) -> str:
        return f"{self.granted} picture{'' if self.granted == 1 else 's'}"


@dataclass(frozen=True)
class DurationView:
    requested_seconds: float
    content_budget_seconds: float
    selected_content_seconds: float
    status: str

    @property
    def line(self) -> str:
        """The one sentence that replaces the four counters."""
        return (
            f"{self.selected_content_seconds:.0f} s of pictures and video selected for a "
            f"{self.requested_seconds:.0f} s memory, {self.content_budget_seconds:.0f} s of it "
            f"available for content — {self.status.replace('_', ' ')}"
        )


@dataclass(frozen=True)
class StoryView:
    thesis: str
    stories: tuple[StoryEntry, ...]
    duration: DurationView | None

    @property
    def carrier_count(self) -> int:
        return sum(len(story.carriers) for story in self.stories)


def _reason(why: object, title: str) -> str:
    text = str(why or "").strip()
    prefix = f"{title}: "
    return text[len(prefix) :] if title and text.startswith(prefix) else text


def _carrier(row: Mapping[str, Any], title: str, render_modes: Mapping[str, str]) -> CarrierView:
    asset_id = str(row.get("asset_id", ""))
    mode = render_modes.get(asset_id)
    motion = mode == "motion" if mode else str(row.get("kind", "")) in MOTION_KINDS
    return CarrierView(
        asset_id=asset_id,
        seconds=float(row.get("seconds") or 0.0),
        taken=str(row.get("taken") or ""),
        reason=_reason(row.get("why"), title),
        standing=str(row.get("standing") or ""),
        motion=motion,
    )


def _weight_rank(weight: str) -> int:
    return WEIGHTS.index(weight) if weight in WEIGHTS else len(WEIGHTS)


def _duration(realization: object) -> DurationView | None:
    if not isinstance(realization, Mapping):
        return None
    try:
        return DurationView(
            requested_seconds=float(realization["requested_seconds"]),
            content_budget_seconds=float(realization["content_budget_seconds"]),
            selected_content_seconds=float(realization["selected_content_seconds"]),
            status=str(realization.get("status") or "unknown"),
        )
    except (KeyError, TypeError, ValueError):
        return None


def story_view_from_plan(
    plan: Mapping[str, Any], render_modes: Mapping[str, str] | None = None
) -> StoryView:
    """Read the planner's plan dict into the view the story page renders.

    `render_modes` maps asset ids to the final-cut render mode ("motion" or
    "still") when the run's selections carry one; a carrier without an entry
    falls back to what its planner kind implies.
    """
    modes = render_modes or {}
    story = plan.get("story") or {}
    by_episode: dict[str, list[Mapping[str, Any]]] = {}
    for row in plan.get("carriers") or ():
        by_episode.setdefault(str(row.get("story_episode") or ""), []).append(row)

    entries = []
    for episode in story.get("episodes") or ():
        key = str(episode.get("episode") or episode.get("key") or "")
        title = str(episode.get("title") or key)
        rows = sorted(by_episode.get(key, ()), key=lambda row: str(row.get("taken") or ""))
        carriers = tuple(_carrier(row, title, modes) for row in rows)
        day = str(episode.get("day") or (carriers[0].taken[:10] if carriers else ""))
        granted = episode.get("granted")
        entries.append(
            StoryEntry(
                key=key,
                title=title,
                weight=str(episode.get("weight") or ""),
                purpose=str(episode.get("purpose") or ""),
                granted=int(granted) if granted is not None else len(carriers),
                day=day,
                carriers=carriers,
            )
        )
    entries.sort(key=lambda entry: (_weight_rank(entry.weight), entry.day))
    return StoryView(
        thesis=str(story.get("thesis") or ""),
        stories=tuple(entries),
        duration=_duration(plan.get("duration_realization")),
    )


def read_story_view(
    attempt_dir: Path, render_modes: Mapping[str, str] | None = None
) -> StoryView | None:
    """The view for a finished attempt, or None when it left no plan behind."""
    plan_path = Path(attempt_dir) / PLAN_FILE
    if not plan_path.is_file():
        return None
    return story_view_from_plan(json.loads(plan_path.read_text()), render_modes)
