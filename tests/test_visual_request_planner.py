"""Visual-request plans preserve groups before any provider call is made."""

from pathlib import Path

from immich_memories.analysis.contact_sheets import ContactSheetPage, TileRef
from immich_memories.analysis.visual_request_planner import (
    SheetGroup,
    VisionRequestLimits,
    plan_visual_requests,
)


def _page(group_id: str, number: int = 1) -> ContactSheetPage:
    data = f"jpeg-{group_id}-{number}".encode()
    return ContactSheetPage(
        sheet_id=f"{group_id}-{number}",
        path=Path(f"/{group_id}-{number}.jpg"),
        jpeg_bytes=data,
        sha256=__import__("hashlib").sha256(data).hexdigest(),
        tile_refs=(TileRef(number, f"{group_id}-{number}"),),
        layout_version="1",
    )


def _group(group_id: str, pages: int = 1) -> SheetGroup:
    return SheetGroup(
        group_id=group_id, pages=tuple(_page(group_id, page) for page in range(1, pages + 1))
    )


def test_request_plan_conserves_groups_without_guessing_provider_limits() -> None:
    plans = plan_visual_requests(groups=(_group("a"), _group("b")), limits=VisionRequestLimits())

    assert tuple(group_id for plan in plans for group_id in plan.group_ids) == ("a", "b")
    assert all(len(plan.pages) == 1 for plan in plans)


def test_oversized_group_has_explicit_ordered_continuations() -> None:
    plans = plan_visual_requests(groups=(_group("episode", pages=3),), limits=VisionRequestLimits())

    assert [
        (plan.group_ids, plan.continuation_number, plan.continuation_count) for plan in plans
    ] == [
        (("episode",), 1, 3),
        (("episode",), 2, 3),
        (("episode",), 3, 3),
    ]


def test_confirmed_limit_packs_only_complete_ordered_groups() -> None:
    plans = plan_visual_requests(
        groups=(_group("a"), _group("b"), _group("c")),
        limits=VisionRequestLimits(max_pages_per_request=2),
    )

    assert [plan.group_ids for plan in plans] == [("a", "b"), ("c",)]
