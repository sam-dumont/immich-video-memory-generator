"""A cut that outlives its page.

The worker is the same blocking run the pool page used. What this module adds
is durability: the session remembers the cut's key before the worker starts,
the page polls the attempt tree that key names on every tick, and a reload
mid-cut lands on the same phase rows instead of starting a second run. The
attempt's OS lease is what separates a slow live run from an interrupted one.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nicegui import background_tasks, run, ui

from immich_memories.analysis.editorial_planner import EditorialSelection
from immich_memories.api.models import AssetType, VideoClipInfo
from immich_memories.operations.cut_progress import StageProgress, read_stage_progress
from immich_memories.operations.editorial_attempt import read_editorial_attempt
from immich_memories.operations.phases import OperationalPhase
from immich_memories.security import sanitize_error_message
from immich_memories.ui.components import im_button
from immich_memories.ui.pages.clip_pipeline import (
    _build_pipeline_config,
    _configure_timeline_for_selection,
    _eligible_pipeline_media,
    _resolve_auto_duration_for_selection,
    _run_pipeline_blocking,
    ui_cut_key,
)
from immich_memories.ui.pages.cut_progress_view import LiveStrip, StageBar, StageLog
from immich_memories.ui.pages.memory_story_data import MOTION_KINDS, PLAN_FILE
from immich_memories.ui.pages.step2_helpers import get_thumbnail
from immich_memories.ui.pages.step2_loading import ensure_caches, load_pool

if TYPE_CHECKING:
    from immich_memories.ui.state import AppState

logger = logging.getLogger(__name__)

LATEST_ATTEMPT = "latest-attempt.private.json"
RENDER_PROJECTION = "render-projection.private.json"

# The phases a cut passes, in order. Render, music and delivery belong to Export.
CUT_PHASES = (
    OperationalPhase.DISCOVERY,
    OperationalPhase.DOWNLOAD,
    OperationalPhase.ANALYSIS,
    OperationalPhase.SELECTION,
    OperationalPhase.COMPLETE,
)
_PHASE_TITLES = {
    OperationalPhase.DISCOVERY: "Finding media",
    OperationalPhase.DOWNLOAD: "Loading thumbnails",
    OperationalPhase.ANALYSIS: "Reading the pictures",
    OperationalPhase.SELECTION: "Editing",
    OperationalPhase.COMPLETE: "Done",
}
_PREPARING = "Preparing the cut"


@dataclass(frozen=True)
class CutStatus:
    """Where a cut is, as the phase rows show it: the active phase and its stage string."""

    phase: OperationalPhase
    detail: str


def arm_cut(state: AppState, before: Callable[[], None] | None = None) -> bool:
    """Mark the session as cutting, unless a cut is already running.

    Returns False and changes nothing when one is. `before` runs under the same
    lock once the cut is admitted, for the reset that must precede the arming.
    The Memory page launches the worker on its next render.
    """
    with state.lock:
        if state.pipeline_running:
            return False
        if before is not None:
            before()
        state.active_cut_key = None
        state.cut_stage_log.clear()
        # Attempt records carry whole seconds; an attempt started in this same second is ours.
        state.cut_armed_at = datetime.now(UTC).replace(microsecond=0)
        state.cancel_requested = False
        state.pipeline_running = True
    return True


CUT_ALREADY_RUNNING = "A cut is already running; wait for it, or cancel it first"


def _cache_path(state: AppState) -> Path:
    if state.config is not None:
        return state.config.cache.cache_path
    from immich_memories.config import get_config

    return get_config().cache.cache_path


def attempt_root(state: AppState) -> Path | None:
    """Where the armed cut writes its attempts, once its key is known."""
    if not state.active_cut_key:
        return None
    return _cache_path(state) / "editorial-runs" / state.active_cut_key


def _started_before(record: Mapping[str, Any], since: datetime | None) -> bool:
    if since is None:
        return False
    try:
        started = datetime.fromisoformat(str(record.get("started_at") or ""))
    except ValueError:
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return started < since


def read_latest_attempt(root: Path | None, since: datetime | None = None) -> dict[str, Any] | None:
    """The newest attempt under a cut key, its liveness read from the lease.

    An attempt started before `since` belongs to an earlier cut of the same
    brief and reads as no attempt at all.
    """
    if root is None:
        return None
    pointer = root / LATEST_ATTEMPT
    if not pointer.is_file():
        return None
    try:
        directory = Path(json.loads(pointer.read_text())["directory"])
        record = read_editorial_attempt(directory)
    except (OSError, ValueError, KeyError):
        return None
    if _started_before(record, since):
        return None
    return record | {"directory": str(directory)}


def latest_attempt_of(state: AppState) -> dict[str, Any] | None:
    """The armed cut's newest attempt, or None before it has written one."""
    return read_latest_attempt(attempt_root(state), since=state.cut_armed_at)


