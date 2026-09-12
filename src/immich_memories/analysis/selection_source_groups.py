"""Canonical chronological grouping of the admitted corpus, and its projections.

Episodes and the moments nested inside them are built once, from the whole
corpus. A narrower ask never regroups: it projects onto the canonical groups, so
a card keeps the moment the picture actually belonged to instead of one invented
for the request.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

from immich_memories.analysis.editorial_contracts import EditorialCandidate
from immich_memories.analysis.moment_grouping import (
    EPISODE_WINDOW_MINUTES,
    MOMENT_WINDOW_MINUTES,
    group_by_time_and_place,
)
from immich_memories.analysis.selection_source_validation import (
    _validate_conservation,
    _validate_moments_refine_episodes,
    _validate_unique_ids,
)

if TYPE_CHECKING:
    from immich_memories.analysis.selection_source import PreparedEditorialSource


@dataclass(frozen=True)
class EditorialGroup:
    """A chronological source group that has not selected a representative."""

    group_id: str
    candidates: tuple[EditorialCandidate, ...]

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        """Return the ordered IDs represented by this visual group."""
        return tuple(candidate.asset_id for candidate in self.candidates)


@dataclass(frozen=True)
class EditorialGroupProjection:
    """The scoped members of one unchanged full-corpus editorial group."""

    group: EditorialGroup
    scoped_candidate_ids: tuple[str, ...]


def project_episode_groups(
    prepared: PreparedEditorialSource,
    scoped_asset_ids: Sequence[str],
) -> tuple[EditorialGroupProjection, ...]:
    """Project a request onto canonical episodes without changing their identity."""
    return _project_groups(
        prepared.episode_groups,
        scoped_asset_ids,
        prepared_candidate_ids=prepared.candidate_ids,
    )


def project_moment_groups(
    prepared: PreparedEditorialSource,
    scoped_asset_ids: Sequence[str],
) -> tuple[EditorialGroupProjection, ...]:
    """Project a request onto canonical moments without changing their identity."""
    return _project_groups(
        prepared.moment_groups,
        scoped_asset_ids,
        prepared_candidate_ids=prepared.candidate_ids,
    )


def _project_groups(
    groups: Sequence[EditorialGroup],
    scoped_asset_ids: Sequence[str],
    *,
    prepared_candidate_ids: Sequence[str],
) -> tuple[EditorialGroupProjection, ...]:
    scoped = frozenset(scoped_asset_ids)
    if not scoped.issubset(prepared_candidate_ids):
        raise ValueError("scoped asset IDs must belong to prepared corpus")
    projections: list[EditorialGroupProjection] = []
    for group in groups:
        members = tuple(asset_id for asset_id in group.candidate_ids if asset_id in scoped)
        if members:
            projections.append(EditorialGroupProjection(group=group, scoped_candidate_ids=members))
    return tuple(projections)


def build_episode_groups(candidates: Sequence[EditorialCandidate]) -> tuple[EditorialGroup, ...]:
    """Build chronological visual episodes from source-eligible candidates."""
    return _build_groups(candidates, kind="episode", window_minutes=EPISODE_WINDOW_MINUTES)


def _build_moment_groups_within(
    candidates: Sequence[EditorialCandidate],
    episodes: Sequence[EditorialGroup],
) -> tuple[EditorialGroup, ...]:
    ordered = _chronological(candidates)
    _validate_conservation(ordered, episodes)
    groups = tuple(
        sorted(
            (
                moment
                for episode in episodes
                for moment in _build_groups(
                    episode.candidates,
                    kind="moment",
                    window_minutes=MOMENT_WINDOW_MINUTES,
                )
            ),
            key=lambda group: (group.candidates[0].taken_at, group.candidates[0].asset_id),
        )
    )
    _validate_conservation(ordered, groups)
    _validate_moments_refine_episodes(episodes, groups)
    return groups


def _build_groups(
    candidates: Sequence[EditorialCandidate],
    *,
    kind: str,
    window_minutes: float,
) -> tuple[EditorialGroup, ...]:
    ordered = _chronological(candidates)
    _validate_unique_ids(ordered)
    member_groups = tuple(
        sorted(
            group_by_time_and_place(ordered, window_minutes=window_minutes),
            key=lambda members: (members[0].taken_at, members[0].asset_id),
        )
    )
    groups = tuple(
        EditorialGroup(
            group_id=_group_id(kind, members),
            candidates=members,
        )
        for members in member_groups
    )
    _validate_conservation(ordered, groups)
    return groups


def _chronological(
    candidates: Sequence[EditorialCandidate],
) -> tuple[EditorialCandidate, ...]:
    return tuple(sorted(candidates, key=lambda candidate: (candidate.taken_at, candidate.asset_id)))


def _group_id(kind: str, members: tuple[EditorialCandidate, ...]) -> str:
    digest = sha256("\x00".join(candidate.asset_id for candidate in members).encode()).hexdigest()
    return f"{kind}-v1-{digest}"
