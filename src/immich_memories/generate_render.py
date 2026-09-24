"""Prepare and assemble a base film on the current machine."""

from __future__ import annotations

import inspect
import time as _time
from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeVar, cast

from immich_memories.filename_builder import normalize_output_path
from immich_memories.generate_clips import cleanup_temp_clips, extract_clips
from immich_memories.generate_privacy import (
    anonymize_clips_for_privacy,
    anonymize_name,
    anonymize_preset_params,
)
from immich_memories.generate_progress import _OperationalProgress, _PipelineProgress
from immich_memories.generate_settings import (
    announce_title_source,
    build_assembly_settings,
    create_assembler,
)
from immich_memories.generate_timeline import (
    apply_final_content_budget as _apply_final_content_budget,
)
from immich_memories.generate_timeline import (
    check_rendered_film,
    validate_certified_content,
)
from immich_memories.operations.phases import OperationalPhase
from immich_memories.processing.clip_validation import validate_clips
from immich_memories.processing.probe_cache import ProbeCache

if TYPE_CHECKING:
    from immich_memories.config_loader import Config
    from immich_memories.generate import GenerationParams, PreparedGeneration
    from immich_memories.tracking import RunTracker

import logging

logger = logging.getLogger(__name__)
_CallResult = TypeVar("_CallResult")


def _build_download_coordinator(
    params: GenerationParams,
    cache_batch,
    output_dir: Path,
):
    """Build isolated download workers without sharing the generation client."""
    if params.client is None:
        return None

    from immich_memories.generate_downloads import _download_temporary_asset
    from immich_memories.processing.download_coordinator import (
        DownloadCoordinator,
        build_sync_client_factory,
        has_sync_client_connection,
    )

    if not has_sync_client_connection(params.client):
        logger.debug("Download prefetch disabled: client cannot seed isolated workers")
        return None

    download_operation = None
    if cache_batch is None:

        def download_temporary_asset(client, asset):
            return _download_temporary_asset(client, asset, output_dir)

        download_operation = download_temporary_asset
    return DownloadCoordinator(
        build_sync_client_factory(params.client, params.config.immich.api_version),
        cache_batch,
        params.config.analysis.download_workers,
        download_operation=download_operation,
    )


def _call_with_optional_probe_cache(
    callable_under_test: Callable[..., _CallResult],
    *args: object,
    probe_cache: ProbeCache,
    **kwargs: object,
) -> _CallResult:
    """Pass run-scoped probing only through extension callables that accept it."""
    signature_target = getattr(callable_under_test, "side_effect", None)
    if not callable(signature_target):
        signature_target = callable_under_test
    parameters: Iterable[inspect.Parameter]
    try:
        parameters = inspect.signature(signature_target).parameters.values()
    except (TypeError, ValueError):
        parameters = ()
    accepts_probe_cache = any(
        parameter.name == "probe_cache" or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )
    if accepts_probe_cache:
        kwargs["probe_cache"] = probe_cache
    return callable_under_test(*args, **kwargs)


def _build_settings_with_optional_probe_cache(
    params: GenerationParams,
    assembly_clips: list,
    *,
    probe_cache: ProbeCache,
):
    return _call_with_optional_probe_cache(
        build_assembly_settings,
        params,
        assembly_clips,
        probe_cache=probe_cache,
    )


def _create_assembler_with_optional_probe_cache(
    settings,
    config: Config,
    *,
    probe_cache: ProbeCache,
):
    return _call_with_optional_probe_cache(
        create_assembler,
        settings,
        config,
        probe_cache=probe_cache,
    )


