"""Conserved chronological workprint for the production Structure pass."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
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
    atlas: VisualAtlas,
    output_dir: Path | None = None,
) -> StructureWorkprint:
    """Represent every moment that survived Cull while conserving its members."""
    admitted_ids = tuple(candidate.asset_id for candidate in admitted)
    if len(admitted_ids) != len(set(admitted_ids)):
        raise ValueError("Structure input candidates must have unique asset IDs")
    unexpected = set(admitted_ids).difference(prepared.candidate_ids)
    if unexpected:
        raise ValueError("Structure input candidates must come from the prepared source")

    still_here = set(admitted_ids)
    moments = tuple(
        StructureMoment(
            moment_id=group.group_id,
            candidates=members,
            representative=_visual_medoid(members, atlas),
        )
        for group in prepared.moment_groups
        if (members := tuple(c for c in group.candidates if c.asset_id in still_here))
    )
    moment_ids = tuple(moment.moment_id for moment in moments)
    if len(moment_ids) != len(set(moment_ids)):
        raise ValueError("Structure moments must have unique IDs")
    grouped_ids = tuple(asset_id for moment in moments for asset_id in moment.candidate_ids)
    if Counter(grouped_ids) != Counter(admitted_ids):
        raise ValueError("Structure workprint must conserve every admitted candidate exactly once")
    for moment in moments:
        atlas.tile_for(moment.representative.asset_id)
    pages = (
        build_contact_sheets(
            tuple(atlas.tile_for(moment.representative.asset_id) for moment in moments),
            scope_id="structure-workprint",
            output_dir=output_dir,
        )
        if output_dir is not None and moments
        else ()
    )
    return StructureWorkprint(moments=moments, pages=pages)


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
