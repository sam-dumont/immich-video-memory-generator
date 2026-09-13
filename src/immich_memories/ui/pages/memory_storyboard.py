"""The cut in shot order: what the video will play, read from the attempt directory.

The story view ranks stories by weight, which is how the editor thinks. The
video plays in capture order, which is how the viewer sees it. This module reads
the same `plan.private.json` the story view reads, lays the renderer's intervals
from `render-projection.private.json` over it, and yields one shot per carrier in
the order they will play: capture day, the story each was granted to, the
moment it depicts, its length, and a running timecode. A month change gets a
chapter divider, the way the title planner puts one divider per month shown; a
day change under one story title is marked so a loose grouping reads as one.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from nicegui import ui

from immich_memories.ui.components import im_card, im_section_header
from immich_memories.ui.pages.memory_story_data import MOTION_KINDS, PLAN_FILE, carrier_reason
from immich_memories.ui.state import get_app_state

PROJECTION_FILE = "render-projection.private.json"

# The grid thumbnail first: a storyboard of forty shots must not cost forty previews. The
# session cache only holds what the pool loader fetched, which today is the preview, so
# that is the fallback until the media route (S3) serves sizes on demand.
_THUMBNAIL_SIZES = ("thumbnail", "preview")


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


def _thumbnail(asset_id: str) -> None:
    state = get_app_state()
    thumb = None
    if state.thumbnail_cache is not None:
        for size in _THUMBNAIL_SIZES:
            try:
                thumb = state.thumbnail_cache.get(asset_id, size)
            except Exception:  # WHY: a missing thumbnail must not take the storyboard down
                thumb = None
            if thumb:
                break
    if thumb:
        ui.image(f"data:image/jpeg;base64,{base64.b64encode(thumb).decode()}").classes(
            "rounded"
        ).style("width: 96px; aspect-ratio: 16/9; object-fit: cover")
    else:
        ui.element("div").classes("rounded").style(
            "width: 96px; aspect-ratio: 16/9; background: var(--im-bg-surface)"
        )


def _render_shot(shot: Shot) -> None:
    with im_card() as card:
        card.classes("p-2 storyboard-shot")
        with ui.row().classes("w-full items-center gap-3 no-wrap"):
            ui.label(shot.timecode).classes("text-xs font-mono w-10").style(
                "color: var(--im-text-secondary)"
            )
            _thumbnail(shot.asset_id)
            with ui.column().classes("gap-0 flex-1 min-w-0"):
                with ui.row().classes("items-center gap-2 flex-wrap"):
                    ui.label(shot.day).classes(
                        "text-sm font-semibold storyboard-day" if shot.new_day else "text-sm"
                    ).style("color: var(--im-text)")
                    ui.icon("videocam" if shot.motion else "photo").classes("text-sm").style(
                        "color: var(--im-text-secondary)"
                    )
                    ui.label(f"{shot.seconds:g} s").classes("text-xs").style(
                        "color: var(--im-text-secondary)"
                    )
                with ui.row().classes("items-center gap-2 flex-wrap"):
                    ui.label(shot.story_title).classes("text-xs storyboard-story").style(
                        "color: var(--im-primary)"
                    )
                    if shot.moment:
                        ui.label(shot.moment).classes("text-xs").style(
                            "color: var(--im-text-secondary)"
                        )
                if shot.reason:
                    ui.label(shot.reason).classes("text-xs").style("color: var(--im-text)")


def render_storyboard(board: Storyboard, note: str = "", warning: str | None = None) -> None:
    """The thesis, then the cut as it will play: chapters, days and shots in order."""
    im_section_header("The storyboard", icon="view_timeline")
    ui.label(board.thesis or "The editor left no thesis for this cut.").classes("text-lg").style(
        "color: var(--im-text)"
    )
    if note:
        ui.label(note).classes("text-sm mt-1").style("color: var(--im-text-secondary)")
    if warning:
        ui.label(warning).classes("text-sm").style("color: var(--im-warning)")
    im_section_header(f"{len(board.shots)} shots, {board.total_label} of content", icon="movie")
    for shot in board.shots:
        if shot.chapter:
            ui.label(shot.chapter).classes("text-sm font-semibold mt-2 storyboard-chapter").style(
                "color: var(--im-text-secondary)"
            )
        _render_shot(shot)
