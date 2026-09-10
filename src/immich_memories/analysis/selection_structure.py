"""Conserved chronological workprint for the production Structure pass."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from immich_memories.analysis.contact_sheets import ContactSheetPage, build_contact_sheets
from immich_memories.analysis.duplicate_hashing import (
    compute_thumbnail_hash,
    hamming_distance,
)
from immich_memories.analysis.editorial_contracts import EditorialCandidate
from immich_memories.analysis.selection_source import PreparedEditorialSource
from immich_memories.analysis.visual_atlas import VisualAtlas


@dataclass(frozen=True)
class StructureMoment:
    """One surviving moment, represented for viewing but not reduced in membership."""

    moment_id: str
    candidates: tuple[EditorialCandidate, ...]
    representative: EditorialCandidate
    representative_reason: str | None = None

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        """Return every Cull survivor held behind this moment's visual proxy."""
        return tuple(candidate.asset_id for candidate in self.candidates)


@dataclass(frozen=True)
class StructureWorkprint:
    """Every surviving moment in chronology, ready to be shown to Structure."""

    moments: tuple[StructureMoment, ...]
    pages: tuple[ContactSheetPage, ...] = ()

    @property
    def representative_ids(self) -> tuple[str, ...]:
        """Return the visual proxies without pretending they are the selected cut."""
        return tuple(moment.representative.asset_id for moment in self.moments)


def build_structure_workprint(
    prepared: PreparedEditorialSource,
    admitted: Sequence[EditorialCandidate],
    *,
    atlas: VisualAtlas | None = None,
    representative_resolver: Callable[[tuple[EditorialCandidate, ...]], tuple[str, str]]
    | None = None,
    output_dir: Path | None = None,
) -> StructureWorkprint:
    """Represent every moment that survived Cull while conserving its members."""
    admitted_ids = _admitted_ids(admitted, prepared)
    moments = _conserved_moments(
        prepared,
        admitted_ids,
        atlas=atlas,
        resolver=representative_resolver,
    )
    if atlas is not None:
        for moment in moments:
            atlas.tile_for(moment.representative.asset_id)
    return StructureWorkprint(
        moments=moments,
        pages=_review_pages(moments, atlas=atlas, output_dir=output_dir),
    )


def _admitted_ids(
    admitted: Sequence[EditorialCandidate],
    prepared: PreparedEditorialSource,
) -> tuple[str, ...]:
    admitted_ids = tuple(candidate.asset_id for candidate in admitted)
    if len(admitted_ids) != len(set(admitted_ids)):
        raise ValueError("Structure input candidates must have unique asset IDs")
    unexpected = set(admitted_ids).difference(prepared.candidate_ids)
    if unexpected:
        raise ValueError("Structure input candidates must come from the prepared source")
    return admitted_ids


def _conserved_moments(
    prepared: PreparedEditorialSource,
    admitted_ids: tuple[str, ...],
    *,
    atlas: VisualAtlas | None,
    resolver: Callable[[tuple[EditorialCandidate, ...]], tuple[str, str]] | None,
) -> tuple[StructureMoment, ...]:
    still_here = set(admitted_ids)
    moments = []
    for group in prepared.moment_groups:
        members = tuple(c for c in group.candidates if c.asset_id in still_here)
        if not members:
            continue
        representative, reason = _resolve_representative(members, atlas=atlas, resolver=resolver)
        moments.append(
            StructureMoment(
                moment_id=group.group_id,
                candidates=members,
                representative=representative,
                representative_reason=reason,
            )
        )
    moment_ids = tuple(moment.moment_id for moment in moments)
    if len(moment_ids) != len(set(moment_ids)):
        raise ValueError("Structure moments must have unique IDs")
    grouped_ids = tuple(asset_id for moment in moments for asset_id in moment.candidate_ids)
    if Counter(grouped_ids) != Counter(admitted_ids):
        raise ValueError("Structure workprint must conserve every admitted candidate exactly once")
    return tuple(moments)


def _review_pages(
    moments: tuple[StructureMoment, ...],
    *,
    atlas: VisualAtlas | None,
    output_dir: Path | None,
) -> tuple[ContactSheetPage, ...]:
    if output_dir is None or not moments:
        return ()
    if atlas is None:
        raise ValueError("Structure review pages require a visual atlas")
    return build_contact_sheets(
        tuple(atlas.tile_for(moment.representative.asset_id) for moment in moments),
        scope_id="structure-workprint",
        output_dir=output_dir,
    )


def _resolve_representative(
    candidates: tuple[EditorialCandidate, ...],
    *,
    atlas: VisualAtlas | None,
    resolver: Callable[[tuple[EditorialCandidate, ...]], tuple[str, str]] | None,
) -> tuple[EditorialCandidate, str | None]:
    if resolver is None:
        if atlas is None:
            raise ValueError("Structure needs a visual atlas or representative resolver")
        return _visual_medoid(candidates, atlas), None
    asset_id, reason = resolver(candidates)
    if not reason.strip():
        raise ValueError("Structure representative reason cannot be blank")
    representative = next(
        (candidate for candidate in candidates if candidate.asset_id == asset_id),
        None,
    )
    if representative is None:
        raise ValueError("Structure representative must belong to its conserved moment")
    return representative, reason


def _visual_medoid(
    candidates: tuple[EditorialCandidate, ...], atlas: VisualAtlas
) -> EditorialCandidate:
    """Return the viewable frame with the least total perceptual distance."""
    viewable = tuple(
        candidate
        for candidate in candidates
        if atlas.tile_for(candidate.asset_id).jpeg_bytes is not None
    )
    if not viewable:
        return candidates[0]
    favourites = tuple(candidate for candidate in viewable if candidate.favourite)
    eligible = favourites or viewable
    hashes = {
        candidate.asset_id: compute_thumbnail_hash(
            atlas.tile_for(candidate.asset_id).jpeg_bytes or b"",
            hash_size=16,
        )
        for candidate in viewable
    }
    order = {candidate.asset_id: index for index, candidate in enumerate(candidates)}
    return min(
        eligible,
        key=lambda candidate: (
            sum(
                hamming_distance(hashes[candidate.asset_id], hashes[other.asset_id])
                for other in viewable
            ),
            order[candidate.asset_id],
        ),
    )