def _extract_clips_with_optional_prefetch(
    params: GenerationParams,
    cache_batch,
    output_dir: Path,
    *,
    probe_cache: ProbeCache,
) -> list:
    """Keep the legacy extraction call shape when prefetch is unavailable."""
    coordinator = _build_download_coordinator(params, cache_batch, output_dir)
    if coordinator is None:
        return _call_with_optional_probe_cache(
            extract_clips,
            params,
            cache_batch,
            output_dir,
            probe_cache=probe_cache,
        )
    return _call_with_optional_probe_cache(
        extract_clips,
        params,
        cache_batch,
        output_dir,
        download_coordinator=coordinator,
        probe_cache=probe_cache,
    )


def _emit_download_phase(
    operational: _OperationalProgress,
    params: GenerationParams,
    current: int,
    total: int,
    message: str,
) -> None:
    """Report direct-generation download work only while this call owns it."""
    if operational.phase_is_unperformed(OperationalPhase.DOWNLOAD):
        operational.emit(OperationalPhase.DOWNLOAD, current, total, message)


def _extracted_sources(
    params: GenerationParams, run_output_dir: Path, *, probe_cache: ProbeCache
) -> list:
    """Own the persistent video-cache lifecycle across exactly one extraction."""
    from immich_memories.cache.video_cache import VideoDownloadCache

    if not params.config.cache.video_cache_enabled:
        # Disabled cache means no interaction with the configured persistent
        # video cache. Extraction uses disposable run-local downloads.
        return _extract_clips_with_optional_prefetch(
            params, None, run_output_dir, probe_cache=probe_cache
        )
    video_cache = VideoDownloadCache(
        cache_dir=params.config.cache.video_cache_path,
        max_size_gb=params.config.cache.video_cache_max_size_gb,
        max_age_days=params.config.cache.video_cache_max_age_days,
    )
    # One cache lifecycle owns all persistent-cache downloads for this
    # generation. It scans once, then evicts from the manifest on exit.
    with video_cache.begin_batch() as cache_batch:
        return _extract_clips_with_optional_prefetch(
            params, cache_batch, run_output_dir, probe_cache=probe_cache
        )


def _anonymized_params(params: GenerationParams) -> GenerationParams:
    return replace(
        params,
        person_name=anonymize_name(params.person_name),
        memory_preset_params=anonymize_preset_params(params.memory_preset_params),
    )


def render_base(params, output_path, directory, tracker, operational, progress, phase_times):
    """Choose the configured renderer and keep one progress and run-tracking lifecycle."""
    from immich_memories.generate import GenerationError
    from immich_memories.processing.remote_render import RemoteRenderClient

    if params.config.render.enabled:
        started = _time.monotonic()
        operational.emit_unperformed_prerequisites(OperationalPhase.SELECTION)
        operational.emit(OperationalPhase.RENDER, 0, len(params.clips), "Rendering on worker")
        tracker.start_phase("assembly", len(params.clips))
        try:
            with RemoteRenderClient(params.config.render) as worker:
                result = worker.render(params, output_path.with_suffix(".mp4"), progress.report)
        except GenerationError:
            if not params.config.render.fallback_to_local:
                raise
            logger.warning("Render worker failed; rendering the same selected cut locally")
            tracker.complete_phase(items_processed=0, errors=[{"error": "Worker render failed"}])
        else:
            tracker.complete_phase(
                items_processed=result.clips_selected, extra_metrics=result.render_metrics
            )
            operational.emit(
                OperationalPhase.RENDER,
                result.clips_selected,
                result.clips_selected,
                "Render complete",
            )
            phase_times["assembly"] = _time.monotonic() - started
            return result
    return render_local(params, output_path, directory, tracker, operational, progress, phase_times)


