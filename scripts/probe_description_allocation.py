"""Prototype conservative projection into the banked-description workprint.

This is not the final cut. It reduces scopes that cannot fit on one text wall
without asking metadata to decide which pictures are good. Positive owner acts
compress only the chapters they cover; chapters with no owner signal stay broad;
relationship and recurrence repairs prevent a favorite-heavy subject from erasing
everybody around them. The complete Structure moment remains the later reservoir.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Collection, Hashable
from dataclasses import dataclass
from datetime import timedelta

from immich_memories.analysis.editorial_contracts import EditorialCandidate
from immich_memories.analysis.selection_structure import StructureMoment, StructureWorkprint


@dataclass(frozen=True)
class DescriptionAdmission:
    """A complete moment plus the exact members projected for description."""

    moment: StructureMoment
    evidence_candidates: tuple[EditorialCandidate, ...]
    reasons: tuple[str, ...]

    @property
    def projected_moment(self) -> StructureMoment:
        """Expose only described evidence without mutating the Structure reservoir."""
        evidence_ids = {candidate.asset_id for candidate in self.evidence_candidates}
        representative = (
            self.moment.representative
            if self.moment.representative.asset_id in evidence_ids
            else self.evidence_candidates[0]
        )
        return StructureMoment(
            moment_id=self.moment.moment_id,
            candidates=self.evidence_candidates,
            representative=representative,
        )


@dataclass(frozen=True)
class DescriptionWorkprint:
    """Chronological moment pool to describe before thesis and selection."""

    admissions: tuple[DescriptionAdmission, ...]
    input_moments: int
    input_assets: int

    @property
    def moments(self) -> tuple[StructureMoment, ...]:
        """Return projected moments for the description/card wall."""
        return tuple(admission.projected_moment for admission in self.admissions)

    @property
    def reservoir_moments(self) -> tuple[StructureMoment, ...]:
        """Return complete Structure membership retained behind the projection."""
        return tuple(admission.moment for admission in self.admissions)

    @property
    def candidates(self) -> tuple[EditorialCandidate, ...]:
        return tuple(candidate for moment in self.moments for candidate in moment.candidates)


def build_description_workprint(
    structure: StructureWorkprint,
    *,
    chapter_key: Callable[[StructureMoment], Hashable],
    relationship_names: Collection[str] = (),
    reduce_above_moments: int = 160,
    resumption_days: int = 730,
) -> DescriptionWorkprint:
    """Project a conservative, unranked pool for lifetime visual description."""
    input_assets = sum(len(moment.candidates) for moment in structure.moments)
    if len(structure.moments) <= reduce_above_moments:
        return DescriptionWorkprint(
            tuple(
                DescriptionAdmission(moment, moment.candidates, ("complete-wall",))
                for moment in structure.moments
            ),
            len(structure.moments),
            input_assets,
        )

    reasons: dict[str, set[str]] = defaultdict(set)
    evidence_ids: dict[str, set[str]] = defaultdict(set)
    by_chapter: dict[Hashable, list[StructureMoment]] = defaultdict(list)
    for moment in structure.moments:
        by_chapter[chapter_key(moment)].append(moment)

    for chapter, moments in by_chapter.items():
        favourite_candidates = tuple(
            (moment, candidate)
            for moment in moments
            for candidate in moment.candidates
            if candidate.favourite
        )
        if favourite_candidates:
            for moment, candidate in favourite_candidates:
                _admit_candidate(
                    reasons,
                    evidence_ids,
                    moment,
                    candidate,
                    f"favourite-evidence:{chapter}",
                )
        else:
            for moment in moments:
                for candidate in moment.candidates:
                    _admit_candidate(
                        reasons,
                        evidence_ids,
                        moment,
                        candidate,
                        f"unstarred-chapter:{chapter}",
                    )

    wanted_relationships = tuple(sorted({name for name in relationship_names if name.strip()}))
    for chapter, moments in by_chapter.items():
        for name in wanted_relationships:
            carrying = [
                (moment, candidate)
                for moment in moments
                for candidate in moment.candidates
                if name in _candidate_people(candidate)
            ]
            visible = any(
                candidate.asset_id in evidence_ids[moment.moment_id]
                for moment, candidate in carrying
            )
            if carrying and not visible:
                _admit_edges(
                    reasons,
                    evidence_ids,
                    carrying,
                    f"relationship-context:{name}:{chapter}",
                )

    appearances: dict[str, list[tuple[StructureMoment, EditorialCandidate]]] = defaultdict(list)
    for moment in structure.moments:
        first_in_moment: dict[str, EditorialCandidate] = {}
        for candidate in moment.candidates:
            for name in _candidate_people(candidate):
                first_in_moment.setdefault(name, candidate)
        for name, candidate in first_in_moment.items():
            appearances[name].append((moment, candidate))
    gap = timedelta(days=resumption_days)
    for name, sightings in appearances.items():
        first_moment, first_candidate = sightings[0]
        _admit_candidate(
            reasons,
            evidence_ids,
            first_moment,
            first_candidate,
            f"first-copresence:{name}",
        )
        previous = first_candidate.taken_at
        for moment, candidate in sightings[1:]:
            current = candidate.taken_at
            if current - previous >= gap:
                _admit_candidate(
                    reasons,
                    evidence_ids,
                    moment,
                    candidate,
                    f"resumption:{name}",
                )
            previous = current

    admissions = tuple(
        DescriptionAdmission(
            moment,
            tuple(
                candidate
                for candidate in moment.candidates
                if candidate.asset_id in evidence_ids[moment.moment_id]
            ),
            tuple(sorted(reasons[moment.moment_id])),
        )
        for moment in structure.moments
        if evidence_ids[moment.moment_id]
    )
    return DescriptionWorkprint(admissions, len(structure.moments), input_assets)


def _admit_candidate(
    reasons: dict[str, set[str]],
    evidence_ids: dict[str, set[str]],
    moment: StructureMoment,
    candidate: EditorialCandidate,
    reason: str,
) -> None:
    reasons[moment.moment_id].add(reason)
    evidence_ids[moment.moment_id].add(candidate.asset_id)


def _admit_edges(
    reasons: dict[str, set[str]],
    evidence_ids: dict[str, set[str]],
    sightings: list[tuple[StructureMoment, EditorialCandidate]],
    reason: str,
) -> None:
    _admit_candidate(reasons, evidence_ids, *sightings[0], reason)
    if len(sightings) > 1:
        _admit_candidate(reasons, evidence_ids, *sightings[-1], reason)


def _candidate_people(candidate: EditorialCandidate) -> set[str]:
    return {
        str(person.name).strip()
        for person in (candidate.source.people or ())
        if str(person.name or "").strip()
    }
