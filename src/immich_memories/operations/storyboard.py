"""The cut in shot order, read from an attempt directory: what the video will play.

The story view ranks stories by weight, which is how the editor thinks. The video
plays in capture order, which is how the viewer sees it. This module reads the
`plan.private.json` every attempt writes, lays the renderer's intervals from
`render-projection.private.json` over it, and yields one shot per carrier in the
order they will play: capture day, the story each was granted to, the moment it
depicts, its length, and a running timecode. Lengths and timecodes are the
film's, not the plan's: the renderer squeezes the granted seconds into the
timeline's content budget and the opening card plays before the first picture,
so a shot is timed and placed the way it will be watched. A month change gets a chapter
divider, the way the title planner puts one divider per month shown; a day change
under one story title is marked so a loose grouping reads as one.

It lives outside the UI package because the terminal reads the same record:
`runs story` prints it, and the end-of-run summary prints its first lines.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from immich_memories.processing.timeline_budget import TimelinePlan, estimate_film_duration

PLAN_FILE = "plan.private.json"
PROJECTION_FILE = "render-projection.private.json"
TRACE_FILE = "selection-trace.private.json"

# Carrier kinds the planner writes for moving pictures; anything else is held as a still.
MOTION_KINDS = frozenset({"video", "live-motion", "motion"})


def carrier_reason(why: object, title: str) -> str:
    """The editor's reason for a carrier, without the story title it prefixed."""
    text = str(why or "").strip()
    prefix = f"{title}: "
    return text[len(prefix) :] if title and text.startswith(prefix) else text


@dataclass(frozen=True)
class Shot:
    asset_id: str
    taken: str
    day: str
    story_key: str
    story_title: str
    moment: str
    seconds: float
    start: float
    motion: bool
    new_day: bool
    chapter: str
    reason: str

    @property
    def timecode(self) -> str:
        return _clock(self.start)

    @property
    def kind_label(self) -> str:
        """The badge word: a moving picture is a Video, everything else is held as a Still."""
        return "Video" if self.motion else "Still"


@dataclass(frozen=True)
class Storyboard:
    thesis: str
    shots: tuple[Shot, ...]
    film_seconds: float | None = None

    @property
    def total_seconds(self) -> float:
        return round(sum(shot.seconds for shot in self.shots), 2)

    @property
    def total_label(self) -> str:
        return _clock(self.total_seconds)

    @property
    def summary_label(self) -> str:
        """The pictures and video, and beside them the whole film the title cards make.

        The film's length is an estimate while the file does not exist: smart
        transitions draw their overlap boundary by boundary.
        """
        count = len(self.shots)
        noun = "picture" if count == 1 else "pictures"
        pictures = f"{count} {noun}, {self.total_label} of pictures and video"
        if self.film_seconds is None:
            return pictures
        return f"{pictures}, about {_clock(self.film_seconds)} of film"


