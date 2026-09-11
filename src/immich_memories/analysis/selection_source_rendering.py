"""The Live Photo rendering family a burst carries into the admitted corpus.

A burst arrives as four parallel arrays or as one canonical material manifest,
sometimes twice over for the same picture through a shared album. Everything
here reads that evidence: it decides whether two representations agree, builds
the family the admitted stills share, and takes the family back off a corpus
where it would straddle two moments. Nothing here judges a picture.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from operator import itemgetter
from typing import TYPE_CHECKING, Any

from immich_memories.analysis.editorial_contracts import (
    EditorialCandidate,
    LivePhotoRenderingFamily,
    live_photo_rendering_family_id,
)
from immich_memories.analysis.source_filter import asset_id_of, asset_of
from immich_memories.api.models import Asset, VideoClipInfo
from immich_memories.processing.live_material import LiveRenderMaterial

if TYPE_CHECKING:
    from immich_memories.analysis.selection_source_groups import EditorialGroup

ManifestArrays = tuple[Sequence[str], Sequence[str], Sequence[Any], Sequence[float]]


def _rendering_manifest_signature(source: Asset | VideoClipInfo) -> tuple[object, ...] | None:
    """What two representations of one asset must agree on to be the same burst."""
    if not isinstance(source, VideoClipInfo) or not _has_rendering_family_evidence(source):
        return None
    if source.live_burst_material is not None:
        try:
            material = _source_live_material(source)
        except (ValueError, TypeError):
            return ("invalid-canonical", repr(source.live_burst_material))
        return ("canonical-positive-segments", material.identity())
    arrays = _manifest_arrays(source)
    if arrays is not None and _aligned(arrays):
        return ("aligned", _sorted_manifest_entries(arrays))
    return (
        "invalid",
        tuple(source.live_burst_still_ids or ()),
        tuple(source.live_burst_video_ids or ()),
        tuple(source.live_burst_trim_points or ()),
        tuple(source.live_burst_shutter_timestamps or ()),
    )


def _manifest_arrays(source: VideoClipInfo) -> ManifestArrays | None:
    """The four parallel burst arrays, or None when any of them is absent."""
    manifests = (
        source.live_burst_still_ids,
        source.live_burst_video_ids,
        source.live_burst_trim_points,
        source.live_burst_shutter_timestamps,
    )
    if any(value is None for value in manifests):
        return None
    still_ids, video_ids, trim_points, timestamps = manifests
    assert still_ids is not None
    assert video_ids is not None
    assert trim_points is not None
    assert timestamps is not None
    return (still_ids, video_ids, trim_points, timestamps)


def _aligned(arrays: ManifestArrays) -> bool:
    return len({len(array) for array in arrays}) == 1


def _sorted_manifest_entries(
    arrays: ManifestArrays, admitted_ids: set[str] | None = None
) -> tuple[tuple[Any, ...], ...]:
    """Burst rows as (shutter, still, video, trim), in canonical order."""
    still_ids, video_ids, trim_points, timestamps = arrays
    return tuple(
        sorted(
            (
                (timestamp, still_id, video_id, trim_point)
                for still_id, video_id, trim_point, timestamp in zip(
                    still_ids, video_ids, trim_points, timestamps, strict=True
                )
                if admitted_ids is None or still_id in admitted_ids
            ),
            key=itemgetter(0, 1),
        )
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
            "live_burst_material": None,
            "editorial_live_manifest": None,
        }
    )


def _with_favourite(source: Asset | VideoClipInfo, favourite: bool) -> Asset | VideoClipInfo:
    asset = asset_of(source)
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
    admitted_ids = {asset_id_of(source) for source in sources}
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
            source.live_burst_material,
        )
    )


def _rendering_family_from(
    source: VideoClipInfo,
    admitted_ids: set[str],
) -> LivePhotoRenderingFamily:
    if source.live_burst_material is not None:
        return _canonical_rendering_family(source, admitted_ids)
    arrays = _manifest_arrays(source)
    if arrays is None:
        raise ValueError("incomplete aligned manifest")
    if not arrays[0] or not _aligned(arrays):
        raise ValueError("unaligned manifest lengths")
    if source.asset.id not in arrays[0]:
        raise ValueError("enriched source is absent from its still manifest")
    admitted_entries = _sorted_manifest_entries(arrays, admitted_ids)
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


def _canonical_rendering_family(
    source: VideoClipInfo,
    admitted_ids: set[str],
) -> LivePhotoRenderingFamily:
    material = _source_live_material(source)
    if source.asset.id not in material.still_ids:
        raise ValueError("enriched source is absent from its still manifest")
    material = LiveRenderMaterial(
        tuple(entry for entry in material.source_entries if entry.still_id in admitted_ids)
    )
    family_id = live_photo_rendering_family_id(
        material.still_ids,
        material.video_ids,
        material.trim_points,
        material.shutter_timestamps,
        motion_duration_seconds=None,
        minimum_motion_seconds=None,
        material=material,
    )
    return LivePhotoRenderingFamily(
        family_id,
        material.still_ids,
        material.video_ids,
        material.trim_points,
        material.shutter_timestamps,
        material=material,
    )


def _source_live_material(source: VideoClipInfo) -> LiveRenderMaterial:
    material = LiveRenderMaterial.from_dict(source.live_burst_material)
    material.assert_arrays(
        still_ids=source.live_burst_still_ids,
        video_ids=source.live_burst_video_ids,
        trim_points=source.live_burst_trim_points,
        shutter_timestamps=source.live_burst_shutter_timestamps,
    )
    return material


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
