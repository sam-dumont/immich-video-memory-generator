"""Clear a picture's hold, or never use it: the owner's per-picture buttons on the pool and the
storyboard. Both write the same store the CLI's `pictures` commands write, and the next cut on
every tier reads it."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from functools import partial

from nicegui import ui

from immich_memories.operations import picture_holds
from immich_memories.operations.picture_holds import PictureHold
from immich_memories.ui.components import im_button
from immich_memories.ui.pages.step2_helpers import render_thumbnail

logger = logging.getLogger(__name__)

_CLEAR_WARNING = (
    "Once cleared, every film up to that level may use it. Nothing the app reads later puts "
    "the hold back; Undo does."
)
# The widest film a cleared picture may play in, as the dialog offers it.
CLEAR_LEVELS = {
    "just-us": "Just us: only films for the household",
    "family": "Family: family films too",
    "anyone": "Anyone: shareable films too",
}


def _config():
    from immich_memories.config import get_config

    return get_config()


def read_holds(
    asset_ids: Iterable[str], clips: Mapping[str, str | None] | None = None
) -> dict[str, PictureHold]:
    """One read of the library's banks for a page of pictures; nothing when they can't be read."""
    try:
        return picture_holds.read(_config(), asset_ids, clips=clips)
    except Exception:  # WHY: a bank the page can't read must not take the pool down
        logger.warning("could not read the pictures' holds", exc_info=True)
        return {}


def render_picture_decision(
    asset_id: str,
    hold: PictureHold | None,
    *,
    clip_id: str | None = None,
    compact: bool = False,
    on_never: Callable[[], object] | None = None,
) -> None:
    """The picture's hold in one line, and the buttons that answer it. Redraws itself.

    `on_never` runs after the owner rules the picture out, for the page to follow suit.
    """
    _DecisionView(asset_id, hold, clip_id=clip_id, compact=compact, on_never=on_never).draw()


class _DecisionView:
    """One picture's line and buttons, redrawn in place after each decision."""

    def __init__(
        self,
        asset_id: str,
        hold: PictureHold | None,
        *,
        clip_id: str | None,
        compact: bool,
        on_never: Callable[[], object] | None,
    ) -> None:
        self._asset_id = asset_id
        self._clip_id = clip_id
        self._compact = compact
        self._on_never = on_never
        self._hold = hold or PictureHold(asset_id, None, (), detector=False)
        self._holder = ui.element("div").classes("w-full picture-decision")

    def _act(self, action, done: str) -> bool:
        try:
            action(_config(), self._asset_id, clip_id=self._clip_id)
        except Exception:  # WHY: a store that refuses the write must say so, not vanish
            logger.warning("could not record the decision on %s", self._asset_id, exc_info=True)
            ui.notify("That didn't save. Try again, or use `pictures` in the CLI.", type="negative")
            return False
        fresh = read_holds([self._asset_id], {self._asset_id: self._clip_id})
        self._hold = fresh.get(self._asset_id, self._hold)
        ui.notify(done, type="positive")
        self.draw()
        return True

    def _clear(self, level: str) -> None:
        self._act(
            partial(picture_holds.clear_hold, via="web", level=level),
            "Hold cleared. The next cut may use it.",
        )

    def _never(self) -> None:
        saved = self._act(
            partial(picture_holds.never_use, via="web"),
            "It won't be in any film from the next cut on.",
        )
        if saved and self._on_never is not None:
            self._on_never()

    def _undo(self) -> None:
        self._act(picture_holds.forget, "Your decision is gone; the app's own holds apply again.")

    def draw(self) -> None:
        self._holder.clear()
        current = self._hold
        with self._holder:
            text = current.describe()
            if text:
                colour = "--im-warning-text" if current.can_clear else "--im-text-secondary"
                ui.label(text).classes("picture-hold text-xs leading-snug mt-1").style(
                    f"color: var({colour})"
                )
            with ui.row().classes("gap-1 mt-1 items-center"):
                self._buttons(current)

    def _buttons(self, current: PictureHold) -> None:
        if current.can_clear:
            _button(
                "Clear hold", "lock_open", self._compact, lambda: _confirm(current, self._clear)
            )
        if current.decision != picture_holds.NEVER_USE:
            _button("Never use", "block", self._compact, self._never)
        if current.decision is not None:
            _button("Undo", "undo", self._compact, self._undo)


def _button(text: str, icon: str, compact: bool, on_click) -> None:
    if compact:
        button = ui.button(icon=icon, on_click=on_click).props(
            f'flat dense round size=sm aria-label="{text}"'
        )
        button.tooltip(text)
        button.classes(f"picture-{icon.replace('_', '-')}")
        return
    button = ui.button(text, icon=icon, on_click=on_click).props("flat dense no-caps size=sm")
    button.classes(f"picture-{icon.replace('_', '-')}")


def _confirm(hold: PictureHold, clear) -> None:
    """Ask before clearing: the owner looks at the picture, the app never clears one itself."""
    with ui.dialog() as dialog, ui.card().classes("clear-hold-dialog"):
        ui.label("Clear this picture's hold?").classes("text-lg font-semibold")
        render_thumbnail(
            hold.asset_id,
            classes="rounded-lg",
            style="width: 320px; max-width: 100%; aspect-ratio: 4/3; object-fit: cover",
            size="preview",
        )
        ui.label(hold.describe()).classes("text-sm")
        if hold.detector:
            ui.label("Open it in Immich first if you haven't looked at it yourself.").classes(
                "text-sm"
            )
        ui.label("Fine for").classes("text-sm font-semibold mt-1")
        level = ui.radio(CLEAR_LEVELS, value="family").props("dense").classes("clear-hold-level")
        ui.label(_CLEAR_WARNING).classes("text-xs").style("color: var(--im-text-secondary)")
        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            im_button("Cancel", variant="ghost", on_click=dialog.close)

            def confirmed() -> None:
                dialog.close()
                clear(level.value)

            im_button("Clear hold", variant="primary", on_click=confirmed)
    dialog.open()
