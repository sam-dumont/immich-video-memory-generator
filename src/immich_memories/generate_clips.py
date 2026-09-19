"""Clip extraction, probing, and cleanup for generate pipeline."""

from __future__ import annotations

import contextlib
import logging
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.generate_privacy import clip_location_name
from immich_memories.processing.assembly_config import AssemblyClip

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from immich_memories.analysis.editorial_planner import EditorialSelection
    from immich_memories.cache.video_cache import CacheBatch
    from immich_memories.generate import GenerationParams
    from immich_memories.processing.download_coordinator import (
        DownloadCoordinator,
        DownloadResult,
        PrefetchAsset,
    )
    from immich_memories.processing.probe_cache import ProbeCache

logger = logging.getLogger(__name__)

# Minimum clip duration filter (matches UI pipeline)
MIN_CLIP_DURATION = 1.5


def _reject(message: str, offenders: Iterable[str]) -> None:
    named = sorted(offenders)
    if named:
        raise ValueError(message + ", ".join(named))


def _impossible_motion(directive: EditorialSelection, clip) -> bool:
    from immich_memories.api.models import AssetType

    return (
        directive.render_mode == "motion"
        and clip.asset.type == AssetType.IMAGE
        and not (
            clip.live_burst_video_ids
            and clip.live_burst_trim_points
            and len(clip.live_burst_video_ids or ()) == len(clip.live_burst_trim_points or ())
        )
    )


def _video_still(directive: EditorialSelection, clip) -> bool:
    from immich_memories.api.models import AssetType

    return directive.render_mode == "still" and clip.asset.type == AssetType.VIDEO


def _frame_outside_source(directive: EditorialSelection, clip) -> bool:
    return _video_still(directive, clip) and (
        directive.render_frame_seconds is not None
        and (clip.duration_seconds <= 0 or directive.render_frame_seconds >= clip.duration_seconds)
    )


def _validate_certified_directive(
    params: GenerationParams, clip, directives: tuple[EditorialSelection, ...]
) -> None:
    from immich_memories.processing.editorial_live_render import validate_editorial_live_clip

    validate_editorial_live_clip(clip)
    directive = next((row for row in directives if row.asset_id == clip.asset.id), None)
    interval = tuple(clip.editorial_live_manifest["selected_interval"])
    if (
        directive is None
        or directive.render_mode != "motion"
        or (directive.start_time, directive.end_time) != interval
    ):
        raise ValueError("Editorial Live directive changed its certified interval")
    declared = getattr(params, "clip_segments", {}).get(clip.asset.id)
    if declared is not None and tuple(declared) != interval:
        raise ValueError("Editorial Live segment overrides its certified interval")


def _validated_render_directives(params: GenerationParams) -> dict[str, EditorialSelection]:
    """Validate the run-level rendering contract before any source work starts."""
    from collections import Counter

    directives = tuple(getattr(params, "editorial_selections", ()))
    ids = tuple(directive.asset_id for directive in directives)
    _reject(
        "duplicate render directives: ",
        (asset_id for asset_id, count in Counter(ids).items() if count > 1),
    )
    clips_by_id = {clip.asset.id: clip for clip in params.clips}
    _reject("unknown render directive asset IDs: ", set(ids) - set(clips_by_id))
    _reject(
        "IMAGE motion directive needs a Live Photo family: ",
        (
            directive.asset_id
            for directive in directives
            if _impossible_motion(directive, clips_by_id[directive.asset_id])
        ),
    )
    _reject(
        "video still rendering needs an exact frame timestamp: ",
        (
            directive.asset_id
            for directive in directives
            if _video_still(directive, clips_by_id[directive.asset_id])
            and directive.render_frame_seconds is None
        ),
    )
    _reject(
        "frame timestamp outside source duration: ",
        (
            directive.asset_id
            for directive in directives
            if _frame_outside_source(directive, clips_by_id[directive.asset_id])
        ),
    )
    for clip in params.clips:
        if clip.editorial_live_manifest is not None:
            _validate_certified_directive(params, clip, directives)
    return {directive.asset_id: directive for directive in directives}


def _probe_file_duration(path: Path, *, probe_cache: ProbeCache | None = None) -> float | None:
    """Probe actual file duration via ffprobe. Returns None on failure."""
    from immich_memories.processing.probe_cache import ProbeCache, ProbeError

    with contextlib.suppress(OSError, ProbeError, ValueError):
        duration = (probe_cache or ProbeCache()).get(path).duration_seconds
        return duration or None
    return None


def _prefetch_assets(
    clips: list,
    directives: dict[str, EditorialSelection] | None = None,
) -> list[PrefetchAsset]:
    """Return network-only video targets, including burst components."""
    from immich_memories.api.models import AssetType
    from immich_memories.processing.download_coordinator import DownloadTarget

    targets: list[PrefetchAsset] = []
    decisions = directives or {}
    for clip in clips:
        if (
            clip.editorial_live_manifest is None
            and clip.local_path
            and Path(clip.local_path).exists()
        ):
            continue
        directive = decisions.get(clip.asset.id)
        if (
            directive is not None
            and directive.render_mode == "still"
            and clip.asset.type == AssetType.IMAGE
        ):
            continue
        if clip.asset.type == AssetType.IMAGE and not clip.live_burst_video_ids:
            continue
        if clip.live_burst_video_ids and clip.live_burst_trim_points:
            targets.extend(DownloadTarget(id=video_id) for video_id in clip.live_burst_video_ids)
        else:
            targets.append(clip.asset)
    return targets


