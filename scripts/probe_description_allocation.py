"""Prototype conservative admission into the banked-description workprint.

This is not the final cut. It reduces scopes that cannot fit on one text wall
without asking metadata to decide which pictures are good. Positive owner acts
admit; chapters with no owner signal stay broad; relationship and recurrence
repairs prevent a favorite-heavy subject from erasing everybody around them.
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
    """One complete moment and every mechanical reason it reached descriptions."""

    moment: StructureMoment
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DescriptionWorkprint:
    """Chronological moment pool to describe before thesis and selection."""

    admissions: tuple[DescriptionAdmission, ...]
    input_moments: int

    @property
    def moments(self) -> tuple[StructureMoment, ...]:
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
    """Admit a conservative, unranked pool for lifetime visual description."""
    if len(structure.moments) <= reduce_above_moments:
        return DescriptionWorkprint(
            tuple(DescriptionAdmission(moment, ("complete-wall",)) for moment in structure.moments),
            len(structure.moments),
        )

    reasons: dict[str, set[str]] = defaultdict(set)
    by_chapter: dict[Hashable, list[StructureMoment]] = defaultdict(list)
    for moment in structure.moments:
        by_chapter[chapter_key(moment)].append(moment)
        if _has_favourite(moment):
            reasons[moment.moment_id].add("favourite-moment")

    for chapter, moments in by_chapter.items():
        if not any(_has_favourite(moment) for moment in moments):
            for moment in moments:
                reasons[moment.moment_id].add(f"unstarred-chapter:{chapter}")

    wanted_relationships = {name for name in relationship_names if name.strip()}
    for chapter, moments in by_chapter.items():
        for name in wanted_relationships:
            carrying = [moment for moment in moments if name in _people(moment)]
            if carrying and not any(moment.moment_id in reasons for moment in carrying):
                _admit_edges(reasons, carrying, f"relationship-context:{name}:{chapter}")

    appearances: dict[str, list[StructureMoment]] = defaultdict(list)
    for moment in structure.moments:
        for name in _people(moment):
            appearances[name].append(moment)
    gap = timedelta(days=resumption_days)
    for name, moments in appearances.items():
        reasons[moments[0].moment_id].add(f"first-copresence:{name}")
        previous = _taken_at(moments[0])
        for moment in moments[1:]:
            current = _taken_at(moment)
            if current - previous >= gap:
                reasons[moment.moment_id].add(f"resumption:{name}")
            previous = current

    admissions = tuple(
        DescriptionAdmission(moment, tuple(sorted(reasons[moment.moment_id])))
        for moment in structure.moments
        if moment.moment_id in reasons
    )
    return DescriptionWorkprint(admissions, len(structure.moments))


def _admit_edges(
    reasons: dict[str, set[str]],
    moments: list[StructureMoment],
    reason: str,
) -> None:
    reasons[moments[0].moment_id].add(reason)
    if len(moments) > 1:
        reasons[moments[-1].moment_id].add(reason)


def _has_favourite(moment: StructureMoment) -> bool:
    return any(candidate.favourite for candidate in moment.candidates)


def _people(moment: StructureMoment) -> set[str]:
    return {
        str(person.name).strip()
        for candidate in moment.candidates
        for person in (candidate.source.people or ())
        if str(person.name or "").strip()
    }


def _taken_at(moment: StructureMoment):
    return moment.candidates[0].taken_at
