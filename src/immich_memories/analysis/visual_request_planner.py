"""Pure planning for bounded visual editorial requests."""

from __future__ import annotations

from dataclasses import dataclass

from immich_memories.analysis.contact_sheets import ContactSheetPage

__all__ = ["SheetGroup", "VisionRequestLimits", "VisualRequestPlan", "plan_visual_requests"]


@dataclass(frozen=True)
class VisionRequestLimits:
    """The empirically approved limits for one endpoint and model."""

    max_pages_per_request: int = 1
    max_output_tokens: int = 500
    timeout_seconds: int = 30

    def __post_init__(self) -> None:
        if self.max_pages_per_request < 1:
            raise ValueError("max_pages_per_request must be at least one")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be at least one")
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be at least one")


@dataclass(frozen=True)
class SheetGroup:
    """One ordered editorial unit that must not be silently separated."""

    group_id: str
    pages: tuple[ContactSheetPage, ...]

    def __post_init__(self) -> None:
        if not self.group_id:
            raise ValueError("sheet groups need an ID")
        if not self.pages:
            raise ValueError("sheet groups need at least one page")


@dataclass(frozen=True)
class VisualRequestPlan:
    """One request and the explicit continuation it represents, if any."""

    group_ids: tuple[str, ...]
    pages: tuple[ContactSheetPage, ...]
    continuation_number: int = 1
    continuation_count: int = 1


def plan_visual_requests(
    *, groups: tuple[SheetGroup, ...], limits: VisionRequestLimits
) -> tuple[VisualRequestPlan, ...]:
    """Plan ordered group requests without assuming any provider capability."""
    plans: list[VisualRequestPlan] = []
    pending_groups: list[str] = []
    pending_pages: list[ContactSheetPage] = []
    for group in groups:
        if len(group.pages) <= limits.max_pages_per_request:
            if (
                pending_pages
                and len(pending_pages) + len(group.pages) > limits.max_pages_per_request
            ):
                plans.append(VisualRequestPlan(tuple(pending_groups), tuple(pending_pages)))
                pending_groups, pending_pages = [], []
            pending_groups.append(group.group_id)
            pending_pages.extend(group.pages)
            continue
        if pending_pages:
            plans.append(VisualRequestPlan(tuple(pending_groups), tuple(pending_pages)))
            pending_groups, pending_pages = [], []
        chunks = _page_chunks(group.pages, limits.max_pages_per_request)
        for number, pages in enumerate(chunks, start=1):
            plans.append(
                VisualRequestPlan(
                    group_ids=(group.group_id,),
                    pages=pages,
                    continuation_number=number,
                    continuation_count=len(chunks),
                )
            )
    if pending_pages:
        plans.append(VisualRequestPlan(tuple(pending_groups), tuple(pending_pages)))
    return tuple(plans)


def _page_chunks(
    pages: tuple[ContactSheetPage, ...], maximum: int
) -> tuple[tuple[ContactSheetPage, ...], ...]:
    return tuple(pages[index : index + maximum] for index in range(0, len(pages), maximum))
