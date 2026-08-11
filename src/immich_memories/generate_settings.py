"""Assembly settings, title settings, assembler creation, music, and upload for generate pipeline."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Literal

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
    EncodingRequest,
    OutputCodec,
    resolve_encoding_plan,
)
from immich_memories.processing.hardware import (
    HWAccelCapabilities,
    detect_hardware_acceleration,
)
from immich_memories.processing.hdr_utilities import has_any_hdr_clip

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.config_loader import Config
    from immich_memories.generate import GenerationParams
    from immich_memories.tracking import RunTracker

logger = logging.getLogger(__name__)


def _build_assembly_settings(
    params: GenerationParams,
    assembly_clips: list[AssemblyClip],
) -> AssemblySettings:
    """Build AssemblySettings from GenerationParams."""
    config = params.config

    transition_type = {
        "smart": TransitionType.SMART,
        "crossfade": TransitionType.CROSSFADE,
        "cut": TransitionType.CUT,
        "none": TransitionType.NONE,
    }.get(params.transition.lower(), TransitionType.CROSSFADE)

    resolution_map = {"4k": (3840, 2160), "1080p": (1920, 1080), "720p": (1280, 720)}
    res = params.output_resolution
    if res is not None and res.lower() == "auto":
        # Explicit "auto": detect from source clips
        auto_resolution = True
        target_resolution = None
    elif res is not None:
        # Explicit resolution (4k/1080p/720p)
        auto_resolution = False
        target_resolution = resolution_map.get(res.lower())
    else:
        # No resolution specified: use config default
        auto_resolution = False
        target_resolution = config.output.resolution_tuple

    title_screen_settings = _build_title_settings(params, config, assembly_clips)

    # Scale mode: CLI/param > config > default
    effective_scale_mode = params.scale_mode or config.defaults.scale_mode

    # A CLI format override selects both a compatible codec and container.
    # With no override, the explicit output config remains authoritative.
    format_override = params.output_format.lower() if params.output_format else None
    output_container: Literal["mp4", "mov"]
    if format_override == "prores":
        output_codec = OutputCodec.PRORES
        output_container = "mov"
    elif format_override == "mp4":
        output_codec = OutputCodec.H264
        output_container = "mp4"
    else:
        output_codec = OutputCodec(config.output.codec)
        output_container = config.output.format

    capabilities = (
        detect_hardware_acceleration() if config.hardware.enabled else HWAccelCapabilities()
    )
    output_crf = params.output_crf if params.output_crf is not None else config.output.effective_crf
    encoding_plan = resolve_encoding_plan(
        EncodingRequest(
            codec=output_codec,
            hdr_mode=config.output.hdr_mode,
            hardware_enabled=config.hardware.enabled,
            preset=config.hardware.encoder_preset,
            crf=output_crf,
            container=output_container,
        ),
        capabilities,
        input_has_hdr=has_any_hdr_clip(assembly_clips),
    )

    return AssemblySettings(
        encoding_plan=encoding_plan,
        transition=transition_type,
        transition_duration=params.transition_duration,
        output_crf=output_crf,
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

    # Apply LLM-generated title overrides
    if params.title:
        settings.title_override = params.title
        settings.subtitle_override = params.subtitle

    return settings


def _create_assembler(settings: AssemblySettings, config: Config):
    """Create a VideoAssembler with the given settings."""
    from immich_memories.processing.video_assembler import VideoAssembler

    return VideoAssembler(
        settings,
        output_crf=config.output.effective_crf,
        default_transition_duration=config.defaults.transition_duration,
        default_resolution=config.output.resolution_tuple,
    )


def _run_music_phase(
    params: GenerationParams,
    assembly_clips: list[AssemblyClip],
    result_path: Path,
    run_output_dir: Path,
    run_tracker: RunTracker,
) -> None:
    """Resolve and apply music to the assembled video."""
    from immich_memories.generate_music import apply_music_file, resolve_music_file

    def _report_fn(phase: str, progress: float, msg: str) -> None:
        if params.progress_callback:
            params.progress_callback(phase, progress, msg)

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
        return
    _report_fn("music", 0.9, "Mixing music...")
    run_tracker.start_phase("music", 1)
    apply_music_file(result_path, music_file, params.music_volume)
    run_tracker.complete_phase(items_processed=1)


def _upload_to_immich(
    client: SyncImmichClient,
    video_path: Path,
    album_name: str | None,
) -> dict:
    result = client.upload_memory(video_path=video_path, album_name=album_name)
    logger.info(f"Uploaded to Immich: asset={result.get('asset_id')}, album={album_name}")
    return result
