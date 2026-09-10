"""The physical material one episode scan is asked about.

Contact-sheet pages, and the packs that put only complete episodes on one page. A pack is
bounded by what the answer can SAY, not by what the page can hold: measured at temperature
0 on two real months, a 36-episode pack came back complete, valid, and with every Cull list
empty -- no refusal, no warning, just silence on the second half of the question. The same
month split into six smaller packs culled 4.6%, and a dense month whose packs held 4 to 14
episodes culled 4.4%. Fourteen answered; thirty-six did not.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path

from immich_memories.analysis.contact_sheets import (
    MAX_SHEET_TILES,
    ContactSheetPage,
    TileRef,
    build_contact_sheets,
)
from immich_memories.analysis.cull_answer import fused_episode_response_fits
from immich_memories.analysis.editorial_contracts import EditorialCandidate
from immich_memories.analysis.selection_source import EditorialGroup
from immich_memories.analysis.visual_atlas import VisualAtlas
from immich_memories.analysis.visual_request_planner import VisionRequestLimits


@dataclass(frozen=True)
class EpisodeSheet:
    """Every chronological contact-sheet page for one source episode."""

    episode_id: str
    candidates: tuple[EditorialCandidate, ...]
    pages: tuple[ContactSheetPage, ...]


@dataclass(frozen=True)
class EpisodePackScope:
    """The exact numbered tiles belonging to one episode on a shared pack page."""

    episode_id: str
    page_id: str
    episode_alias: int
    page_alias: int
    tile_refs: tuple[TileRef, ...]
    candidates: tuple[EditorialCandidate, ...]
    unavailable_asset_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EpisodeScanPack:
    """One physical chronological page containing only complete episode groups."""

    pack_id: str
    page: ContactSheetPage
    scopes: tuple[EpisodePackScope, ...]
    continuation_number: int = 1
    continuation_count: int = 1


def _viewable_groups(
    groups: tuple[EditorialGroup, ...], atlas: VisualAtlas
) -> tuple[EditorialGroup, ...]:
    """The same groups without the candidates that have no pixels to show."""
    kept = []
    for group in groups:
        members = tuple(
            candidate
            for candidate in group.candidates
            if atlas.tile_for(candidate.asset_id).kind != "unavailable"
        )
        if members:
            kept.append(replace(group, candidates=members))
    return tuple(kept)


def _episode_material(
    groups: tuple[EditorialGroup, ...],
    *,
    atlas: VisualAtlas,
    output_dir: Path,
    limits: VisionRequestLimits,
) -> tuple[tuple[EpisodeSheet, ...], tuple[EpisodeScanPack, ...]]:
    sheets: list[EpisodeSheet] = []
    packs: list[EpisodeScanPack] = []
    pending: list[EditorialGroup] = []
    pending_count = 0

    def flush_pending() -> None:
        nonlocal pending, pending_count
        if not pending:
            return
        page, scopes = _shared_pack_page(tuple(pending), atlas=atlas, output_dir=output_dir)
        pack_id = page.sheet_id.removesuffix("-001")
        packs.append(EpisodeScanPack(pack_id, page, scopes))
        sheets.extend(EpisodeSheet(group.group_id, group.candidates, (page,)) for group in pending)
        pending, pending_count = [], 0

    for group in groups:
        size = len(group.candidates)
        if size > MAX_SHEET_TILES or not _episode_groups_fit((group,), limits):
            flush_pending()
            episode_sheet, continuation_packs = _episode_continuation_material(
                group,
                atlas=atlas,
                output_dir=output_dir,
                limits=limits,
            )
            sheets.append(episode_sheet)
            packs.extend(continuation_packs)
            continue
        proposed = (*pending, group)
        if pending and (
            pending_count + size > MAX_SHEET_TILES or not _episode_groups_fit(proposed, limits)
        ):
            flush_pending()
        pending.append(group)
        pending_count += size
    flush_pending()
    return tuple(sheets), tuple(packs)


def _episode_continuation_material(
    group: EditorialGroup,
    *,
    atlas: VisualAtlas,
    output_dir: Path,
    limits: VisionRequestLimits,
) -> tuple[EpisodeSheet, tuple[EpisodeScanPack, ...]]:
    size = len(group.candidates)
    page_sizes = _continuation_page_sizes(size, limits)
    pages = build_contact_sheets(
        tuple(atlas.tile_for(candidate.asset_id) for candidate in group.candidates),
        _pack_id((group.group_id,)),
        output_dir,
        page_sizes=page_sizes,
    )
    packs = tuple(
        _episode_continuation_pack(
            group,
            page=page,
            number=number,
            count=len(pages),
            atlas=atlas,
        )
        for number, page in enumerate(pages, start=1)
    )
    return EpisodeSheet(group.group_id, group.candidates, pages), packs


def _continuation_page_sizes(size: int, limits: VisionRequestLimits) -> tuple[int, ...]:
    sizes: list[int] = []
    offset = 0
    while offset < size:
        count = min(MAX_SHEET_TILES, size - offset)
        while count and not _episode_response_fits(
            (tuple(range(offset + 1, offset + count + 1)),), limits
        ):
            count -= 1
        if count == 0:
            raise ValueError("episode scan output budget cannot fit one continuation tile")
        sizes.append(count)
        offset += count
    return tuple(sizes)


def _episode_continuation_pack(
    group: EditorialGroup,
    *,
    page: ContactSheetPage,
    number: int,
    count: int,
    atlas: VisualAtlas,
) -> EpisodeScanPack:
    page_asset_ids = {ref.entity_id for ref in page.tile_refs}
    page_candidates = tuple(
        candidate for candidate in group.candidates if candidate.asset_id in page_asset_ids
    )
    unavailable_asset_ids = tuple(
        candidate.asset_id
        for candidate in page_candidates
        if atlas.tile_for(candidate.asset_id).kind == "unavailable"
    )
    return EpisodeScanPack(
        pack_id=page.sheet_id.rsplit("-", 1)[0],
        page=page,
        scopes=(
            EpisodePackScope(
                group.group_id,
                page.sheet_id,
                1,
                1,
                page.tile_refs,
                page_candidates,
                unavailable_asset_ids,
            ),
        ),
        continuation_number=number,
        continuation_count=count,
    )


def _shared_pack_page(
    groups: tuple[EditorialGroup, ...],
    *,
    atlas: VisualAtlas,
    output_dir: Path,
) -> tuple[ContactSheetPage, tuple[EpisodePackScope, ...]]:
    candidates = tuple(
        sorted(
            (candidate for group in groups for candidate in group.candidates),
            key=lambda candidate: (candidate.taken_at, candidate.asset_id),
        )
    )
    pages = build_contact_sheets(
        tuple(atlas.tile_for(candidate.asset_id) for candidate in candidates),
        _pack_id(tuple(group.group_id for group in groups)),
        output_dir,
    )
    if len(pages) != 1:
        raise ValueError("complete episode pack must fit one contact-sheet page")
    page = pages[0]
    episode_by_asset = {
        candidate.asset_id: group.group_id for group in groups for candidate in group.candidates
    }
    scopes = tuple(
        EpisodePackScope(
            episode_id=group.group_id,
            page_id=page.sheet_id,
            episode_alias=episode_alias,
            page_alias=1,
            tile_refs=tuple(
                ref for ref in page.tile_refs if episode_by_asset[ref.entity_id] == group.group_id
            ),
            candidates=group.candidates,
            unavailable_asset_ids=tuple(
                candidate.asset_id
                for candidate in group.candidates
                if atlas.tile_for(candidate.asset_id).kind == "unavailable"
            ),
        )
        for episode_alias, group in enumerate(groups, start=1)
    )
    return page, scopes


def _pack_id(group_ids: tuple[str, ...]) -> str:
    digest = sha256("\x00".join(group_ids).encode()).hexdigest()
    return f"episode-pack-v1-{digest}"


MAX_EPISODES_PER_PACK = 14


def _episode_groups_fit(groups: tuple[EditorialGroup, ...], limits: VisionRequestLimits) -> bool:
    if len(groups) > MAX_EPISODES_PER_PACK:
        return False
    candidates = tuple(
        sorted(
            (candidate for group in groups for candidate in group.candidates),
            key=lambda candidate: (candidate.taken_at, candidate.asset_id),
        )
    )
    number_by_asset = {
        candidate.asset_id: number for number, candidate in enumerate(candidates, start=1)
    }
    displayed_by_episode = tuple(
        tuple(number_by_asset[candidate.asset_id] for candidate in group.candidates)
        for group in groups
    )
    return _episode_response_fits(displayed_by_episode, limits)


def _episode_response_fits(
    displayed_by_episode: tuple[tuple[int, ...], ...], limits: VisionRequestLimits
) -> bool:
    return fused_episode_response_fits(
        displayed_by_episode,
        max_output_tokens=limits.max_output_tokens,
    )


def _solo_episode_pack(
    scope: EpisodePackScope, *, atlas: VisualAtlas, output_dir: Path
) -> EpisodeScanPack:
    """One episode from a shared page, rendered alone so its scope partitions its page."""
    candidates = tuple(
        sorted(scope.candidates, key=lambda candidate: (candidate.taken_at, candidate.asset_id))
    )
    pages = build_contact_sheets(
        tuple(atlas.tile_for(candidate.asset_id) for candidate in candidates),
        _pack_id((scope.episode_id,)),
        output_dir,
    )
    if len(pages) != 1:
        raise ValueError("an episode that shared one page must fit one page alone")
    page = pages[0]
    return EpisodeScanPack(
        page.sheet_id.removesuffix("-001"),
        page,
        (
            EpisodePackScope(
                scope.episode_id,
                page.sheet_id,
                1,
                1,
                page.tile_refs,
                candidates,
                scope.unavailable_asset_ids,
            ),
        ),
    )
