"""One page in the DOM at a time, for the media pool and the review page.

The grid used to append every "Show more" page into the same container, so a
two-hundred-picture pool ended up with two hundred decoded bitmaps alive at once
and the tab died (#824). Paging now replaces the page: the browser keeps the
thumbnails it has seen in its own cache and evicts them on its own terms.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from nicegui import ui

Item = TypeVar("Item")

DEFAULT_PAGE_SIZE = 20


@dataclass(frozen=True)
class Pager:
    """The arithmetic of a paged list: how many pages, which slice, what to call it."""

    total: int
    page_size: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.page_size))

    def clamp(self, page: int) -> int:
        return min(max(page, 0), self.pages - 1)

    def bounds(self, page: int) -> tuple[int, int]:
        start = self.clamp(page) * self.page_size
        return start, min(self.total, start + self.page_size)

    def label(self, page: int) -> str:
        start, end = self.bounds(page)
        if end == 0:
            return "0 of 0"
        return f"{start + 1}–{end} of {self.total}"


def render_paged(
    items: Sequence[Item],
    render_page: Callable[[Sequence[Item]], None],
    page_size: int = DEFAULT_PAGE_SIZE,
) -> None:
    """Render one page of `items`, with previous/next controls when there is more than one."""
    pager = Pager(total=len(items), page_size=page_size)
    if pager.pages == 1:
        render_page(items)
        return

    container = ui.column().classes("w-full")
    controls = ui.row().classes("w-full items-center justify-center gap-2 mt-2")
    current = 0

    def show(page: int) -> None:
        nonlocal current
        current = pager.clamp(page)
        start, end = pager.bounds(current)
        container.clear()
        with container:
            render_page(items[start:end])
        controls.clear()
        with controls:
            ui.button(icon="chevron_left", on_click=lambda: show(current - 1)).props(
                "flat dense aria-label='Previous page'"
            ).set_enabled(current > 0)
            ui.label(pager.label(current)).classes("text-sm").style(
                "color: var(--im-text-secondary)"
            )
            ui.button(icon="chevron_right", on_click=lambda: show(current + 1)).props(
                "flat dense aria-label='Next page'"
            ).set_enabled(current < pager.pages - 1)

    show(0)