def live_progress_of(record: Mapping[str, Any] | None) -> StageProgress | None:
    """The numbers the attempt is reporting right now, read from that same attempt.

    Going through the record rather than the session's own key is what makes a
    reload rejoin the bar exactly where it rejoins the rows. A finished per-asset
    pass leaves its last snapshot on disk, so the stage it names has to match the
    stage the attempt is on: otherwise a full bar would sit under a row that has
    long since moved on to work that counts nothing.
    """
    directory = (record or {}).get("directory")
    progress = read_stage_progress(Path(directory)) if directory else None
    if progress is None or progress.stage_label != str((record or {}).get("stage") or ""):
        return None
    return progress


def phase_of(record: Mapping[str, Any] | None) -> CutStatus:
    """Map an attempt record onto the cut's phases; no record yet means the cut is being prepared."""
    if record is None:
        return CutStatus(OperationalPhase.ANALYSIS, _PREPARING)
    stage = str(record.get("stage") or "")
    if record.get("status") == "complete":
        return CutStatus(OperationalPhase.COMPLETE, stage)
    # Annotation production announces itself as "Preparing ..."; everything after is the edit.
    if stage.startswith("Preparing"):
        return CutStatus(OperationalPhase.ANALYSIS, stage)
    return CutStatus(OperationalPhase.SELECTION, stage)


def elapsed_label(started_at: str | None, now: datetime | None = None) -> str:
    """Time since the attempt started, from its own record so a reload keeps counting."""
    if not started_at:
        return ""
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return ""
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    seconds = max(0, int(((now or datetime.now(UTC)) - started).total_seconds()))
    minutes, rest = divmod(seconds, 60)
    return f"{minutes}m {rest:02d}s" if minutes else f"{rest}s"


def restore_cut_from_attempt(state: AppState, attempt_dir: Path, record: Mapping[str, Any]) -> None:
    """Rebuild the session's selection from a finished attempt, over a pool loaded again.

    The projection names what shipped and the exact interval of each; the plan
    says what kind of picture each was. Media the pool no longer holds is
    skipped rather than invented.
    """
    projection = json.loads((attempt_dir / RENDER_PROJECTION).read_text())
    plan = json.loads((attempt_dir / PLAN_FILE).read_text())
    kinds = {row.get("asset_id"): row.get("kind") for row in plan.get("carriers") or ()}
    sources: dict[str, Any] = {clip.asset.id: clip for clip in state.clips}
    sources.update({photo.id: photo for photo in state.photo_assets})

    selected: list[VideoClipInfo] = []
    selections: list[EditorialSelection] = []
    segments: dict[str, tuple[float, float]] = {}
    for asset_id in projection.get("selected_ids") or ():
        source = sources.get(asset_id)
        if source is None:
            continue
        start, end = (float(value) for value in projection["intervals"][asset_id])
        clip = (
            source
            if isinstance(source, VideoClipInfo)
            else VideoClipInfo(
                asset=source, duration_seconds=end - start, width=source.width, height=source.height
            )
        )
        selected.append(clip)
        segments[asset_id] = (start, end)
        selections.append(
            EditorialSelection(
                asset_id=asset_id,
                start_time=start,
                end_time=end,
                render_mode="motion" if kinds.get(asset_id) in MOTION_KINDS else "still",
            )
        )

    state.pipeline_selected_clips = selected
    state.editorial_selections = tuple(selections)
    state.selected_clip_ids = set(segments)
    state.selected_photo_ids = {c.asset.id for c in selected if c.asset.type == AssetType.IMAGE}
    state.previous_cut_asset_ids = frozenset(segments)
    state.clip_segments = segments
    state.editorial_attempt_dir = attempt_dir
    state.pipeline_result = {
        "selected_clips": selected,
        "editorial_selections": tuple(selections),
        "clip_segments": segments,
        "errors": [],
        "stats": {
            "selection_route": "editorial-source",
            "editorial_attempt_directory": str(attempt_dir),
            "editorial_duration_realization": record.get("duration_realization"),
            "eligible_count": len(state.clips) + len(state.photo_assets),
            "planned_count": len(selected),
            "recovered": True,
        },
    }


class _PhaseRows:
    """One row per phase of the cut; the active row carries the attempt's own stage string."""

    def __init__(self) -> None:
        self._rows: dict[OperationalPhase, tuple[ui.icon, ui.label]] = {}
        # Named so a reader -- a person or a browser test -- can tell the active
        # row's stage from the same string echoed in the detail panel below.
        with ui.column().classes("w-full gap-1 mb-3 cut-phase-rows"):
            for phase in CUT_PHASES:
                with ui.row().classes("items-center gap-3"):
                    icon = ui.icon("radio_button_unchecked").classes("text-lg")
                    ui.label(_PHASE_TITLES[phase]).classes("text-sm w-40")
                    detail = (
                        ui.label("").classes("text-sm").style("color: var(--im-text-secondary)")
                    )
                self._rows[phase] = (icon, detail)

    def paint(self, status: CutStatus) -> None:
        for phase, (icon, detail) in self._rows.items():
            if phase.order < status.phase.order:
                icon.name = "check_circle"
                icon.style("color: var(--im-success)")
                detail.set_text("")
            elif phase is status.phase:
                icon.name = "pending"
                icon.style("color: var(--im-info)")
                detail.set_text(status.detail)
            else:
                icon.name = "radio_button_unchecked"
                icon.style("color: var(--im-text-muted)")
                detail.set_text("")


