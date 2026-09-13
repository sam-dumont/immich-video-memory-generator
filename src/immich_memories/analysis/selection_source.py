"""Acquire and admit the source-eligible corpus every editorial pass reads.

Split out of selection_flow when that file reached 798 of its 800 lines and
Tasks 7-11 all needed to modify it. Roughly nine tenths of it was source
preparation, whose tests already lived in test_selection_source.py, and the
cycle it created was being papered over by a function-local import of the
passes. Nothing here knows a pass exists.

The request model and the acquisition itself live here. What each admitted
source IS goes to selection_source_rendering, whether it may be admitted at all
to selection_source_validation, and the canonical grouping of what was admitted
to selection_source_groups.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.analysis.editorial_contracts import (
    DecisionProvenance,
    EditorialCandidate,
    LivePhotoRenderingFamily,
    PassTrace,
    SourceEvidence,
    TraceDecision,
)
from immich_memories.analysis.selection_source_groups import (
    EditorialGroup,
    _build_moment_groups_within,
    build_episode_groups,
)
from immich_memories.analysis.selection_source_rendering import (
    _cross_moment_family_ids,
    _rendering_family_material,
    _rendering_manifest_signature,
    _with_favourite,
    _without_rendering_evidence,
    _without_rendering_family,
)
from immich_memories.analysis.selection_source_validation import (
    _source_exclusion_reason,
    _validate_prepared_source,
)
from immich_memories.analysis.selection_trace import Trace
from immich_memories.analysis.source_filter import (
    asset_id_of,
    asset_of,
    live_photo_component_ids,
)
from immich_memories.analysis.source_quality import grounded_source_annotations
from immich_memories.analysis.visual_atlas import AtlasSource
from immich_memories.api.models import AssetType, VideoClipInfo
from immich_memories.timeperiod import DateRange

if TYPE_CHECKING:
    from immich_memories.api.models import Asset


@dataclass(frozen=True)
class PlaceConstraint:
    """Keep only assets provably AT a place: a named city, a GPS radius, or both.

    Either form admits (union), so a home era may state its city and its
    coordinates. An asset with no place evidence never matches -- for a scoped
    ask, unknown place is not presence; the human gate downstream owns the
    recall risk. City names compare case-insensitively against Immich's
    ingest-time reverse geocoding, the ruled place source (2026-08-31).
    """

    cities: tuple[str, ...] = ()
    center: tuple[float, float] | None = None
    radius_km: float = 0.0


@dataclass(frozen=True)
class SourceScope:
    """The date and library boundaries used to acquire an editorial source."""

    start_at: datetime | None = None
    end_at: datetime | None = None
    date_ranges: tuple[DateRange, ...] = ()
    library_ids: tuple[str, ...] = ()
    # None preserves date-only acquisition; an explicit membership never expands.
    asset_ids: tuple[str, ...] | None = None
    # Provenance, not quality: whether a file came off this library's camera is
    # a fact about the file, so it settles scope rather than waiting for a pass
    # to judge it. Every other pool builder asks the same question here.
    excluded_filename_patterns: tuple[str, ...] = ()
    stills_need_a_camera: bool = False
    # A renamed messaging re-encode can evade the filename patterns, especially
    # for video. Low resolution plus absent camera metadata is the measured
    # provenance signal; either fact on its own remains insufficient.
    min_source_short_side: int = 1080
    # A conscious per-memory override for sources where received media is the
    # record (for example, a nephew spotlight assembled from family forwards).
    # This never relaxes date/person/library scope, privacy, owner exclusions,
    # or Live Photo component handling.
    accept_any_provenance: bool = False
    # Visibility, not provenance: whether Immich shows an asset on the timeline
    # at all. Off by default and hard-coded off at generation, so pointing
    # analysis at the archive on purpose stays possible without a forgotten
    # setting reaching a video.
    include_off_timeline: bool = False
    # Scope, like the library boundary: where the ask says the memory happened.
    # None means the ask names no place, which admits everything.
    place: PlaceConstraint | None = None

    def __post_init__(self) -> None:
        if self.date_ranges and (self.start_at is not None or self.end_at is not None):
            raise ValueError("source scope uses either exact date ranges or one continuous range")
        if any(window.start > window.end for window in self.date_ranges):
            raise ValueError("source date ranges must start before they end")


@dataclass(frozen=True)
class EditorialSelectionRequest:
    """The source boundary and owner choices for one editorial selection."""

    scope: SourceScope
    owner_excluded_asset_ids: tuple[str, ...] = ()
    # Pictures the owner ticked after seeing a cut: admitted after the read, never argued
    # about again. A post-read signal, so it changes no prompt and no digest input.
    owner_required_asset_ids: tuple[str, ...] = ()
    evidence_exclusions: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EditorialDependencies:
    """External acquisition required before the pure editorial passes begin."""

    source_fetcher: Callable[[SourceScope], Sequence[Asset | VideoClipInfo]]
    library_membership: Callable[[Asset, tuple[str, ...]], bool] | None = None
    source_evidence: Callable[[Asset | VideoClipInfo], SourceEvidence | None] | None = None
    preview_jpeg: Callable[[Asset], bytes | None] | None = None


@dataclass(frozen=True)
class PreparedEditorialSource:
    """Chronological candidates and their admission record for subsequent passes."""

    candidates: tuple[EditorialCandidate, ...]
    visual_sources: tuple[AtlasSource, ...]
    rendering_families: tuple[LivePhotoRenderingFamily, ...]
    source_warnings: tuple[str, ...]
    trace: Trace
    episode_groups: tuple[EditorialGroup, ...]
    moment_groups: tuple[EditorialGroup, ...]
    owner_required_asset_ids: tuple[str, ...] = ()

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


@dataclass(frozen=True)
class _GroupedCorpus:
    """The corpus after grouping, with any straddling rendering family taken off it."""

    candidates: tuple[EditorialCandidate, ...]
    rendering_families: tuple[LivePhotoRenderingFamily, ...]
    episode_groups: tuple[EditorialGroup, ...]
    moment_groups: tuple[EditorialGroup, ...]
    warnings: tuple[str, ...]


def prepare_editorial_source(
    request: EditorialSelectionRequest,
    dependencies: EditorialDependencies,
    *,
    trace: Trace | None = None,
) -> PreparedEditorialSource:
    """Acquire, normalize, and admit the complete source-eligible corpus."""
    excluded = set(request.owner_excluded_asset_ids)
    sources, normalization_warnings = _coalesce_sources(
        tuple(
            sorted(
                dependencies.source_fetcher(request.scope),
                key=lambda source: (asset_of(source).file_created_at, asset_id_of(source)),
            )
        )
    )
    components = live_photo_component_ids(asset_of(source) for source in sources)
    source_decisions = tuple(
        (source, _source_exclusion_reason(source, request, dependencies, excluded, components))
        for source in sources
    )
    eligible_sources = tuple(
        source for source, exclusion_reason in source_decisions if exclusion_reason is None
    )
    (
        rendering_families,
        stitch_memberships,
        rendering_family_ids,
        family_warnings,
    ) = _rendering_family_material(eligible_sources)
    candidates = tuple(
        sorted(
            (
                _candidate_from(
                    source,
                    stitch_memberships.get(asset_id_of(source), ()),
                    rendering_family_ids.get(asset_id_of(source)),
                    dependencies.source_evidence(source)
                    if dependencies.source_evidence is not None
                    else None,
                )
                for source in eligible_sources
            ),
            key=lambda candidate: (candidate.taken_at, candidate.asset_id),
        )
    )
    visual_sources = tuple(
        _visual_source_from(source, dependencies.preview_jpeg) for source in eligible_sources
    )
    grouped = _grouped_corpus(candidates, rendering_families)
    source_warnings = (*normalization_warnings, *family_warnings, *grouped.warnings)
    trace = Trace() if trace is None else trace
    trace.warnings.extend(source_warnings)
    source_ids = tuple(asset_id_of(source) for source in sources)
    trace.record_editorial_pass(
        PassTrace(
            name="source-eligibility",
            input_ids=source_ids,
            kept_ids=tuple(candidate.asset_id for candidate in grouped.candidates),
            rejected=tuple(
                TraceDecision(asset_id_of(source), reason)
                for source, reason in source_decisions
                if reason is not None
            ),
            unresolved=(),
            duration_before=sum(_duration_of(source) for source in sources),
            duration_after=sum(candidate.shippable_duration for candidate in grouped.candidates),
            provenance=_source_provenance("source-eligibility", source_ids),
        )
    )
    prepared = PreparedEditorialSource(
        candidates=grouped.candidates,
        visual_sources=visual_sources,
        rendering_families=grouped.rendering_families,
        source_warnings=source_warnings,
        trace=trace,
        episode_groups=grouped.episode_groups,
        moment_groups=grouped.moment_groups,
        owner_required_asset_ids=_required_in_pool(request, grouped.candidates, trace),
    )
    _validate_prepared_source(prepared)
    return prepared


def _grouped_corpus(
    candidates: tuple[EditorialCandidate, ...],
    rendering_families: tuple[LivePhotoRenderingFamily, ...],
) -> _GroupedCorpus:
    """Group the admitted corpus, dropping any family that straddles two moments.

    A family whose stills land in different moments cannot render as one clip
    without moving a picture out of the moment it belongs to, so the family is
    withdrawn and its stills regrouped as ordinary photographs.
    """
    episode_groups = build_episode_groups(candidates)
    moment_groups = _build_moment_groups_within(candidates, episode_groups)
    crossing_family_ids = _cross_moment_family_ids(rendering_families, moment_groups)
    if not crossing_family_ids:
        return _GroupedCorpus(candidates, rendering_families, episode_groups, moment_groups, ())
    warnings = tuple(
        f"!! Live Photo rendering family crosses moment groups: {family_id}"
        for family_id in crossing_family_ids
    )
    rendering_families = tuple(
        family for family in rendering_families if family.family_id not in crossing_family_ids
    )
    candidates = tuple(
        _without_rendering_family(candidate)
        if candidate.rendering_family_id in crossing_family_ids
        else candidate
        for candidate in candidates
    )
    episode_groups = build_episode_groups(candidates)
    moment_groups = _build_moment_groups_within(candidates, episode_groups)
    return _GroupedCorpus(candidates, rendering_families, episode_groups, moment_groups, warnings)


def _candidate_from(
    source: Asset | VideoClipInfo,
    live_photo_stitch_member_ids: tuple[str, ...],
    rendering_family_id: str | None,
    evidence: SourceEvidence | None,
) -> EditorialCandidate:
    asset = asset_of(source)
    is_video = asset.type == AssetType.VIDEO
    grounded_annotations = grounded_source_annotations(
        asset,
        source if isinstance(source, VideoClipInfo) else None,
        evidence,
    )
    if rendering_family_id is not None:
        grounded_annotations = (
            *grounded_annotations,
            f"live-photo-rendering-family:{rendering_family_id}",
            f"live-photo-stitch-members:{','.join(live_photo_stitch_member_ids)}",
        )
    return EditorialCandidate(
        asset_id=asset.id,
        taken_at=asset.file_created_at,
        media_kind=("live_photo" if asset.is_live_photo else "video" if is_video else "photo"),
        live_photo_stitch_member_ids=live_photo_stitch_member_ids,
        rendering_family_id=rendering_family_id,
        favourite=asset.is_favorite,
        source=asset,
        proposed_segment=None,
        shippable_duration=(
            source.duration_seconds
            if isinstance(source, VideoClipInfo)
            else (asset.duration_seconds or 0.0)
        ),
        grounded_annotations=grounded_annotations,
    )


def _required_in_pool(
    request: EditorialSelectionRequest,
    candidates: tuple[EditorialCandidate, ...],
    trace: Trace,
) -> tuple[str, ...]:
    """Keep the required ids the pool can honour; name the ones it cannot."""
    in_pool = {candidate.asset_id for candidate in candidates}
    for asset_id in request.owner_required_asset_ids:
        if asset_id not in in_pool:
            trace.warnings.append(f"owner required picture is not in the eligible pool: {asset_id}")
    return tuple(asset_id for asset_id in request.owner_required_asset_ids if asset_id in in_pool)


def _visual_source_from(
    source: Asset | VideoClipInfo,
    preview_jpeg: Callable[[Asset], bytes | None] | None,
) -> AtlasSource:
    asset = asset_of(source)
    preview: bytes | None = None
    unavailable_reason: str | None = None
    if preview_jpeg is not None:
        try:
            preview = preview_jpeg(asset)
        except Exception as exc:  # WHY: one failed external preview read cannot abort the corpus
            unavailable_reason = (
                f"preview provider raised {type(exc).__name__} and no usable motion frames"
            )
    return AtlasSource(
        asset=asset,
        preview_jpeg=preview,
        motion_path=(
            Path(source.local_path)
            if isinstance(source, VideoClipInfo) and source.local_path is not None
            else None
        ),
        unavailable_reason=unavailable_reason,
    )


def _coalesce_sources(
    sources: Sequence[Asset | VideoClipInfo],
) -> tuple[tuple[Asset | VideoClipInfo, ...], tuple[str, ...]]:
    """Keep one canonical source per asset ID, preferring richer clip evidence."""
    coalesced: dict[str, Asset | VideoClipInfo] = {}
    conflicting_render_manifests: set[str] = set()
    warnings: list[str] = []
    for source in sources:
        asset_id = asset_id_of(source)
        existing = coalesced.get(asset_id)
        if existing is None:
            coalesced[asset_id] = source
            continue
        if _asset_signature(existing) != _asset_signature(source):
            raise ValueError(f"conflicting source representations for asset {asset_id}")
        preferred = (
            source if _source_evidence_rank(source) > _source_evidence_rank(existing) else existing
        )
        existing_manifest = _rendering_manifest_signature(existing)
        source_manifest = _rendering_manifest_signature(source)
        if (
            existing_manifest is not None
            and source_manifest is not None
            and existing_manifest != source_manifest
            and asset_id not in conflicting_render_manifests
        ):
            conflicting_render_manifests.add(asset_id)
            warnings.append(
                f"!! conflicting Live Photo rendering manifests for duplicate asset {asset_id}"
            )
        coalesced[asset_id] = _with_favourite(
            _without_rendering_evidence(preferred)
            if asset_id in conflicting_render_manifests
            else preferred,
            asset_of(existing).is_favorite or asset_of(source).is_favorite,
        )
    return tuple(coalesced.values()), tuple(warnings)


def _asset_signature(source: Asset | VideoClipInfo) -> tuple[object, ...]:
    """Identify the immutable asset facts that must agree across representations."""
    asset = asset_of(source)
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
