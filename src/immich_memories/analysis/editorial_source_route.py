"""Metadata demand and exact render projection for the production editorial route."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from math import isfinite
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from immich_memories.analysis.editorial_planner import EditorialPlan, EditorialSelection
from immich_memories.analysis.motion_rendering import (
    ClockOffsetProbe,
    MotionRendering,
    motion_renderings,
)
from immich_memories.api.models import Asset, AssetType, VideoClipInfo
from immich_memories.operations.cut_progress import StageUpdate
from immich_memories.processing.live_material import LiveRenderMaterial

if TYPE_CHECKING:
    from immich_memories.analysis.editorial_structure_contract import StructurePlanningInput
    from immich_memories.analysis.selection_source import PreparedEditorialSource
    from immich_memories.analysis.selection_trace import Trace
    from immich_memories.analysis.smart_pipeline import ClipWithSegment
    from immich_memories.config_loader import Config

CARRIER_KINDS = frozenset({"still", "live-still", "live-motion", "video"})


@dataclass(frozen=True)
class EditorialSourcePlan:
    """Canonical evidence was checked; legacy visual scores remain explicitly absent."""

    candidates: tuple[ClipWithSegment, ...]
    plan: EditorialPlan
    render_adjustments: tuple[dict, ...] = ()
    render_timing: dict | None = None
    duration_realization: dict | None = None


@runtime_checkable
class EditorialSourcePlanner(Protocol):
    """An explicit production capability, separate from legacy score verification."""

    def plan_source(
        self,
        sources: Sequence[Asset | VideoClipInfo],
        *,
        trace: Trace,
        include_live_photos: bool = True,
        hdr_only: bool = False,
        on_stage: Callable[[StageUpdate], None] | None = None,
    ) -> EditorialSourcePlan: ...


def metadata_demand(
    prepared: PreparedEditorialSource,
    sources: Sequence[Asset | VideoClipInfo],
    *,
    photo_seconds: float,
    hdr_only: bool = False,
) -> tuple[ClipWithSegment, ...]:
    """Keep every requested eligible primary, without ranking the captured context."""
    source_by_id = {_asset(source).id: source for source in sources}
    requested = set(source_by_id)
    captured = set(prepared.candidate_ids) | set(prepared.excluded_ids)
    if requested - captured:
        raise ValueError("canonical source omitted captured requested assets")
    demanded = tuple(row for row in prepared.candidates if row.asset_id in requested)
    if any(not isfinite(row.shippable_duration) or row.shippable_duration < 0 for row in demanded):
        raise ValueError("source metadata has invalid duration")
    metadata_sources = tuple(
        source_by_id[row.asset_id].model_copy(update={"asset": row.source})
        if isinstance(source_by_id[row.asset_id], VideoClipInfo)
        else row.source
        for row in demanded
    )
    rows = _demand_candidates(
        replace(prepared, candidates=demanded),
        metadata_sources,
        photo_seconds=photo_seconds,
        strict_source_durations=True,
    )
    if any(
        not isfinite(row.clip.duration_seconds) or row.clip.duration_seconds < 0 for row in rows
    ):
        raise ValueError("source metadata has invalid duration")
    return tuple(
        row
        for row in rows
        if not hdr_only or row.clip.asset.type != AssetType.VIDEO or row.clip.is_hdr
    )


def _seconds(value: object, name: str, *, zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"editorial {name} must be numeric")
    number = float(value)
    if not isfinite(number) or number < 0 or (number == 0 and not zero):
        raise ValueError(f"editorial {name} must be finite and positive")
    return number


@dataclass(frozen=True)
class _Carrier:
    """One native carrier row after its own numbers were checked against each other."""

    asset_id: str
    kind: str
    seconds: float
    start: float
    end: float
    frame: float | None

    @property
    def mode(self) -> Literal["motion", "still"]:
        return "motion" if self.kind in {"live-motion", "video"} else "still"


def _carrier_from(row: dict) -> _Carrier:
    kind = row["kind"]
    if kind not in CARRIER_KINDS:
        raise ValueError("unknown editorial carrier rendering kind")
    seconds = _seconds(row["seconds"], "carrier duration")
    start = _seconds(row.get("start_time", 0.0), "source start", zero=True)
    end = _seconds(row.get("end_time", start + seconds), "source end")
    if abs(end - start - seconds) > 1e-6:
        raise ValueError("editorial source interval disagrees with carrier duration")
    frame = row.get("render_frame_seconds")
    if frame is not None:
        frame = _seconds(frame, "source frame", zero=True)
    return _Carrier(row["asset_id"], kind, seconds, start, end, frame)


def project_source_rendering(
    carriers: list[dict],
    candidates: tuple[ClipWithSegment, ...],
    *,
    config: Config,
    include_live_photos: bool,
    clock_offsets: ClockOffsetProbe | None = None,
    companion_assets: Mapping[str, Asset] | None = None,
) -> EditorialSourcePlan:
    """Bind native selected intervals to the exact sources/material that will render."""
    from immich_memories.analysis.smart_pipeline import ClipWithSegment

    by_id = {candidate.clip.asset.id: candidate for candidate in candidates}
    if len(by_id) != len(candidates):
        raise ValueError("editorial source candidates need unique IDs")
    selected: list[EditorialSelection] = []
    adjustments: list[dict] = []
    replacements: dict[str, ClipWithSegment] = {}
    for row in carriers:
        asset_id = row["asset_id"]
        if asset_id not in by_id or asset_id in replacements:
            raise ValueError("editorial render carrier escaped or duplicated source demand")
        carrier = _carrier_from(row)
        clip, end = _bound_carrier(
            carrier,
            row,
            by_id,
            adjustments,
            config=config,
            include_live_photos=include_live_photos,
            companion_assets=companion_assets,
            clock_offsets=clock_offsets,
        )
        selected.append(
            EditorialSelection(asset_id, carrier.start, end, carrier.mode, carrier.frame)
        )
        replacements[asset_id] = ClipWithSegment(clip, carrier.start, end, 0.0, analyzed=False)
    return EditorialSourcePlan(
        candidates=tuple(replacements.get(row.clip.asset.id, row) for row in candidates),
        plan=EditorialPlan(selections=tuple(selected)),
        render_adjustments=tuple(adjustments),
    )


def _bound_carrier(
    carrier: _Carrier,
    row: dict,
    by_id: dict[str, ClipWithSegment],
    adjustments: list[dict],
    *,
    config: Config,
    include_live_photos: bool,
    companion_assets: Mapping[str, Asset] | None,
    clock_offsets: ClockOffsetProbe | None = None,
) -> tuple[VideoClipInfo, float]:
    """Return the exact clip and end time one carrier will actually render."""
    asset = by_id[carrier.asset_id].clip.asset
    clip = _projected_clip(
        carrier,
        row,
        by_id,
        config=config,
        include_live_photos=include_live_photos,
        companion_assets=companion_assets,
        clock_offsets=clock_offsets,
    )
    end = _bounded_end(carrier, clip, row, adjustments)
    if carrier.kind == "live-motion":
        clip = _with_live_manifest(clip, row, carrier, end)
    if (
        asset.type == AssetType.VIDEO
        and carrier.mode == "still"
        and (
            clip.duration_seconds <= 0
            or carrier.frame is None
            or carrier.frame >= clip.duration_seconds
        )
    ):
        raise ValueError("editorial still frame exceeds actual video source")
    return clip, end


def projection_problem(
    row: Mapping[str, Any], asset_type: AssetType, *, include_live_photos: bool
) -> str | None:
    """Why this carrier cannot render from its source, or None when projection accepts it.

    The same question ``_projected_clip`` enforces, asked before the film finalizes: a
    carrier that fails it retires in the planner instead of dying at the render with every
    model call already spent.
    """
    carrier = _carrier_from(dict(row))
    if carrier.kind == "live-motion":
        if not include_live_photos or asset_type != AssetType.IMAGE:
            return "editorial Live motion is outside the requested media contract"
        return None
    if asset_type == AssetType.VIDEO:
        if carrier.kind != "video" and carrier.frame is None:
            return "editorial video still requires an exact source frame"
        return None
    if asset_type == AssetType.IMAGE and carrier.mode == "still":
        if carrier.start != 0 or carrier.frame is not None:
            return "editorial photograph cannot claim video source timing"
        return None
    return "editorial carrier kind disagrees with source media"


def retire_unprojectable(
    carriers: list[dict], source: StructurePlanningInput, cut_carriers: list[dict]
) -> list[dict]:
    """Drop the carriers the strict projection refuses, retiring them into ``cut_carriers``.

    The projection question asked before membership and timing finalize, so hours of model
    work never die at the render: the gates upstream have already had their chance to
    substitute, and the film may simply shrink here. A retired row carries its ``reason``
    and ``review_stage``.
    """
    kept: list[dict] = []
    for carrier in carriers:
        asset = source.assets.get(carrier["asset_id"])
        problem = (
            projection_problem(carrier, asset.type, include_live_photos=source.allow_live_motion)
            if asset is not None
            else "editorial carrier has no captured source"
        )
        if problem is None:
            kept.append(carrier)
        else:
            cut_carriers.append(
                carrier | {"reason": problem, "review_stage": "projection-finalize"}
            )
    return kept


def _projected_clip(
    carrier: _Carrier,
    row: dict,
    by_id: dict[str, ClipWithSegment],
    *,
    config: Config,
    include_live_photos: bool,
    companion_assets: Mapping[str, Asset] | None,
    clock_offsets: ClockOffsetProbe | None = None,
) -> VideoClipInfo:
    """Re-time the demanded clip against the rendering its carrier kind claims."""
    clip = by_id[carrier.asset_id].clip
    asset = clip.asset
    problem = projection_problem(row, asset.type, include_live_photos=include_live_photos)
    if problem is not None:
        raise ValueError(problem)
    if carrier.kind == "live-motion":
        return _live_motion_clip(
            carrier,
            row,
            by_id,
            config=config,
            companion_assets=companion_assets,
            clock_offsets=clock_offsets,
        )
    if asset.type == AssetType.VIDEO:
        return clip
    return clip.model_copy(update={"duration_seconds": carrier.seconds})


def _live_motion_clip(
    carrier: _Carrier,
    row: dict,
    by_id: dict[str, ClipWithSegment],
    *,
    config: Config,
    companion_assets: Mapping[str, Asset] | None,
    clock_offsets: ClockOffsetProbe | None = None,
) -> VideoClipInfo:
    members = _live_members(row, carrier.asset_id, by_id)
    material = [by_id[key].clip.asset for key in members]
    rendering = motion_renderings(
        material,
        config,
        companion_assets=companion_assets,
        clock_offsets=clock_offsets,
    ).get(carrier.asset_id)
    if rendering is None or set(rendering.still_ids) != set(members):
        raise ValueError("editorial Live motion has no matching source manifest")
    if rendering.material is None:
        raise ValueError("editorial Live motion lacks canonical material lineage")
    _check_declared_manifest(row, rendering, rendering.material)
    return by_id[carrier.asset_id].clip.model_copy(
        update={
            "duration_seconds": rendering.duration_seconds,
            "live_burst_video_ids": list(rendering.video_ids),
            "live_burst_trim_points": list(rendering.trim_points),
            "live_burst_shutter_timestamps": list(rendering.shutter_timestamps),
            "live_burst_still_ids": list(rendering.still_ids),
            "live_burst_material": rendering.material.as_dict(),
            "local_path": None,
        }
    )


def _live_members(row: dict, asset_id: str, by_id: Mapping[str, ClipWithSegment]) -> Sequence[str]:
    members = row.get("members")
    if not isinstance(members, (list, tuple)) or not members or len(set(members)) != len(members):
        raise ValueError("editorial Live motion needs unique exact material members")
    if asset_id not in members or not set(members).issubset(by_id):
        raise ValueError("editorial Live motion escaped demanded material")
    return members


def _check_declared_manifest(
    row: dict, rendering: MotionRendering, material: LiveRenderMaterial
) -> None:
    declared_videos = row.get("video_ids")
    declared_trims = row.get("trim_points")
    if declared_videos != list(rendering.video_ids) or declared_trims != [
        list(pair) for pair in rendering.trim_points
    ]:
        raise ValueError("editorial Live motion companion manifest changed")
    if row.get("live_material") is not None and row["live_material"] != material.as_dict():
        raise ValueError("editorial Live source lineage changed")


def _bounded_end(
    carrier: _Carrier, clip: VideoClipInfo, row: dict, adjustments: list[dict]
) -> float:
    end = carrier.end
    if carrier.mode == "motion" and end > clip.duration_seconds:
        end = _tightened_to_source(carrier, clip, row, adjustments)
    if carrier.mode == "motion" and clip.duration_seconds <= 0:
        raise ValueError("editorial interval exceeds actual source metadata")
    return end


def _tightened_to_source(
    carrier: _Carrier, clip: VideoClipInfo, row: dict, adjustments: list[dict]
) -> float:
    """Accept only the centisecond rounding boundary, and record both intervals.

    Native units publish centiseconds, while source durations retain their full
    precision. Any other overrun is a real disagreement with the source.
    """
    if carrier.start == 0 and carrier.end == round(clip.duration_seconds, 2) == row.get(
        "raw_seconds"
    ):
        adjustments.append(
            {
                "asset_id": carrier.asset_id,
                "reason": "native-duration-rounded-to-centiseconds",
                "native_interval": [carrier.start, carrier.end],
                "source_interval": [carrier.start, clip.duration_seconds],
            }
        )
        return clip.duration_seconds
    raise ValueError("editorial interval exceeds actual source metadata")


def _with_live_manifest(
    clip: VideoClipInfo, row: dict, carrier: _Carrier, end: float
) -> VideoClipInfo:
    # This is an explicit renderer contract, not a legacy cache/analysis flag.
    canonical_material = LiveRenderMaterial.from_dict(clip.live_burst_material)
    exact_interval = canonical_material.selected_interval(
        carrier.seconds,
        start=row.get("start_time", 0.0),
        end=row.get("end_time"),
        raw_seconds=row.get("raw_seconds"),
    )
    if exact_interval != (carrier.start, end):
        raise ValueError("Editorial Live interval disagrees with source projection")
    return clip.model_copy(
        update={
            "editorial_live_manifest": {
                "version": "editorial-live-render-v1",
                "material": clip.live_burst_material,
                "selected_interval": [carrier.start, end],
            }
        }
    )


def _asset(source: Asset | VideoClipInfo) -> Asset:
    return source.asset if isinstance(source, VideoClipInfo) else source


def _demand_candidates(
    prepared: PreparedEditorialSource,
    sources: Sequence[Asset | VideoClipInfo],
    *,
    photo_seconds: float,
    strict_source_durations: bool = False,
) -> tuple[ClipWithSegment, ...]:
    """One shippable clip per prepared candidate, timed from its own metadata."""
    from immich_memories.analysis.smart_pipeline import ClipWithSegment

    source_by_id = {_asset(source).id: source for source in sources}
    demanded: list[ClipWithSegment] = []
    for candidate in prepared.candidates:
        source = source_by_id[candidate.asset_id]
        seconds = candidate.shippable_duration
        unknown_video = (
            strict_source_durations and _asset(source).type.value == "VIDEO" and seconds == 0
        )
        if seconds <= 0 and not unknown_video:
            seconds = photo_seconds
        if seconds <= 0 and not unknown_video:
            raise ValueError("text-only demand candidates need a positive duration")
        if isinstance(source, VideoClipInfo):
            clip = source.model_copy(update={"duration_seconds": seconds})
        else:
            clip = VideoClipInfo(
                asset=source,
                duration_seconds=seconds,
                width=source.width,
                height=source.height,
            )
        demanded.append(
            ClipWithSegment(
                clip=clip,
                start_time=0.0,
                end_time=seconds,
                score=0.0,
                analyzed=False,
            )
        )
    return tuple(demanded)