class _LoaderSurface:
    """The status label the pool loader writes to, echoed onto the phase rows."""

    def __init__(self, rows: _PhaseRows) -> None:
        self._rows = rows
        self._phase = OperationalPhase.DISCOVERY

    def set_text(self, text: str) -> None:
        self._rows.paint(CutStatus(self._phase, text))

    def on_phase(self, phase: OperationalPhase) -> None:
        self._phase = phase


def _launch(state: AppState, progress_state: dict[str, Any]) -> None:
    """Size the timeline from the pool, name the cut, and start the worker once."""
    clips, photos = _eligible_pipeline_media(state, state.clips)
    _resolve_auto_duration_for_selection(state, clips, photos)
    _configure_timeline_for_selection(state, clips, photos)
    config = _build_pipeline_config(state)
    state.active_cut_key = ui_cut_key(state)

    async def work() -> None:
        await run.io_bound(_run_pipeline_blocking, state, config, clips, photos, progress_state)

    # WHY a background task, not a page timer: a timer dies with its client, and a
    # reload between naming the cut and starting its worker would leave the
    # session armed for a run nothing ever started.
    background_tasks.create(work(), name=f"cut {state.active_cut_key}")


def _finish(state: AppState, progress_state: dict[str, Any]) -> None:
    error = progress_state.get("error")
    if error:
        ui.notify(f"Cut failed: {sanitize_error_message(str(error))}", type="negative")
    elif state.pipeline_result is not None:
        from immich_memories.ui.pages.pipeline_title import generate_title_after_pipeline

        asyncio.ensure_future(generate_title_after_pipeline(state))
    ui.navigate.to("/")


async def _load_pool_for_cut(state: AppState, rows: _PhaseRows) -> bool:
    """Load the brief's media into the session; False when the cut cannot go on."""
    surface = _LoaderSurface(rows)
    try:
        await load_pool(state, surface, None, on_phase=surface.on_phase)
    except Exception as exc:  # WHY: UI graceful degradation
        logger.exception("Loading the pool for a cut failed")
        state.pipeline_running = False
        ui.notify(f"Could not load media: {sanitize_error_message(str(exc))}", type="negative")
        ui.navigate.to("/")
        return False
    if not state.clips and not state.photo_assets:
        state.pipeline_running = False
        ui.notify("No media found for this brief.", type="warning")
        ui.navigate.to("/")
        return False
    return True


def render_cutting(state: AppState) -> None:
    """The cut in progress: phase rows, elapsed time, Cancel; a reload joins the same run."""
    ensure_caches(state)
    ui.label("Cutting the memory...").classes("text-2xl font-bold mb-2")
    rows = _PhaseRows()
    strip = LiveStrip(get_thumbnail)
    bar = StageBar()
    elapsed = ui.label("").classes("text-sm mb-2").style("color: var(--im-text-secondary)")
    log = StageLog(state.cut_stage_log)

    def cancel() -> None:
        state.cancel_requested = True
        cancel_button.disable()
        elapsed.set_text("Cancelling after the current stage...")

    cancel_button = im_button("Cancel", variant="secondary", icon="stop", on_click=cancel)
    progress_state: dict[str, Any] = {"done": False, "error": None}

    def poll() -> None:
        if not state.pipeline_running:
            timer.deactivate()
            _finish(state, progress_state)
            return
        record = latest_attempt_of(state)
        status = phase_of(record)
        rows.paint(status)
        log.remember(status.detail)
        progress = live_progress_of(record)
        bar.show(progress)
        strip.show(progress.recent_asset_ids if progress is not None else ())
        if record is not None and not state.cancel_requested:
            elapsed.set_text(f"Elapsed: {elapsed_label(record.get('started_at'))}")

    timer = ui.timer(1.0, poll, active=False)

    async def begin() -> None:
        pool_loaded = bool(state.clips or state.photo_assets)
        if not pool_loaded and not await _load_pool_for_cut(state, rows):
            return
        if state.active_cut_key is None:
            _launch(state, progress_state)
        rows.paint(phase_of(latest_attempt_of(state)))
        timer.activate()

    ui.timer(0.1, begin, once=True)


def render_reload_media(state: AppState, attempt_dir: Path, record: Mapping[str, Any]) -> None:
    """Offer to load the pool again and rebuild the finished cut's selection over it."""
    container = ui.column().classes("w-full")

    async def reload() -> None:
        container.clear()
        with container:
            progress = ui.linear_progress(value=0, show_value=False).classes("w-full")
            status = ui.label("Connecting to Immich...").classes("text-sm")
        try:
            await load_pool(state, status, progress)
            restore_cut_from_attempt(state, attempt_dir, record)
        except Exception as exc:  # WHY: UI graceful degradation
            logger.exception("Re-loading media for a recovered cut failed")
            ui.notify(
                f"Could not re-load media: {sanitize_error_message(str(exc))}", type="negative"
            )
            return
        ui.navigate.to("/")

    im_button("Re-load media to export", variant="primary", icon="movie", on_click=reload)
