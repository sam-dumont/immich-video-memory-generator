"""Video processing modules."""

import importlib as _importlib

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

_SUBMODULE_MAP = {
    "AssemblyClip": "immich_memories.processing.assembly",
    "AssemblySettings": "immich_memories.processing.assembly",
    "TitleScreenSettings": "immich_memories.processing.assembly",
    "TransitionType": "immich_memories.processing.assembly",
    "VideoAssembler": "immich_memories.processing.assembly",
    "aggregate_mood_from_clips": "immich_memories.processing.assembly",
    "assemble_montage": "immich_memories.processing.assembly",
    "ClipExtractor": "immich_memories.processing.clips",
    "ClipSegment": "immich_memories.processing.clips",
    "extract_clip": "immich_memories.processing.clips",
    "HWAccelBackend": "immich_memories.processing.hardware",
    "HWAccelCapabilities": "immich_memories.processing.hardware",
    "detect_hardware_acceleration": "immich_memories.processing.hardware",
    "get_ffmpeg_encoder": "immich_memories.processing.hardware",
    "get_ffmpeg_hwaccel_args": "immich_memories.processing.hardware",
    "print_hardware_info": "immich_memories.processing.hardware",
    "AspectRatioTransformer": "immich_memories.processing.transforms",
    "apply_aspect_ratio_transform": "immich_memories.processing.transforms",
}


def __getattr__(name: str):
    if name in _SUBMODULE_MAP:
        module = _importlib.import_module(_SUBMODULE_MAP[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
