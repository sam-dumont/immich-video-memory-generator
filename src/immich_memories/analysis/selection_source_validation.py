"""Whether a source may be admitted, and what the admitted corpus must satisfy.

Two halves of one question. The admission rules decide, per source, whether the
ask reaches it at all -- exact membership, visibility, owner and evidence
exclusions, provenance, library, place and date. The invariants then hold the
finished corpus to what those rules promised: nothing both admitted and
excluded, candidates and visual sources in step, moments refining episodes, and
every rendering family referencing only admitted members.

Neither half knows a pass exists, and neither builds anything.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import TYPE_CHECKING

from immich_memories.analysis.source_filter import (
    asset_of,
    is_editorial_source_asset,
    not_on_the_timeline,
    not_shot_here,
)
from immich_memories.analysis.source_quality import forwarded_source_refusal
from immich_memories.analysis.trip_detection import haversine_km
from immich_memories.api.models import Asset, VideoClipInfo

if TYPE_CHECKING:
    from immich_memories.analysis.editorial_contracts import (
        EditorialCandidate,
        LivePhotoRenderingFamily,
    )
    from immich_memories.analysis.selection_source import (
        EditorialDependencies,
        EditorialSelectionRequest,
        PlaceConstraint,
        PreparedEditorialSource,
        SourceScope,
    )
    from immich_memories.analysis.selection_source_groups import EditorialGroup


def _source_exclusion_reason(
    source: Asset | VideoClipInfo,
    request: EditorialSelectionRequest,
    dependencies: EditorialDependencies,
    owner_exclusions: set[str],
    components: frozenset[str],
) -> str | None:
    clip = source if isinstance(source, VideoClipInfo) else None
    asset = asset_of(source)
    reason = _identity_exclusion_reason(asset, request, owner_exclusions, components)
    if reason is None:
        reason = _provenance_exclusion_reason(asset, clip, request.scope)
    if reason is None:
        reason = _duration_exclusion_reason(source, asset, request.scope)
    if reason is None:
        reason = _boundary_exclusion_reason(asset, request, dependencies)
    return reason


def _duration_exclusion_reason(
    source: Asset | VideoClipInfo, asset: Asset, scope: SourceScope
) -> str | None:
    """A recording too long to be worth its cost never reaches preparation.

    Decided on Immich metadata alone, before speech analysis, editorial
    selection, or any original download: an hour-long source once paid for a
    full 37GB fetch to place one six-second shot (#1013). A photograph has no
    duration and is exempt; an owner-required id does not lift this — the
    required picture simply never enters the pool, and the eligibility trace
    says why.
    """
    from immich_memories.api.models import AssetType

    if scope.max_source_video_seconds <= 0:
        return None
    if asset.type != AssetType.VIDEO and not isinstance(source, VideoClipInfo):
        return None
    duration = (
        source.duration_seconds if isinstance(source, VideoClipInfo) else asset.duration_seconds
    )
    if isinstance(source, VideoClipInfo) and (duration is None or duration <= 0):
        # A burst clip may not state its own duration; the underlying video is
        # what would be downloaded, so its metadata is the honest cost.
        duration = asset.duration_seconds
    if duration is None or duration <= 0:
        return "video duration metadata is missing"
    if duration > scope.max_source_video_seconds:
        return (
            f"source video runs {duration / 60:.0f} minutes, over the "
            f"{scope.max_source_video_seconds / 60:g}-minute maximum"
        )
    return None


def _identity_exclusion_reason(
    asset: Asset,
    request: EditorialSelectionRequest,
    owner_exclusions: set[str],
    components: frozenset[str],
) -> str | None:
    """Facts about this asset alone: who asked for it, and who already refused it."""
    if request.scope.asset_ids is not None and asset.id not in request.scope.asset_ids:
        return "outside exact asset membership"
    if not request.scope.include_off_timeline and not_on_the_timeline(asset):
        return "not on the timeline"
    if asset.id in owner_exclusions:
        return "owner exclusion"
    if asset.id in request.evidence_exclusions:
        return request.evidence_exclusions[asset.id]
    if asset.id in components:
        return "Live Photo component"
    return None


def _provenance_exclusion_reason(
    asset: Asset, clip: VideoClipInfo | None, scope: SourceScope
) -> str | None:
    """The camera-roll provenance rule, reading probed motion dimensions when present."""
    if scope.accept_any_provenance:
        return None
    # The resolution-aware rule supersedes the old still-only EXIF veto. Keeping
    # both made a high-resolution published photo fail before its size could
    # rescue it. Retain the legacy behaviour only when the new provenance rule
    # has been explicitly disabled; filename exclusions always remain.
    legacy_still_exif_veto = scope.stills_need_a_camera and scope.min_source_short_side <= 0
    if not_shot_here(
        asset,
        patterns=scope.excluded_filename_patterns,
        stills_need_a_camera=legacy_still_exif_veto,
    ):
        return "not shot on this camera"
    return forwarded_source_refusal(asset, min_short_side=scope.min_source_short_side, clip=clip)


def _boundary_exclusion_reason(
    asset: Asset,
    request: EditorialSelectionRequest,
    dependencies: EditorialDependencies,
) -> str | None:
    """The boundaries the ask itself drew: library, place, and date."""
    scope = request.scope
    if scope.library_ids:
        if dependencies.library_membership is None:
            raise ValueError("library scope requires a library_membership dependency")
        if not dependencies.library_membership(asset, scope.library_ids):
            return "outside library scope"
    if scope.place is not None and not _at_place(asset, scope.place):
        return "outside place scope"
    if scope.date_ranges and not any(
        window.contains(asset.file_created_at) for window in scope.date_ranges
    ):
        return "outside date scope"
    if not is_editorial_source_asset(asset, start_at=scope.start_at, end_at=scope.end_at):
        return _scope_exclusion_reason(asset, scope)
    return None


def _at_place(asset: Asset, place: PlaceConstraint) -> bool:
    exif = asset.exif_info
    if exif is None:
        return False
    if (
        place.cities
        and exif.city
        and exif.city.casefold() in {city.casefold() for city in place.cities}
    ):
        return True
    if (
        place.center is not None
        and place.radius_km > 0
        and exif.latitude is not None
        and exif.longitude is not None
    ):
        distance = haversine_km(place.center[0], place.center[1], exif.latitude, exif.longitude)
        return distance <= place.radius_km
    return False


def _scope_exclusion_reason(asset: Asset, scope: SourceScope) -> str:
    from immich_memories.api.models import AssetType

    if asset.type not in (AssetType.IMAGE, AssetType.VIDEO):
        return "unsupported media"
    if scope.start_at is not None and asset.file_created_at < scope.start_at:
        return "outside date scope"
    if scope.end_at is not None and asset.file_created_at > scope.end_at:
        return "outside date scope"
    return "missing capture time"


def _validate_prepared_source(prepared: PreparedEditorialSource) -> None:
    if set(prepared.candidate_ids).intersection(prepared.excluded_ids):
        raise ValueError("editorial source cannot both admit and exclude an asset")
    visual_ids = tuple(str(source.asset.id) for source in prepared.visual_sources)
    if visual_ids != prepared.candidate_ids:
        raise ValueError("editorial candidates and visual sources must conserve order and identity")
    _validate_moments_refine_episodes(prepared.episode_groups, prepared.moment_groups)
    _validate_rendering_family_references(prepared)


def _validate_moments_refine_episodes(
    episodes: Sequence[EditorialGroup], moments: Sequence[EditorialGroup]
) -> None:
    episode_by_asset = {
        asset_id: episode.group_id for episode in episodes for asset_id in episode.candidate_ids
    }
    for moment in moments:
        carrying = {episode_by_asset.get(asset_id) for asset_id in moment.candidate_ids}
        if len(carrying) != 1 or None in carrying:
            raise ValueError("each canonical moment must belong to exactly one canonical episode")


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
