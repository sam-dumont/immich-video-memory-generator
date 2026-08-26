"""Prepare source-eligible visuals for the editorial selection passes."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import TYPE_CHECKING

from immich_memories.analysis.editorial_contracts import (
    DecisionProvenance,
    EditorialCandidate,
    PassTrace,
)
from immich_memories.analysis.moment_grouping import (
    EPISODE_WINDOW_MINUTES,
    MOMENT_WINDOW_MINUTES,
    group_by_time_and_place,
)
from immich_memories.analysis.selection_trace import Trace
from immich_memories.analysis.source_filter import is_editorial_source_asset
from immich_memories.analysis.source_quality import grounded_source_annotations
from immich_memories.api.models import AssetType, VideoClipInfo

if TYPE_CHECKING:
    from immich_memories.api.models import Asset


@dataclass(frozen=True)
class SourceScope:
    """The date and library boundaries used to acquire an editorial source."""

    start_at: datetime | None = None
    end_at: datetime | None = None
    library_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EditorialSelectionRequest:
    """The source boundary and owner choices for one editorial selection."""

    scope: SourceScope
    owner_excluded_asset_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EditorialDependencies:
    """External acquisition required before the pure editorial passes begin."""

    source_fetcher: Callable[[SourceScope], Sequence[Asset | VideoClipInfo]]


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
class PreparedEditorialSource:
    """Chronological candidates and their admission record for subsequent passes."""

    candidates: tuple[EditorialCandidate, ...]
    excluded_ids: tuple[str, ...]
    trace: Trace
    episode_groups: tuple[EditorialGroup, ...]
    moment_groups: tuple[EditorialGroup, ...]

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        """Return the stable source order without exposing mutable collection state."""
        return tuple(candidate.asset_id for candidate in self.candidates)


def prepare_editorial_source(
    request: EditorialSelectionRequest,
    dependencies: EditorialDependencies,
) -> PreparedEditorialSource:
    """Acquire, normalize, and admit the complete source-eligible corpus."""
    excluded = set(request.owner_excluded_asset_ids)
    sources = tuple(dependencies.source_fetcher(request.scope))
    candidates = tuple(
        sorted(
            (
                _candidate_from(source)
                for source in sources
                if _asset_id(source) not in excluded
                and is_editorial_source_asset(
                    _asset(source),
                    start_at=request.scope.start_at,
                    end_at=request.scope.end_at,
                )
            ),
            key=lambda candidate: (candidate.taken_at, candidate.asset_id),
        )
    )
    episode_groups = build_episode_groups(candidates)
    moment_groups = build_moment_groups(candidates)
    trace = Trace()
    candidate_ids = tuple(candidate.asset_id for candidate in candidates)
    trace.record_editorial_pass(
        PassTrace(
            name="pass-0",
            input_ids=candidate_ids,
            kept_ids=candidate_ids,
            rejected=(),
            unresolved=(),
            duration_before=sum(candidate.shippable_duration for candidate in candidates),
            duration_after=sum(candidate.shippable_duration for candidate in candidates),
            provenance=DecisionProvenance(
                pass_name="pass-0",  # noqa: S106 - public editorial pass identity
                pass_version="1",  # noqa: S106 - public pass version
                schema_version="1",
                model_identity="",
                input_ids=candidate_ids,
                sheet_hashes=(),
                request_key="source-eligibility",
                cache_hit=False,
            ),
        )
    )
    return PreparedEditorialSource(
        candidates=candidates,
        excluded_ids=tuple(
            _asset_id(source) for source in sources if _asset_id(source) in excluded
        ),
        trace=trace,
        episode_groups=episode_groups,
        moment_groups=moment_groups,
    )


def _candidate_from(source: Asset | VideoClipInfo) -> EditorialCandidate:
    asset = source.asset if isinstance(source, VideoClipInfo) else source
    is_video = asset.type == AssetType.VIDEO
    return EditorialCandidate(
        asset_id=asset.id,
        taken_at=asset.file_created_at,
        media_kind=("live_photo" if asset.is_live_photo else "video" if is_video else "photo"),
        favourite=asset.is_favorite,
        source=asset,
        proposed_segment=None,
        shippable_duration=(
            source.duration_seconds
            if isinstance(source, VideoClipInfo)
            else (asset.duration_seconds or 0.0)
        ),
        grounded_annotations=grounded_source_annotations(
            asset,
            source if isinstance(source, VideoClipInfo) else None,
        ),
    )


def _asset_id(source: Asset | VideoClipInfo) -> str:
    return source.asset.id if isinstance(source, VideoClipInfo) else source.id


def _asset(source: Asset | VideoClipInfo) -> Asset:
    return source.asset if isinstance(source, VideoClipInfo) else source


def build_episode_groups(candidates: Sequence[EditorialCandidate]) -> tuple[EditorialGroup, ...]:
    """Build chronological visual episodes from source-eligible candidates."""
    return _build_groups(candidates, kind="episode", window_minutes=EPISODE_WINDOW_MINUTES)


def build_moment_groups(candidates: Sequence[EditorialCandidate]) -> tuple[EditorialGroup, ...]:
    """Build chronological visual moments from source-eligible candidates."""
    return _build_groups(candidates, kind="moment", window_minutes=MOMENT_WINDOW_MINUTES)


def _build_groups(
    candidates: Sequence[EditorialCandidate],
    *,
    kind: str,
    window_minutes: float,
) -> tuple[EditorialGroup, ...]:
    ordered = tuple(
        sorted(candidates, key=lambda candidate: (candidate.taken_at, candidate.asset_id))
    )
    _validate_unique_ids(ordered)
    groups = tuple(
        EditorialGroup(
            group_id=_group_id(kind, members),
            candidates=members,
        )
        for members in group_by_time_and_place(ordered, window_minutes=window_minutes)
    )
    _validate_conservation(ordered, groups)
    return groups


def _group_id(kind: str, members: tuple[EditorialCandidate, ...]) -> str:
    digest = sha256("\x00".join(candidate.asset_id for candidate in members).encode()).hexdigest()
    return f"{kind}-v1-{digest}"


def _validate_unique_ids(candidates: Sequence[EditorialCandidate]) -> None:
    asset_ids = tuple(candidate.asset_id for candidate in candidates)
    if len(asset_ids) != len(set(asset_ids)):
        raise ValueError("editorial candidates must have unique asset IDs")


def _validate_conservation(
    candidates: Sequence[EditorialCandidate], groups: Sequence[EditorialGroup]
) -> None:
    expected = tuple(candidate.asset_id for candidate in candidates)
    actual = tuple(asset_id for group in groups for asset_id in group.candidate_ids)
    if actual != expected:
        raise ValueError("editorial grouping must conserve every candidate in chronological order")
