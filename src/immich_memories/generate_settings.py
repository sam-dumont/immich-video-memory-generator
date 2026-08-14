"""Assembly settings, title settings, assembler creation, music, and upload for generate pipeline."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.generate_privacy import (
    extract_trip_locations,
    generate_trip_title_text,
)
from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
    TitleScreenSettings,
    TransitionType,
)
from immich_memories.processing.encoding_plan import (
    EncodingPlan,
    EncodingRequest,
    resolve_encoding_plan,
    resolve_output_selection,
)
from immich_memories.processing.hardware import (
    HWAccelCapabilities,
    detect_hardware_acceleration,
)
from immich_memories.processing.hdr_utilities import detect_dominant_hdr_transfer

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.config_loader import Config
    from immich_memories.generate import GenerationParams
    from immich_memories.generate_music import MusicPhaseResult
    from immich_memories.processing.encoding_plan import EncodingPlan
    from immich_memories.processing.probe_cache import ProbeCache
    from immich_memories.tracking import RunTracker

logger = logging.getLogger(__name__)


def _build_assembly_settings(
    params: GenerationParams,
    assembly_clips: list[AssemblyClip],
    *,
    probe_cache: ProbeCache | None = None,
) -> AssemblySettings:
    """Build AssemblySettings from GenerationParams."""
    config = params.config

    transition_type = {
        "smart": TransitionType.SMART,
        "crossfade": TransitionType.CROSSFADE,
        "cut": TransitionType.CUT,
        "none": TransitionType.NONE,
    }.get(params.transition.lower(), TransitionType.CROSSFADE)

    from immich_memories.processing.output_canvas import resolve_generation_canvas

    canvas = resolve_generation_canvas(params)
    # WHY: photos and titles are rendered before final assembly. Leaving
    # assembly resolution on auto would let it make a second, conflicting
    # canvas decision after those intermediates already exist.
    auto_resolution = False
    target_resolution = (canvas.width, canvas.height)

    title_screen_settings = _build_title_settings(params, config, assembly_clips)

    # Scale mode: CLI/param > config > default
    effective_scale_mode = params.scale_mode or config.defaults.scale_mode

    # A CLI format override selects both a compatible codec and container.
    # With no override, the explicit output config remains authoritative.
    output_selection = resolve_output_selection(
        config_codec=config.output.codec,
        config_container=config.output.format,
        format_override=params.output_format,
    )

    capabilities = (
        detect_hardware_acceleration() if config.hardware.enabled else HWAccelCapabilities()
    )
    output_crf = params.output_crf if params.output_crf is not None else config.output.effective_crf
    encoding_plan = resolve_encoding_plan(
        EncodingRequest(
            codec=output_selection.codec,
            hdr_mode=config.output.hdr_mode,
            hardware_enabled=config.hardware.enabled,
            preset=config.hardware.encoder_preset,
            crf=output_crf,
            container=output_selection.container,
        ),
        capabilities,
        input_transfer=detect_dominant_hdr_transfer(
            assembly_clips,
            probe_cache=probe_cache,
        ),
    )
    if encoding_plan.tone_map_to_sdr and config.output.hdr_mode.value == "auto":
        logger.warning(
            "HDR input was detected, but %s output cannot preserve HDR; "
            "tone-mapping the final video to SDR. Set output.codec: h265 with "
            "output.hdr_mode: auto to preserve HDR.",
            encoding_plan.codec.value,
        )

    return AssemblySettings(
        encoding_plan=encoding_plan,
        transition=transition_type,
        transition_duration=params.transition_duration,
        auto_resolution=auto_resolution,
        target_resolution=target_resolution,
        title_screens=title_screen_settings,
        scale_mode=effective_scale_mode,
        add_date_overlay=params.add_date_overlay,
        debug_preserve_intermediates=params.debug_preserve_intermediates,
        privacy_mode=params.privacy_mode,
    )


def _build_title_settings(
    params: GenerationParams,
    config: Config,
    assembly_clips: list[AssemblyClip],
) -> TitleScreenSettings | None:
    """Build TitleScreenSettings if title screens are enabled in config."""
    if not config.title_screens.enabled:
        return None

    from immich_memories.filename_builder import build_title_person_name, get_divider_mode

    title_person_name = build_title_person_name(
        memory_type=params.memory_type,
        preset_params=params.memory_preset_params,
        person_name=params.person_name,
        use_first_name_only=config.title_screens.use_first_name_only,
    )

    divider_mode = get_divider_mode(
        memory_type=params.memory_type,
        date_start=params.date_start,
        date_end=params.date_end,
    )
    if not config.title_screens.show_month_dividers:
        divider_mode = "none"

    # Trip-specific title settings
    trip_locations = None
    trip_title_text = None
    if params.memory_type == "trip":
        trip_locations = extract_trip_locations(assembly_clips)
        trip_title_text = generate_trip_title_text(params.memory_preset_params)

    settings = TitleScreenSettings(
        enabled=True,
        person_name=title_person_name,
        start_date=params.date_start,
        end_date=params.date_end,
        locale=config.title_screens.locale,
        style_mode=config.title_screens.style_mode,
        title_duration=config.title_screens.title_duration,
        month_divider_duration=config.title_screens.month_divider_duration,
        ending_duration=config.title_screens.ending_duration,
        show_month_dividers=divider_mode == "month",
        divider_mode=divider_mode,
        month_divider_threshold=config.title_screens.month_divider_threshold,
        use_first_name_only=config.title_screens.use_first_name_only,
        memory_type=params.memory_type,
        trip_locations=trip_locations,
        trip_title_text=trip_title_text,
        home_lat=params.memory_preset_params.get("home_lat"),
        home_lon=params.memory_preset_params.get("home_lon"),
    )

    if params.timeline_plan is not None:
        plan = params.timeline_plan
        settings.title_duration = plan.title_duration
        settings.month_divider_duration = plan.divider_duration
        settings.ending_duration = plan.ending_duration
        settings.max_dividers = plan.max_dividers
        settings.show_ending_screen = plan.ending_duration > 0.0

    # Apply LLM-generated title overrides
    if params.title:
        settings.title_override = params.title
        settings.subtitle_override = params.subtitle

    return settings


def _create_assembler(
    settings: AssemblySettings,
    config: Config,
    *,
    probe_cache: ProbeCache | None = None,
):
    """Create a VideoAssembler with the given settings."""
    from immich_memories.processing.video_assembler import VideoAssembler

    return VideoAssembler(
        settings,
        default_transition_duration=config.defaults.transition_duration,
        default_resolution=config.output.resolution_tuple,
        probe_cache=probe_cache,
    )


def _music_phase_requested(params: GenerationParams) -> bool:
    """Return whether music resolution represents tracked phase work."""
    from immich_memories.generate_music import music_config_available

    return not params.no_music and (
        params.music_path is not None or music_config_available(params.config)
    )


def _complete_music_failure(
    exc: Exception,
    *,
    config: Config,
    run_tracker: RunTracker,
    phase_started: bool,
) -> MusicPhaseResult:
    """Finish optional music work without invalidating the base video."""
    from immich_memories.generate_music import MusicPhaseResult, optional_music_warning

    if not phase_started:
        run_tracker.start_phase("music", 1)
    warning = optional_music_warning(exc, config)
    logger.warning(warning)
    run_tracker.complete_phase(
        items_processed=0,
        errors=[{"error": warning}],
    )
    return MusicPhaseResult(applied=False, warning=warning)


def _run_music_phase(
    params: GenerationParams,
    assembly_clips: list[AssemblyClip],
    result_path: Path,
    run_output_dir: Path,
    run_tracker: RunTracker,
    *,
    encoding_plan: EncodingPlan,
) -> MusicPhaseResult:
    """Resolve and apply music to the assembled video."""
    from immich_memories.generate_music import (
        MusicPhaseResult,
        apply_music_file,
        resolve_music_file,
    )

    def _report_fn(phase: str, progress: float, msg: str) -> None:
        if params.progress_callback:
            params.progress_callback(phase, progress, msg)

    phase_started = _music_phase_requested(params)
    if phase_started:
        # WHY: resolve_music_file performs the expensive model generation and
        # optional stem separation, so the phase must include that work.
        run_tracker.start_phase("music", 1)
    try:
        music_file = resolve_music_file(
            config=params.config,
            music_path=params.music_path,
            no_music=params.no_music,
            assembly_clips=assembly_clips,
            run_output_dir=run_output_dir,
            memory_type=params.memory_type,
            report_fn=_report_fn,
        )
        if not music_file:
            if phase_started:
                run_tracker.complete_phase(items_processed=0)
            return MusicPhaseResult(applied=False)
        _report_fn("music", 0.9, "Mixing music...")
        if not phase_started:
            # Defensive fallback for custom resolvers that produce a track
            # without an explicit path or configured generation backend.
            run_tracker.start_phase("music", 1)
            phase_started = True
        apply_music_file(result_path, music_file, params.music_volume, encoding_plan)
    except Exception as exc:  # WHY: optional music must not invalidate the base artifact
        return _complete_music_failure(
            exc,
            config=params.config,
            run_tracker=run_tracker,
            phase_started=phase_started,
        )
    run_tracker.complete_phase(items_processed=1)
    return MusicPhaseResult(applied=True)


def _upload_to_immich(
    client: SyncImmichClient,
    video_path: Path,
    album_name: str | None,
) -> dict:
    result = client.upload_memory(video_path=video_path, album_name=album_name)
    logger.info(f"Uploaded to Immich: asset={result.get('asset_id')}, album={album_name}")
    return result
