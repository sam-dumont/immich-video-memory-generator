"""Video processing modules."""

from immich_memories.processing.assembly import (
    AssemblyClip,
    AssemblySettings,
    TitleScreenSettings,
    TransitionType,
    VideoAssembler,
    aggregate_mood_from_clips,
    assemble_montage,
)
from immich_memories.processing.clips import (
    ClipExtractor,
    ClipSegment,
    extract_clip,
)
from immich_memories.processing.hardware import (
    HWAccelBackend,
    HWAccelCapabilities,
    detect_hardware_acceleration,
    get_ffmpeg_encoder,
    get_ffmpeg_hwaccel_args,
    print_hardware_info,
)
from immich_memories.processing.transforms import (
    AspectRatioTransformer,
    apply_aspect_ratio_transform,
)

__all__ = [
    "AssemblyClip",
    "AssemblySettings",
    "ClipExtractor",
    "extract_clip",
    "ClipSegment",
    "AspectRatioTransformer",
    "apply_aspect_ratio_transform",
    "TitleScreenSettings",
    "VideoAssembler",
    "TransitionType",
    "aggregate_mood_from_clips",
    "assemble_montage",
    "HWAccelBackend",
    "HWAccelCapabilities",
    "detect_hardware_acceleration",
    "get_ffmpeg_encoder",
    "get_ffmpeg_hwaccel_args",
    "print_hardware_info",
]