def _download_video_path(
    params: GenerationParams,
    clip,
    video_cache: CacheBatch | None,
    output_dir: Path,
    prefetched: dict[str, DownloadResult] | None,
) -> Path | None:
    """Resolve one clip's source without retrying completed burst prefetches."""
    from immich_memories.generate_downloads import download_clip

    if clip.live_burst_video_ids and clip.live_burst_trim_points:
        burst_results = (
            {
                video_id: prefetched[video_id]
                for video_id in clip.live_burst_video_ids
                if video_id in prefetched
            }
            if prefetched is not None
            else None
        )
        return download_clip(
            params.client,
            video_cache,
            clip,
            output_dir,
            prefetched_burst_results=burst_results,
            hardware_enabled=params.config.hardware.enabled,
        )

    prefetched_result = prefetched.get(clip.asset.id) if prefetched is not None else None
    if prefetched_result and prefetched_result.path is not None:
        return prefetched_result.path
    if prefetched_result and prefetched_result.error:
        logger.warning("Failed to prefetch %s: %s", clip.asset.id, prefetched_result.error)
        return None
    return download_clip(params.client, video_cache, clip, output_dir)


@dataclass(frozen=True)
class _Extraction:
    """The one run's sources, caches and progress reporting, shared by every clip."""

    params: GenerationParams
    video_cache: CacheBatch | None
    output_dir: Path
    directives: dict[str, EditorialSelection]
    prefetched: dict[str, DownloadResult] | None
    probe_cache: ProbeCache | None
    report: Callable[[str, float, str], None]


def _rendered_photo(extraction: _Extraction, clip, directive: EditorialSelection | None):
    from immich_memories.generate_photos import _render_photo_as_clip

    params = extraction.params
    segment = params.clip_segments.get(clip.asset.id) if directive is not None else None
    return _render_photo_as_clip(
        clip,
        params,
        extraction.output_dir,
        duration_seconds=None if segment is None else segment[1] - segment[0],
    )


def _extracted_segment(extraction: _Extraction, clip, video_path: Path, progress: float, name: str):
    from immich_memories.processing.clips import extract_clip

    params = extraction.params
    start_time, end_time = params.clip_segments.get(
        clip.asset.id, (0.0, clip.duration_seconds or 5.0)
    )

    extraction.report("extract", progress, f"Extracting segment: {name}")
    if clip.editorial_live_manifest is not None:
        from immich_memories.processing.editorial_live_render import extract_certified_live

        segment_path, duration = extract_certified_live(
            clip,
            video_path,
            extraction.output_dir,
            extract=extract_clip,
            config=params.config,
        )
    else:
        segment_path = extract_clip(
            video_path, start_time=start_time, end_time=end_time, config=params.config
        )

        # WHY: extract_clip with -c copy can produce files shorter OR longer
        # than requested due to keyframe boundaries. Use min(actual, nominal)
        # so we never claim more duration than the file actually has (prevents
        # frame underruns) but also never more than what was requested
        # (prevents audio starting early).
        nominal_duration = end_time - start_time
        actual_duration = (
            _probe_file_duration(segment_path, probe_cache=extraction.probe_cache)
            if extraction.probe_cache is not None
            else _probe_file_duration(segment_path)
        )
        duration = min(actual_duration, nominal_duration) if actual_duration else nominal_duration

    exif = clip.asset.exif_info
    return AssemblyClip(
        path=segment_path,
        duration=duration,
        date=clip.asset.file_created_at.strftime("%Y-%m-%d"),
        asset_id=clip.asset.id,
        rotation_override=params.clip_rotations.get(clip.asset.id),
        llm_emotion=clip.llm_emotion,
        latitude=exif.latitude if exif else None,
        longitude=exif.longitude if exif else None,
        location_name=clip_location_name(exif),
        has_music=bool(set(clip.audio_categories or []) & {"music", "singing"}),
    )


def _rendered_clip(extraction: _Extraction, clip, progress: float, name: str):
    """Render one selected source as the directive demands, or report nothing usable."""
    from immich_memories.api.models import AssetType

    params = extraction.params
    directive = extraction.directives.get(clip.asset.id)
    # IMAGE-type clips from the unified selection pool. The question is
    # what the candidate CARRIES, not what kind of asset it is: a Live
    # Photo whose burst was not worth stitching is a photograph, and
    # asking whether it has a video component at all sent every one of
    # them off to download a video for a still.
    if clip.asset.type == AssetType.IMAGE and (
        not clip.live_burst_video_ids
        or (directive is not None and directive.render_mode == "still")
    ):
        return _rendered_photo(extraction, clip, directive)

    video_path = _download_video_path(
        params, clip, extraction.video_cache, extraction.output_dir, extraction.prefetched
    )
    if not video_path or not video_path.exists():
        if clip.editorial_live_manifest is not None:
            raise ValueError("Certified editorial Live source is unavailable")
        logger.warning(f"Failed to download {clip.asset.id}, skipping")
        return None

    if directive is not None and directive.render_mode == "still":
        from immich_memories.generate_photos import _render_video_frame_as_clip

        assert directive.render_frame_seconds is not None
        return _render_video_frame_as_clip(
            clip,
            params,
            extraction.output_dir,
            video_path=video_path,
            frame_seconds=directive.render_frame_seconds,
        )

    return _extracted_segment(extraction, clip, video_path, progress, name)


