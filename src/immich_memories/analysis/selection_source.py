"""Acquire and admit the source-eligible corpus every editorial pass reads.

Split out of selection_flow when that file reached 798 of its 800 lines and
Tasks 7-11 all needed to modify it. Roughly nine tenths of it was source
preparation, whose tests already lived in test_selection_source.py, and the
cycle it created was being papered over by a function-local import of the
passes. Nothing here knows a pass exists.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
from operator import itemgetter
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.analysis.editorial_contracts import (
    DecisionProvenance,
    EditorialCandidate,
    LivePhotoRenderingFamily,
    PassTrace,
    SourceEvidence,
    TraceDecision,
    live_photo_rendering_family_id,
)
from immich_memories.analysis.moment_grouping import (
    EPISODE_WINDOW_MINUTES,
    MOMENT_WINDOW_MINUTES,
    group_by_time_and_place,
)
from immich_memories.analysis.selection_trace import Trace
from immich_memories.analysis.source_filter import (
    is_editorial_source_asset,
    live_photo_component_ids,
    not_on_the_timeline,
    not_shot_here,
)
from immich_memories.analysis.source_quality import grounded_source_annotations, is_usable_source
from immich_memories.analysis.visual_atlas import AtlasSource
from immich_memories.api.models import AssetType, VideoClipInfo

if TYPE_CHECKING:
    from immich_memories.api.models import Asset


@dataclass(frozen=True)
class SourceScope:
    """The date and library boundaries used to acquire an editorial source."""

    start_at: datetime | None = None
    end_at: datetime | None = None
    library_ids: tuple[str, ...] = ()
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
    preview_jpeg: Callable[[Asset], bytes | None] | None = None


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
    visual_sources: tuple[AtlasSource, ...]
    rendering_families: tuple[LivePhotoRenderingFamily, ...]
    source_warnings: tuple[str, ...]
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
    sources, normalization_warnings = _coalesce_sources(
        tuple(
            sorted(
                dependencies.source_fetcher(request.scope),
                key=lambda source: (_asset(source).file_created_at, _asset_id(source)),
            )
        )
    )
    components = live_photo_component_ids(_asset(source) for source in sources)
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
    source_warnings = (*normalization_warnings, *family_warnings)
    candidates = tuple(
        sorted(
            (
                _candidate_from(
                    source,
                    stitch_memberships.get(_asset_id(source), ()),
                    rendering_family_ids.get(_asset_id(source)),
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
    episode_groups = build_episode_groups(candidates)
    moment_groups = build_moment_groups(candidates)
    crossing_family_ids = _cross_moment_family_ids(rendering_families, moment_groups)
    if crossing_family_ids:
        source_warnings = (
            *source_warnings,
            *(
                f"!! Live Photo rendering family crosses moment groups: {family_id}"
                for family_id in crossing_family_ids
            ),
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
        moment_groups = build_moment_groups(candidates)
    trace = Trace()
    trace.warnings.extend(source_warnings)
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
    prepared = PreparedEditorialSource(
        candidates=candidates,
        visual_sources=visual_sources,
        rendering_families=rendering_families,
        source_warnings=source_warnings,
        trace=trace,
        episode_groups=episode_groups,
        moment_groups=moment_groups,
    )
    _validate_prepared_source(prepared)
    return prepared


def _candidate_from(
    source: Asset | VideoClipInfo,
    live_photo_stitch_member_ids: tuple[str, ...],
    rendering_family_id: str | None,
    evidence: SourceEvidence | None,
) -> EditorialCandidate:
    asset = source.asset if isinstance(source, VideoClipInfo) else source
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


def _visual_source_from(
    source: Asset | VideoClipInfo,
    preview_jpeg: Callable[[Asset], bytes | None] | None,
) -> AtlasSource:
    asset = _asset(source)
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


def _asset_id(source: Asset | VideoClipInfo) -> str:
    return source.asset.id if isinstance(source, VideoClipInfo) else source.id


def _asset(source: Asset | VideoClipInfo) -> Asset:
    return source.asset if isinstance(source, VideoClipInfo) else source


def _coalesce_sources(
    sources: Sequence[Asset | VideoClipInfo],
) -> tuple[tuple[Asset | VideoClipInfo, ...], tuple[str, ...]]:
    """Keep one canonical source per asset ID, preferring richer clip evidence."""
    coalesced: dict[str, Asset | VideoClipInfo] = {}
    conflicting_render_manifests: set[str] = set()
    warnings: list[str] = []
    for source in sources:
        asset_id = _asset_id(source)
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
            _asset(existing).is_favorite or _asset(source).is_favorite,
        )
    return tuple(coalesced.values()), tuple(warnings)


def _rendering_manifest_signature(source: Asset | VideoClipInfo) -> tuple[object, ...] | None:
    if not isinstance(source, VideoClipInfo) or not _has_rendering_family_evidence(source):
        return None
    manifests = (
        source.live_burst_still_ids,
        source.live_burst_video_ids,
        source.live_burst_trim_points,
        source.live_burst_shutter_timestamps,
    )
    if all(value is not None for value in manifests):
        still_ids, video_ids, trim_points, timestamps = manifests
        assert still_ids is not None
        assert video_ids is not None
        assert trim_points is not None
        assert timestamps is not None
        if len(still_ids) == len(video_ids) == len(trim_points) == len(timestamps):
            return (
                "aligned",
                tuple(
                    sorted(
                        zip(timestamps, still_ids, video_ids, trim_points, strict=True),
                        key=itemgetter(0, 1),
                    )
                ),
            )
    return (
        "invalid",
        tuple(source.live_burst_still_ids or ()),
        tuple(source.live_burst_video_ids or ()),
        tuple(source.live_burst_trim_points or ()),
        tuple(source.live_burst_shutter_timestamps or ()),
    )


def _without_rendering_evidence(source: Asset | VideoClipInfo) -> Asset | VideoClipInfo:
    if not isinstance(source, VideoClipInfo):
        return source
    return source.model_copy(
        update={
            "live_burst_still_ids": None,
            "live_burst_video_ids": None,
            "live_burst_trim_points": None,
            "live_burst_shutter_timestamps": None,
        }
    )


def _with_favourite(source: Asset | VideoClipInfo, favourite: bool) -> Asset | VideoClipInfo:
    asset = _asset(source)
    if asset.is_favorite is favourite:
        return source
    merged_asset = asset.model_copy(update={"is_favorite": favourite})
    if isinstance(source, VideoClipInfo):
        return source.model_copy(update={"asset": merged_asset})
    return merged_asset


def _rendering_family_material(
    sources: Sequence[Asset | VideoClipInfo],
) -> tuple[
    tuple[LivePhotoRenderingFamily, ...],
    dict[str, tuple[str, ...]],
    dict[str, str],
    tuple[str, ...],
]:
    families: dict[str, LivePhotoRenderingFamily] = {}
    family_by_member: dict[str, str] = {}
    warnings: list[str] = []
    conflicting_family_ids: set[str] = set()
    admitted_ids = {_asset_id(source) for source in sources}
    for source in sources:
        if not isinstance(source, VideoClipInfo) or not _has_rendering_family_evidence(source):
            continue
        try:
            family = _rendering_family_from(source, admitted_ids)
        except ValueError as exc:
            warnings.append(f"!! invalid Live Photo rendering family for {source.asset.id}: {exc}")
            continue
        families[family.family_id] = family
        for asset_id in family.still_ids:
            existing = family_by_member.get(asset_id)
            if existing is not None and existing != family.family_id:
                conflicting_family_ids.update((existing, family.family_id))
                warnings.append(
                    f"!! conflicting Live Photo rendering family for admitted asset {asset_id}"
                )
            family_by_member[asset_id] = family.family_id
    if conflicting_family_ids:
        families = {
            family_id: family
            for family_id, family in families.items()
            if family_id not in conflicting_family_ids
        }
        family_by_member = {
            asset_id: family_id
            for asset_id, family_id in family_by_member.items()
            if family_id not in conflicting_family_ids
        }
    memberships = {
        asset_id: families[family_id].still_ids for asset_id, family_id in family_by_member.items()
    }
    return (
        tuple(sorted(families.values(), key=lambda family: family.family_id)),
        memberships,
        family_by_member,
        tuple(dict.fromkeys(warnings)),
    )


def _has_rendering_family_evidence(source: VideoClipInfo) -> bool:
    return any(
        value is not None
        for value in (
            source.live_burst_still_ids,
            source.live_burst_video_ids,
            source.live_burst_trim_points,
            source.live_burst_shutter_timestamps,
        )
    )


def _rendering_family_from(
    source: VideoClipInfo,
    admitted_ids: set[str],
) -> LivePhotoRenderingFamily:
    manifests = (
        source.live_burst_still_ids,
        source.live_burst_video_ids,
        source.live_burst_trim_points,
        source.live_burst_shutter_timestamps,
    )
    if any(value is None for value in manifests):
        raise ValueError("incomplete aligned manifest")
    still_ids, video_ids, trim_points, timestamps = manifests
    assert still_ids is not None
    assert video_ids is not None
    assert trim_points is not None
    assert timestamps is not None
    if not still_ids or not (
        len(still_ids) == len(video_ids) == len(trim_points) == len(timestamps)
    ):
        raise ValueError("unaligned manifest lengths")
    if source.asset.id not in still_ids:
        raise ValueError("enriched source is absent from its still manifest")
    admitted_entries = tuple(
        sorted(
            (
                (timestamp, still_id, video_id, trim_point)
                for still_id, video_id, trim_point, timestamp in zip(
                    still_ids,
                    video_ids,
                    trim_points,
                    timestamps,
                    strict=True,
                )
                if still_id in admitted_ids
            ),
            key=itemgetter(0, 1),
        )
    )
    if not admitted_entries:
        raise ValueError("manifest has no admitted still")
    ordered_timestamps = tuple(entry[0] for entry in admitted_entries)
    ordered_still_ids = tuple(entry[1] for entry in admitted_entries)
    ordered_video_ids = tuple(entry[2] for entry in admitted_entries)
    ordered_trim_points = tuple(entry[3] for entry in admitted_entries)
    family_id = live_photo_rendering_family_id(
        ordered_still_ids,
        ordered_video_ids,
        ordered_trim_points,
        ordered_timestamps,
        motion_duration_seconds=None,
        minimum_motion_seconds=None,
    )
    return LivePhotoRenderingFamily(
        family_id=family_id,
        still_ids=ordered_still_ids,
        video_ids=ordered_video_ids,
        trim_points=ordered_trim_points,
        shutter_timestamps=ordered_timestamps,
    )


def _cross_moment_family_ids(
    families: tuple[LivePhotoRenderingFamily, ...],
    moment_groups: tuple[EditorialGroup, ...],
) -> frozenset[str]:
    moment_by_asset = {
        asset_id: group.group_id for group in moment_groups for asset_id in group.candidate_ids
    }
    return frozenset(
        family.family_id
        for family in families
        if len({moment_by_asset[asset_id] for asset_id in family.still_ids}) > 1
    )


def _without_rendering_family(candidate: EditorialCandidate) -> EditorialCandidate:
    annotations = tuple(
        annotation
        for annotation in candidate.grounded_annotations
        if not annotation.startswith(("live-photo-rendering-family:", "live-photo-stitch-members:"))
    )
    return replace(
        candidate,
        live_photo_stitch_member_ids=(),
        rendering_family_id=None,
        grounded_annotations=annotations,
    )


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
    components: frozenset[str],
) -> str | None:
    asset = _asset(source)
    if not request.scope.include_off_timeline and not_on_the_timeline(asset):
        return "not on the timeline"
    if asset.id in owner_exclusions:
        return "owner exclusion"
    if asset.id in components:
        return "Live Photo component"
    # The resolution-aware rule below supersedes the old still-only EXIF veto.
    # Keeping both made a high-resolution published photo fail before its size
    # could rescue it. Retain the legacy behaviour only when the new provenance
    # rule has been explicitly disabled; filename exclusions always remain.
    legacy_still_exif_veto = (
        request.scope.stills_need_a_camera and request.scope.min_source_short_side <= 0
    )
    if not request.scope.accept_any_provenance and not_shot_here(
        asset,
        patterns=request.scope.excluded_filename_patterns,
        stills_need_a_camera=legacy_still_exif_veto,
    ):
        return "not shot on this camera"
    width, height = _source_dimensions(source)
    if (
        not request.scope.accept_any_provenance
        and request.scope.min_source_short_side > 0
        and not asset.is_favorite
        and not is_usable_source(
            width=width,
            height=height,
            has_camera_exif=_has_camera_exif(asset),
            min_short_side=request.scope.min_source_short_side,
            captured_at=asset.file_created_at,
            original_file_name=asset.original_file_name,
        )
    ):
        return "likely forwarded source without camera provenance"
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


def _source_dimensions(source: Asset | VideoClipInfo) -> tuple[int, int]:
    """Use probed motion dimensions when available, then Immich's asset facts."""
    if isinstance(source, VideoClipInfo) and source.width and source.height:
        return source.width, source.height
    asset = _asset(source)
    return asset.width, asset.height