def render_local(
    params: GenerationParams,
    requested_output_path: Path,
    run_output_dir: Path,
    run_tracker: RunTracker,
    operational: _OperationalProgress,
    pp: _PipelineProgress,
    phase_times: dict[str, float],
) -> PreparedGeneration:
    """Own source preparation and assembly; return metadata for the common music phase."""
    from immich_memories.generate import GenerationError, PreparedGeneration

    probe_cache = ProbeCache()
    phase_start = _time.monotonic()
    assembly_clips: list = []
    try:
        # Phase 1: Download and extract clips
        pp.report("download", 0.0, "Downloading clips...")
        run_tracker.start_phase("clip_extraction", len(params.clips))
        _emit_download_phase(
            operational,
            params,
            0,
            len(params.clips),
            "Preparing source downloads",
        )

        assembly_clips = _extracted_sources(params, run_output_dir, probe_cache=probe_cache)
        run_tracker.complete_phase(items_processed=len(assembly_clips))
        _emit_download_phase(
            operational,
            params,
            len(assembly_clips),
            len(params.clips),
            "Sources prepared",
        )
        operational.emit_unperformed_prerequisites(OperationalPhase.SELECTION)
        operational.emit(OperationalPhase.RENDER, 0, len(params.clips), "Rendering memory")
        phase_times["download"] = _time.monotonic() - phase_start
        pp.report("download", 1.0, "Clips downloaded")

        # Pre-assembly validation: skip clips with missing/empty files
        assembly_clips, skipped = validate_clips(assembly_clips)
        validate_certified_content(params, assembly_clips)

        if not assembly_clips:
            raise GenerationError("No clips could be processed")

        # Privacy mode: anonymize GPS + names before title/assembly
        if params.privacy_mode:
            assembly_clips = anonymize_clips_for_privacy(assembly_clips)
            params = _anonymized_params(params)

        assembly_clips = _apply_final_content_budget(params, assembly_clips)
        validate_certified_content(params, assembly_clips)

        from immich_memories.generate_captions import prepare_location_captions

        assembly_clips = prepare_location_captions(params, assembly_clips)

        # Phase 2: Assemble (includes title generation + streaming encode)
        _t = _time.monotonic()
        assembly_cb = pp.assembly_callback()
        run_tracker.start_phase("assembly", len(assembly_clips))

        settings = _build_settings_with_optional_probe_cache(
            params,
            assembly_clips,
            probe_cache=probe_cache,
        )
        if settings.title_screens is not None:
            announce_title_source(settings.title_screens, run_tracker)
        result_output_path = normalize_output_path(
            requested_output_path,
            cast(Literal["mp4", "mov"], settings.encoding_plan.container),
        )
        assembler = _create_assembler_with_optional_probe_cache(
            settings,
            params.config,
            probe_cache=probe_cache,
        )
        staged_output_path = result_output_path.with_name(
            f"{result_output_path.stem}.assembling{result_output_path.suffix}"
        )
        encode_started = _time.monotonic()
        staged_result_path = assembler.assemble_with_titles(
            assembly_clips,
            staged_output_path,
            assembly_cb,
            frame_preview_callback=params.frame_preview_callback,
        )
        # Titles included: every second of it was this machine producing the
        # film, and decoding the result can only be faster.
        encode_seconds = _time.monotonic() - encode_started
        plan = settings.encoding_plan
        metrics, duration_warning = check_rendered_film(params, staged_result_path, plan)
        run_tracker.complete_phase(items_processed=len(assembly_clips), extra_metrics=metrics)
        operational.emit(
            OperationalPhase.RENDER,
            len(assembly_clips),
            len(assembly_clips),
            "Render complete",
        )
        phase_times["assembly"] = _time.monotonic() - _t
        return PreparedGeneration(
            path=result_output_path,
            staged_path=staged_result_path,
            encoding_plan=plan,
            assembly_clips=tuple(assembly_clips),
            clips_analyzed=len(params.clips),
            clips_selected=len(assembly_clips),
            music_mute_windows=settings.music_mute_windows,
            duration_warning=duration_warning,
            render_metrics=metrics,
            encode_seconds=encode_seconds,
        )
    finally:
        try:
            cleanup_temp_clips(assembly_clips)
        except OSError:
            logger.debug("Temp clip cleanup failed", exc_info=True)