def _prepare_in_workers(params, video_cache, output_dir, directives, coordinator, report):
    from immich_memories.processing.output_canvas import resolve_generation_canvas
    from immich_memories.processing.probe_cache import ProbeCache
    from immich_memories.processing.source_preparation import prepare_sources

    canvas = resolve_generation_canvas(params)
    ordered = {}
    jobs = list(enumerate(params.clips))

    def prepare(client, job):
        index, clip = job
        work_dir = output_dir / ".source_preparation" / str(index)
        work_dir.mkdir(parents=True, exist_ok=True)
        worker_params = replace(params, client=client, output_canvas=canvas, progress_callback=None)
        extraction = _Extraction(
            worker_params,
            video_cache,
            work_dir,
            directives,
            coordinator.sources_for(client, _prefetch_assets([clip], directives)),
            ProbeCache(),
            lambda *_args: None,
        )
        name = clip.asset.original_file_name or clip.asset.id[:8]
        try:
            return _rendered_clip(extraction, clip, 0.0, name)
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
            if clip.editorial_live_manifest is not None:
                raise
            logger.warning("%s leaves the film: %s", name, exc)
            return None

    for completed, (index, rendered) in enumerate(
        prepare_sources(
            jobs,
            client=coordinator.worker_client,
            prepare=prepare,
            workers=params.config.analysis.source_prepare_workers,
        ),
        1,
    ):
        if rendered is not None:
            ordered[index] = rendered
        report("extract", completed / len(jobs) * 0.7, f"Prepared {completed}/{len(jobs)} sources")
    return [ordered[index] for index in sorted(ordered)]


def _extract_clips(
    params: GenerationParams,
    video_cache: CacheBatch | None,
    output_dir: Path,
    *,
    download_coordinator: DownloadCoordinator | None = None,
    probe_cache: ProbeCache | None = None,
) -> list[AssemblyClip]:
    """Download videos and extract clip segments. Renders IMAGE clips as photo animations."""

    def _report(phase: str, progress: float, msg: str) -> None:
        if params.progress_callback:
            params.progress_callback(phase, progress, msg)

    directives = _validated_render_directives(params)
    assembly_clips: list[AssemblyClip] = []
    total = len(params.clips)
    prefetched: dict[str, DownloadResult] | None = None
    if download_coordinator is not None:
        return _prepare_in_workers(
            params, video_cache, output_dir, directives, download_coordinator, _report
        )
    extraction = _Extraction(
        params, video_cache, output_dir, directives, prefetched, probe_cache, _report
    )

    for i, clip in enumerate(params.clips):
        progress = (i / total) * 0.7
        clip_name = clip.asset.original_file_name or clip.asset.id[:8]
        _report("extract", progress, f"Downloading: {clip_name}")

        try:
            rendered = _rendered_clip(extraction, clip, progress, clip_name)
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as e:
            if clip.editorial_live_manifest is not None:
                raise
            # One source the tools cannot render is not a reason to lose the film.
            # It leaves by name, and only an empty cut is an error.
            logger.warning("%s leaves the film — %s", clip_name, e)
            continue
        if rendered:
            assembly_clips.append(rendered)

    return assembly_clips


def _cleanup_temp_clips(assembly_clips: list[AssemblyClip]) -> None:
    for clip in assembly_clips:
        with contextlib.suppress(Exception):
            if clip.path.exists() and "tmp" in str(clip.path).lower():
                clip.path.unlink()


def _cleanup_temp_dirs(output_dir: Path) -> None:
    """Remove intermediate directories created during generation."""
    import shutil

    for subdir in (
        ".title_screens",
        ".intermediates",
        ".live_merges",
        ".assembly_temps",
        ".temporary_downloads",
        ".source_preparation",
        "photos",
    ):
        path = output_dir / subdir
        if path.exists():
            with contextlib.suppress(Exception):
                shutil.rmtree(path)


def assets_to_clips(assets: list, *, min_duration: float = MIN_CLIP_DURATION) -> list:
    """Convert metadata to timed clips; a zero minimum leaves admission to the editor."""
    from immich_memories.api.models import VideoClipInfo

    clips = []
    for asset in assets:
        duration = asset.duration_seconds or 0
        if duration < min_duration:
            continue
        clips.append(
            VideoClipInfo(
                asset=asset,
                duration_seconds=duration,
                width=asset.width,
                height=asset.height,
            )
        )
    return clips