def _has_camera_exif(asset: Asset) -> bool:
    exif = asset.exif_info
    return bool(exif and (exif.make or exif.model))


def _validate_prepared_source(prepared: PreparedEditorialSource) -> None:
    if set(prepared.candidate_ids).intersection(prepared.excluded_ids):
        raise ValueError("editorial source cannot both admit and exclude an asset")
    visual_ids = tuple(str(source.asset.id) for source in prepared.visual_sources)
    if visual_ids != prepared.candidate_ids:
        raise ValueError("editorial candidates and visual sources must conserve order and identity")
    _validate_rendering_family_references(prepared)


def _validate_rendering_family_references(prepared: PreparedEditorialSource) -> None:
    families = {family.family_id: family for family in prepared.rendering_families}
    if len(families) != len(prepared.rendering_families):
        raise ValueError("editorial rendering family IDs must be unique")
    candidates = {candidate.asset_id: candidate for candidate in prepared.candidates}
    for candidate in prepared.candidates:
        _validate_candidate_family_reference(candidate, families)
    for family in prepared.rendering_families:
        if not _family_members_are_admitted(family, candidates):
            raise ValueError("rendering family may contain only admitted referenced candidates")


def _validate_candidate_family_reference(
    candidate: EditorialCandidate,
    families: dict[str, LivePhotoRenderingFamily],
) -> None:
    if candidate.rendering_family_id is None:
        if candidate.live_photo_stitch_member_ids:
            raise ValueError("diagnostic stitch membership requires a rendering family")
        return
    family = families.get(candidate.rendering_family_id)
    if family is None or candidate.asset_id not in family.still_ids:
        raise ValueError("editorial candidate references an unavailable rendering family")
    if candidate.live_photo_stitch_member_ids != family.still_ids:
        raise ValueError("editorial candidate stitch membership must match its family")


def _family_members_are_admitted(
    family: LivePhotoRenderingFamily,
    candidates: dict[str, EditorialCandidate],
) -> bool:
    return all(
        asset_id in candidates and candidates[asset_id].rendering_family_id == family.family_id
        for asset_id in family.still_ids
    )


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
