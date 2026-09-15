"""Translate the versioned job contract into the existing generation input."""

from immich_memories.analysis.editorial_planner import EditorialSelection
from immich_memories.api.models import AssetType, VideoClipInfo
from immich_memories.config import Config
from immich_memories.config_models_render import TitleScreenConfig
from immich_memories.generate import GenerationParams


def worker_config(request) -> Config:
    """Rebuild the film-shaping half of the app's config from the envelope alone.

    The timing policy the app froze into its binding is derived from these
    fields, so anything omitted here turns every job into a spurious 409.
    """
    config = Config()
    config.output.codec = request.output.codec
    config.output.codec_policy = request.output.codec_policy
    config.output.hdr_mode = request.output.hdr_mode
    config.output.quality = request.output.quality
    config.hardware.enabled = True
    config.hardware.backend = "nvidia"
    config.title_screens = TitleScreenConfig.model_validate(
        request.titles.model_dump(exclude={"title", "subtitle"})
    )
    config.photos.duration = request.options.photo_duration
    return config


def generation_params(request, directory, client, progress) -> GenerationParams:
    """Preserve cut order and intervals; keep all generated state in the job workspace."""
    config = worker_config(request)
    config.immich.url = str(request.immich.url)
    config.immich.api_key = request.immich.api_key.get_secret_value()
    config.cache.directory = str(directory / "cache")
    config.cache.database = str(directory / "run.sqlite")
    config.cache.video_cache_enabled = False
    config.output.directory = str(directory)
    clips = []
    for chosen in request.plan.clips:
        asset = client.get_asset(str(chosen.asset_id))
        clip = VideoClipInfo(
            asset=asset,
            duration_seconds=asset.duration_seconds or chosen.end,
            width=asset.width,
            height=asset.height,
            audio_categories=chosen.audio_categories,
            llm_emotion=chosen.llm_emotion,
        )
        if chosen.live is not None:
            _restore_live(clip, chosen.live)
        elif asset.type == AssetType.IMAGE and chosen.render_mode == "motion":
            raise ValueError("Moving Live Photos require their certified source material")
        clips.append(clip)
    return GenerationParams(
        clips=clips,
        output_path=directory / "render.mp4",
        config=config,
        client=client,
        transition=request.plan.transition,
        transition_duration=request.plan.transition_duration,
        output_resolution=request.output.resolution,
        output_orientation=request.output.orientation,
        output_crf=request.output.crf,
        output_format="mp4",
        no_music=True,
        upload_enabled=False,
        title=request.titles.title or None,
        subtitle=request.titles.subtitle or None,
        memory_type=request.memory.memory_type,
        person_name=request.memory.person_name,
        memory_preset_params=request.memory.preset_params,
        date_start=request.memory.date_start,
        date_end=request.memory.date_end,
        target_duration_seconds=request.memory.target_duration_seconds,
        editorial_render_timing=request.timing.model_dump(mode="json"),
        memory_key_override=request.memory_key,
        progress_callback=progress,
        clip_segments={str(c.asset_id): (c.start, c.end) for c in request.plan.clips},
        clip_rotations={
            str(c.asset_id): c.rotation_override
            for c in request.plan.clips
            if c.rotation_override is not None
        },
        scale_mode=request.options.scale_mode,
        add_date_overlay=request.options.add_date_overlay,
        add_place_overlay=request.options.add_place_overlay,
        privacy_mode=request.options.privacy_mode,
        editorial_selections=tuple(
            EditorialSelection(
                str(c.asset_id), c.start, c.end, c.render_mode, c.render_frame_seconds
            )
            for c in request.plan.clips
        ),
    )


def _restore_live(clip, certificate) -> None:
    from immich_memories.processing.editorial_live_render import validate_editorial_live_clip
    from immich_memories.processing.live_material import LiveRenderMaterial

    material = LiveRenderMaterial.from_dict(certificate.material)
    clip.duration_seconds = material.duration_seconds
    clip.live_burst_still_ids = list(material.still_ids)
    clip.live_burst_video_ids = list(material.video_ids)
    clip.live_burst_trim_points = list(material.trim_points)
    clip.live_burst_shutter_timestamps = list(material.shutter_timestamps)
    clip.live_burst_material = material.as_dict()
    clip.editorial_live_manifest = certificate.model_dump(mode="json")
    validate_editorial_live_clip(clip)
