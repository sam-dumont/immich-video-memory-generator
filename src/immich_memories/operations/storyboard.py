"""The cut in shot order, read from an attempt directory: what the video will play.

The story view ranks stories by weight, which is how the editor thinks. The video
plays in capture order, which is how the viewer sees it. This module reads the
`plan.private.json` every attempt writes, lays the renderer's intervals from
`render-projection.private.json` over it, and yields one shot per carrier in the
order they will play: capture day, the story each was granted to, the moment it
depicts, its length, and a running timecode. A month change gets a chapter
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
        whole = int(self.start)
        return f"{whole // 60}:{whole % 60:02d}"

    @property
    def kind_label(self) -> str:
        """The badge word: a moving picture is a Video, everything else is held as a Still."""
        return "Video" if self.motion else "Still"


@dataclass(frozen=True)
class Storyboard:
    thesis: str
    shots: tuple[Shot, ...]

    @property
    def total_seconds(self) -> float:
        return round(sum(shot.seconds for shot in self.shots), 2)

    @property
    def total_label(self) -> str:
        whole = int(self.total_seconds)
        return f"{whole // 60}:{whole % 60:02d}"

    @property
    def summary_label(self) -> str:
        """What the seconds are: pictures and video, before titles and transitions fill the film."""
        count = len(self.shots)
        noun = "picture" if count == 1 else "pictures"
        return f"{count} {noun}, {self.total_label} of pictures and video"


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


def storyboard_from_plan(
    plan: Mapping[str, Any], projection: Mapping[str, Any] | None
) -> Storyboard:
    """Every carrier in capture order, timed the way the renderer will hold it."""
    story = plan.get("story") or {}
    titles = {
        str(episode.get("episode") or episode.get("key") or ""): str(episode.get("title") or "")
        for episode in story.get("episodes") or ()
    }
    rows = sorted(plan.get("carriers") or (), key=lambda row: str(row.get("taken") or ""))
    shots: list[Shot] = []
    start = 0.0
    previous_day = previous_month = ""
    for row in rows:
        asset_id = str(row.get("asset_id", ""))
        taken = str(row.get("taken") or "")
        day, month = taken[:10], _month(taken)
        key = str(row.get("story_episode") or "")
        title = titles.get(key, key)
        seconds = _interval_seconds(projection, asset_id)
        if seconds is None:
            seconds = float(row.get("seconds") or 0.0)
        shots.append(
            Shot(
                asset_id=asset_id,
                taken=taken,
                day=day,
                story_key=key,
                story_title=title,
                moment=str(row.get("depicted_moment") or ""),
                seconds=seconds,
                start=round(start, 2),
                motion=str(row.get("kind", "")) in MOTION_KINDS,
                new_day=day != previous_day,
                chapter=month if month != previous_month else "",
                reason=carrier_reason(row.get("why"), title),
            )
        )
        start += seconds
        previous_day, previous_month = day, month
    return Storyboard(thesis=str(story.get("thesis") or ""), shots=tuple(shots))


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
