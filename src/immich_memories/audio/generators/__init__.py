"""Music generation backends.

Provides a unified interface for different music generation systems:
- MusicGen: API-based generation via external MusicGen server
- ACE-Step: Local or API-based generation using ACE-Step 1.5
"""

import importlib as _importlib

__all__ = [
    "MusicGenerator",
    "GenerationRequest",
    "GenerationResult",
    "MusicGenBackend",
    "ACEStepBackend",
    "create_generator",
]

_SUBMODULE_MAP = {
    "MusicGenerator": "immich_memories.audio.generators.base",
    "GenerationRequest": "immich_memories.audio.generators.base",
    "GenerationResult": "immich_memories.audio.generators.base",
    "MusicGenBackend": "immich_memories.audio.generators.musicgen_backend",
    "ACEStepBackend": "immich_memories.audio.generators.ace_step_backend",
    "create_generator": "immich_memories.audio.generators.factory",
}


def __getattr__(name: str):
    if name in _SUBMODULE_MAP:
        module = _importlib.import_module(_SUBMODULE_MAP[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
