"""Translate the versioned job contract into the existing generation input."""

from immich_memories.analysis.editorial_planner import EditorialSelection
from immich_memories.api.models import AssetType, VideoClipInfo
from immich_memories.config import Config
from immich_memories.generate import GenerationParams
from immich_memories.processing.encoding_plan import HdrMode


def generation_params(request, directory, client, progress) -> GenerationParams:
    """Preserve cut order and intervals; keep all generated state in the job workspace."""
    config = Config()
    config.immich.url = str(request.immich.url)
    config.immich.api_key = request.immich.api_key.get_secret_value()
    config.cache.directory = str(directory / "cache")
    config.cache.database = str(directory / "run.sqlite")
    config.cache.video_cache_enabled = False
    config.output.directory = str(directory)
    config.output.codec = request.output.codec
    config.output.codec_policy = "strict"
    config.output.hdr_mode = HdrMode.SDR
    config.hardware.enabled = True
    config.hardware.backend = "nvidia"
    config.title_screens.enabled = bool(request.plan.title)
    config.title_screens.show_month_dividers = False
    clips = []
    for chosen in request.plan.clips:
        asset = client.get_asset(str(chosen.asset_id))
        if asset.type == AssetType.IMAGE and chosen.render_mode == "motion":
            raise ValueError("Moving Live Photos require the selected video asset in S1")
        clips.append(
            VideoClipInfo(
                asset=asset,
                duration_seconds=asset.duration_seconds or chosen.end,
                width=asset.width,
                height=asset.height,
            )
        )
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
        title=request.plan.title or None,
        subtitle=request.plan.subtitle or None,
        memory_key_override=request.memory_key,
        progress_callback=progress,
        clip_segments={str(c.asset_id): (c.start, c.end) for c in request.plan.clips},
        editorial_selections=tuple(
            EditorialSelection(
                str(c.asset_id), c.start, c.end, c.render_mode, c.render_frame_seconds
            )
            for c in request.plan.clips
        ),
    )
