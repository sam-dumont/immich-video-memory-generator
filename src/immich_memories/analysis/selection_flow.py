"""Prepare source-eligible visuals for the editorial selection passes."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import TYPE_CHECKING

from immich_memories.analysis.editorial_contracts import (
    DecisionProvenance,
    EditorialCandidate,
    PassTrace,
    SourceEvidence,
    TraceDecision,
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
    library_membership: Callable[[Asset, tuple[str, ...]], bool] | None = None
    source_evidence: Callable[[Asset | VideoClipInfo], SourceEvidence | None] | None = None


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
    trace: Trace
    episode_groups: tuple[EditorialGroup, ...]
    moment_groups: tuple[EditorialGroup, ...]

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        """Return the stable source order without exposing mutable collection state."""
        return tuple(candidate.asset_id for candidate in self.candidates)

    @property
    def excluded_ids(self) -> tuple[str, ...]:
        """Return exclusions from the durable source-eligibility trace."""
        source_pass = next(
            pass_trace
            for pass_trace in self.trace.editorial_passes
            if pass_trace.name == "source-eligibility"
        )
        return tuple(decision.asset_id for decision in source_pass.rejected)


def prepare_editorial_source(
    request: EditorialSelectionRequest,
    dependencies: EditorialDependencies,
) -> PreparedEditorialSource:
    """Acquire, normalize, and admit the complete source-eligible corpus."""
    excluded = set(request.owner_excluded_asset_ids)
    sources = _coalesce_sources(
        tuple(
            sorted(
                dependencies.source_fetcher(request.scope),
                key=lambda source: (_asset(source).file_created_at, _asset_id(source)),
            )
        )
    )
    source_decisions = tuple(
        (source, _source_exclusion_reason(source, request, dependencies, excluded))
        for source in sources
    )
    eligible_sources = tuple(
        source for source, exclusion_reason in source_decisions if exclusion_reason is None
    )
    candidates = tuple(
        sorted(
            (
                _candidate_from(
                    source,
                    dependencies.source_evidence(source)
                    if dependencies.source_evidence is not None
                    else None,
                )
                for source in eligible_sources
            ),
            key=lambda candidate: (candidate.taken_at, candidate.asset_id),
        )
    )
    episode_groups = build_episode_groups(candidates)
    moment_groups = build_moment_groups(candidates)
    trace = Trace()
    candidate_ids = tuple(candidate.asset_id for candidate in candidates)
    source_ids = tuple(_asset_id(source) for source in sources)
    trace.record_editorial_pass(
        PassTrace(
            name="source-eligibility",
            input_ids=source_ids,
            kept_ids=candidate_ids,
            rejected=tuple(
                TraceDecision(_asset_id(source), reason)
                for source, reason in source_decisions
                if reason is not None
            ),
            unresolved=(),
            duration_before=sum(_duration_of(source) for source in sources),
            duration_after=sum(candidate.shippable_duration for candidate in candidates),
            provenance=_source_provenance("source-eligibility", source_ids),
        )
    )
    trace.record_editorial_pass(
        PassTrace(
            name="pass-0",
            input_ids=candidate_ids,
            kept_ids=candidate_ids,
            rejected=(),
            unresolved=(),
            duration_before=sum(candidate.shippable_duration for candidate in candidates),
            duration_after=sum(candidate.shippable_duration for candidate in candidates),
            provenance=_source_provenance("pass-0", candidate_ids),
        )
    )
    prepared = PreparedEditorialSource(
        candidates=candidates,
        trace=trace,
        episode_groups=episode_groups,
        moment_groups=moment_groups,
    )
    _validate_prepared_source(prepared)
    return prepared


def _candidate_from(
    source: Asset | VideoClipInfo,
    evidence: SourceEvidence | None,
) -> EditorialCandidate:
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
            evidence,
        ),
    )


def _asset_id(source: Asset | VideoClipInfo) -> str:
    return source.asset.id if isinstance(source, VideoClipInfo) else source.id


def _asset(source: Asset | VideoClipInfo) -> Asset:
    return source.asset if isinstance(source, VideoClipInfo) else source


def _coalesce_sources(
    sources: Sequence[Asset | VideoClipInfo],
) -> tuple[Asset | VideoClipInfo, ...]:
    """Keep one canonical source per asset ID, preferring richer clip evidence."""
    coalesced: dict[str, Asset | VideoClipInfo] = {}
    for source in sources:
        asset_id = _asset_id(source)
        existing = coalesced.get(asset_id)
        if existing is None:
            coalesced[asset_id] = source
            continue
        if _asset_signature(existing) != _asset_signature(source):
            raise ValueError(f"conflicting source representations for asset {asset_id}")
        if _source_evidence_rank(source) > _source_evidence_rank(existing):
            coalesced[asset_id] = source
    return tuple(coalesced.values())


def _asset_signature(source: Asset | VideoClipInfo) -> tuple[object, ...]:
    """Identify the immutable asset facts that must agree across representations."""
    asset = _asset(source)
    return (
        asset.owner_id,
        asset.device_asset_id,
        asset.type,
        asset.file_created_at,
        asset.original_path,
        asset.original_file_name,
        asset.original_mime_type,
        asset.checksum,
        asset.live_photo_video_id,
    )


def _source_evidence_rank(source: Asset | VideoClipInfo) -> tuple[float, ...]:
    if not isinstance(source, VideoClipInfo):
        return (0.0, 0.0, 0.0, 0.0, 0.0)
    return (
        1.0,
        source.duration_seconds,
        float(source.width * source.height),
        float(len(source.live_burst_still_ids or ())),
        float(sum(value is not None for value in (source.llm_category, source.llm_quality))),
    )


def _source_exclusion_reason(
    source: Asset | VideoClipInfo,
    request: EditorialSelectionRequest,
    dependencies: EditorialDependencies,
    owner_exclusions: set[str],
) -> str | None:
    asset = _asset(source)
    if asset.id in owner_exclusions:
        return "owner exclusion"
    if request.scope.library_ids:
        if dependencies.library_membership is None:
            raise ValueError("library scope requires a library_membership dependency")
        if not dependencies.library_membership(asset, request.scope.library_ids):
            return "outside library scope"
    if not is_editorial_source_asset(
        asset,
        start_at=request.scope.start_at,
        end_at=request.scope.end_at,
    ):
        return _scope_exclusion_reason(asset, request.scope)
    return None


def _validate_prepared_source(prepared: PreparedEditorialSource) -> None:
    if set(prepared.candidate_ids).intersection(prepared.excluded_ids):
        raise ValueError("editorial source cannot both admit and exclude an asset")


def _scope_exclusion_reason(asset: Asset, scope: SourceScope) -> str:
    from immich_memories.api.models import AssetType

    if asset.type not in (AssetType.IMAGE, AssetType.VIDEO):
        return "unsupported media"
    if scope.start_at is not None and asset.file_created_at < scope.start_at:
        return "outside date scope"
    if scope.end_at is not None and asset.file_created_at > scope.end_at:
        return "outside date scope"
    return "missing capture time"


def _duration_of(source: Asset | VideoClipInfo) -> float:
    return (
        source.duration_seconds
        if isinstance(source, VideoClipInfo)
        else (source.duration_seconds or 0.0)
    )


def _source_provenance(pass_name: str, input_ids: tuple[str, ...]) -> DecisionProvenance:
    return DecisionProvenance(
        pass_name=pass_name,  # noqa: S106 - public source pass identity
        pass_version="1",  # noqa: S106 - public pass version
        schema_version="1",
        model_identity="",
        input_ids=input_ids,
        sheet_hashes=(),
        request_key="source-eligibility",
        cache_hit=False,
    )


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
    expected_ids = tuple(candidate.asset_id for candidate in candidates)
    grouped_ids = tuple(asset_id for group in groups for asset_id in group.candidate_ids)
    if Counter(grouped_ids) != Counter(expected_ids):
        raise ValueError("editorial grouping must conserve every candidate exactly once")
    for group in groups:
        ordered_members = tuple(
            sorted(group.candidates, key=lambda candidate: (candidate.taken_at, candidate.asset_id))
        )
        if group.candidates != ordered_members:
            raise ValueError("editorial group members must be chronological")
    ordered_groups = tuple(
        sorted(
            groups, key=lambda group: (group.candidates[0].taken_at, group.candidates[0].asset_id)
        )
    )
    if tuple(groups) != ordered_groups:
        raise ValueError("editorial groups must be ordered by their first candidate")