def _clock(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def _month(taken: str) -> str:
    try:
        return datetime.fromisoformat(taken).strftime("%B %Y")
    except ValueError:
        return ""


def _interval_seconds(projection: Mapping[str, Any] | None, asset_id: str) -> float | None:
    intervals = (projection or {}).get("intervals") or {}
    bounds = intervals.get(asset_id)
    if not isinstance(bounds, list | tuple) or len(bounds) != 2:
        return None
    try:
        return round(float(bounds[1]) - float(bounds[0]), 2)
    except (TypeError, ValueError):
        return None


def _recorded_timeline(plan: Mapping[str, Any]) -> TimelinePlan | None:
    timeline = (plan.get("render_timing") or {}).get("timeline")
    if not isinstance(timeline, Mapping):
        return None
    try:
        return TimelinePlan(**timeline)
    except TypeError:
        # A record written against a timeline this version no longer knows: the
        # shot order is still readable, so report it untimed rather than nothing.
        return None


def _content_budget(plan: Mapping[str, Any], timeline: TimelinePlan | None) -> float | None:
    recorded = (plan.get("duration_realization") or {}).get(
        "content_budget_seconds", plan.get("content_cap_seconds")
    )
    if recorded is not None:
        return float(recorded)
    return timeline.content_budget if timeline is not None else None


def _film_timing(
    plan: Mapping[str, Any], content_seconds: float, content_clips: int
) -> tuple[float, float, float | None]:
    """The renderer's content squeeze, the opening card's offset and the film's length.

    The squeeze is what `apply_final_content_budget` will apply: a selection over
    its content budget is scaled down to fit, so a shot is held for less than the
    editor granted it.
    """
    timeline = _recorded_timeline(plan)
    budget = _content_budget(plan, timeline)
    squeeze = 1.0
    if budget is not None and 0.0 < budget < content_seconds:
        squeeze = budget / content_seconds
    if timeline is None:
        return squeeze, 0.0, None
    policy = (plan.get("render_timing") or {}).get("policy") or {}
    film = estimate_film_duration(
        timeline,
        content_seconds=content_seconds,
        content_clips=content_clips,
        transition_mode=policy.get("transition", "none"),
        transition_duration=float(policy.get("transition_duration") or 0.0),
    )
    return squeeze, timeline.title_duration, film


def _granted_seconds(projection: Mapping[str, Any] | None, row: Mapping[str, Any]) -> float:
    seconds = _interval_seconds(projection, str(row.get("asset_id", "")))
    return float(row.get("seconds") or 0.0) if seconds is None else seconds


def storyboard_from_plan(
    plan: Mapping[str, Any], projection: Mapping[str, Any] | None
) -> Storyboard:
    """Every carrier in capture order, timed and placed the way the renderer will play it."""
    story = plan.get("story") or {}
    titles = {
        str(episode.get("episode") or episode.get("key") or ""): str(episode.get("title") or "")
        for episode in story.get("episodes") or ()
    }
    rows = sorted(plan.get("carriers") or (), key=lambda row: str(row.get("taken") or ""))
    granted = [_granted_seconds(projection, row) for row in rows]
    squeeze, start, film_seconds = _film_timing(plan, sum(granted), len(rows))
    shots: list[Shot] = []
    previous_day = previous_month = ""
    for row, seconds in zip(rows, granted, strict=True):
        taken = str(row.get("taken") or "")
        day, month = taken[:10], _month(taken)
        key = str(row.get("story_episode") or "")
        title = titles.get(key, key)
        held = round(seconds * squeeze, 2)
        shots.append(
            Shot(
                asset_id=str(row.get("asset_id", "")),
                taken=taken,
                day=day,
                story_key=key,
                story_title=title,
                moment=str(row.get("depicted_moment") or ""),
                seconds=held,
                start=round(start, 2),
                motion=str(row.get("kind", "")) in MOTION_KINDS,
                new_day=day != previous_day,
                chapter=month if month != previous_month else "",
                reason=carrier_reason(row.get("why"), title),
            )
        )
        start += held
        previous_day, previous_month = day, month
    return Storyboard(
        thesis=str(story.get("thesis") or ""), shots=tuple(shots), film_seconds=film_seconds
    )


def read_storyboard(attempt_dir: Path) -> Storyboard | None:
    """The storyboard for a finished attempt, or None when it left no plan behind."""
    plan_path = Path(attempt_dir) / PLAN_FILE
    if not plan_path.is_file():
        return None
    projection_path = Path(attempt_dir) / PROJECTION_FILE
    projection = json.loads(projection_path.read_text()) if projection_path.is_file() else None
    return storyboard_from_plan(json.loads(plan_path.read_text()), projection)


def storyboard_lines(board: Storyboard, *, limit: int | None = None) -> list[str]:
    """The storyboard as terminal lines: timecode, day, kind, length, story, reason.

    One line per shot, a chapter line where the month changes, so the terminal
    reads the cut the way the page shows it.
    """
    lines: list[str] = []
    shots = board.shots if limit is None else board.shots[:limit]
    for shot in shots:
        if shot.chapter:
            lines.append(f"  {shot.chapter}")
        kind = "video" if shot.motion else "photo"
        day = shot.day if shot.new_day else " " * len(shot.day)
        head = f"  {shot.timecode:>5}  {day}  {kind:5}  {shot.seconds:>4g} s  {shot.story_title}"
        lines.append(f"{head}: {shot.reason}" if shot.reason else head)
    return lines
