"""The shared saved cut, presented as an interactive contact sheet."""

from __future__ import annotations

import json
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

from nicegui import ui

from immich_memories.operations.cut_review import read_cut_decisions
from immich_memories.operations.storyboard import (
    PROJECTION_FILE,
    Shot,
    Storyboard,
    read_storyboard,
    storyboard_from_plan,
)
from immich_memories.ui.i18n import tr
from immich_memories.ui.pages.picture_decisions import read_holds, render_picture_decision
from immich_memories.ui.state import get_app_state

__all__ = [
    "PROJECTION_FILE",
    "Shot",
    "Storyboard",
    "read_storyboard",
    "render_storyboard",
    "storyboard_from_plan",
]

_BUNDLE = Path(__file__).parents[1] / "static/review/review.js"
_BUNDLE_VERSION = sha256(_BUNDLE.read_bytes()).hexdigest()[:12]


def render_storyboard(
    board: Storyboard,
    note: str = "",
    warning: str | None = None,
    *,
    show_thesis: bool = True,
    attempt_dir: Path | None = None,
) -> None:
    """Inspect the saved cut and refine the session's export selection independently."""
    if show_thesis:
        ui.label(board.thesis or tr("The editor left no thesis for this cut.")).classes("text-lg")
    if note:
        ui.label(note).classes("text-sm")
    if warning:
        ui.label(warning).classes("text-sm").style("color: var(--im-warning)")
    ui.label(board.summary_label).classes("text-sm font-semibold")
    ui.label(tr("Titles and transitions make up the rest of the film.")).classes("text-xs").style(
        "color: var(--im-text-secondary)"
    )
    _CutReview(board, attempt_dir)


class _CutReview:
    def __init__(self, board: Storyboard, attempt_dir: Path | None) -> None:
        self._state = get_app_state()
        self._editable = bool(self._state.pipeline_selected_clips)
        decisions = read_cut_decisions(attempt_dir) if attempt_dir else {}
        kinds = {"Video": tr("Video"), "Still": tr("Still")}
        self._shots = {
            shot.asset_id: asdict(shot)
            | {
                "timecode": shot.timecode,
                "kind_label": kinds[shot.kind_label],
                "included": not self._editable or shot.asset_id in self._state.selected_clip_ids,
                "decision": decisions.get(shot.asset_id),
            }
            for shot in board.shots
        }
        ui.add_head_html(
            f'<script type="module" src="/static/review/review.js?v={_BUNDLE_VERSION}"></script>'
        )
        self._element = ui.element("im-cut-review").classes("w-full")
        self._element.on("review-action", self._act, args=["detail"])
        self._update()

    def _update(self) -> None:
        self._element.props["payload"] = json.dumps(
            {
                "editable": self._editable,
                "shots": list(self._shots.values()),
                "labels": _labels(),
            }
        )
        self._element.update()

    def _include(self, asset_id: str, value: bool) -> None:
        if not self._editable:
            return
        state = self._state
        if value:
            state.selected_clip_ids.add(asset_id)
        else:
            state.selected_clip_ids.discard(asset_id)
        if any(photo.id == asset_id for photo in state.photo_assets):
            if value:
                state.selected_photo_ids.add(asset_id)
            else:
                state.selected_photo_ids.discard(asset_id)
        self._shots[asset_id]["included"] = value
        self._update()

    def _act(self, event) -> None:
        detail = event.args.get("detail", {})
        asset_id = detail.get("asset_id")
        if asset_id not in self._shots:
            return
        action = detail.get("action")
        if action == "include" and isinstance(detail.get("included"), bool):
            self._include(asset_id, detail["included"])
        elif action == "decisions":
            with ui.dialog() as dialog, ui.card().classes("w-96 max-w-full"):
                ui.label(tr("Picture decisions")).classes("text-lg font-semibold")
                holds = read_holds([asset_id])
                render_picture_decision(
                    asset_id, holds.get(asset_id), on_never=lambda: self._include(asset_id, False)
                )
                ui.button(tr("Close"), on_click=dialog.close).props("flat no-caps")
            dialog.open()
        elif action in {"trim", "pool"}:
            self._state.review_selected_mode = action == "trim"
            ui.navigate.to("/step2")


def _labels() -> dict[str, str]:
    return {
        "savedTiming": tr("Order and timecodes from the saved cut."),
        "show": tr("Show"),
        "all": tr("All pictures"),
        "videos": tr("Videos"),
        "stills": tr("Stills"),
        "excluded": tr("Excluded"),
        "contactSheet": tr("Cut contact sheet"),
        "pictureReview": tr("Picture review"),
        "noPictures": tr("No pictures match this filter."),
        "why": tr("Why this picture"),
        "noReason": tr("No reason recorded for this picture."),
        "modelSuggestion": tr("Model suggestion"),
        "keptBecause": tr("Kept because"),
        "alternative": tr("Recorded alternative"),
        "alternativeCount": tr("Alternatives considered"),
        "noOutcome": tr("No outcome recorded."),
        "include": tr("Include in export"),
        "selectionNote": tr("Export uses your selection. Cut again replans it."),
        "trim": tr("Trim the video clips"),
        "decisions": tr("Picture decisions"),
        "alternatives": tr("Find alternatives in the pool"),
        "backToPictures": tr("Back to pictures"),
    }
