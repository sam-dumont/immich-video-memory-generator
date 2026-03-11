"""Factory for creating music generation backends.

Provides a single entry point to create the right backend
based on configuration, with automatic fallback.
"""

from __future__ import annotations

import logging
from typing import Any

from immich_memories.audio.generators.base import MusicGenerator

logger = logging.getLogger(__name__)


def create_generator(
    backend: str = "musicgen",
    config: Any | None = None,
) -> MusicGenerator:
    """Create a music generation backend.

    Args:
        backend: Backend name - 'musicgen', 'ace_step', or 'ace-step'.
        config: Backend-specific configuration object.
            - For 'musicgen': MusicGenClientConfig or app's MusicGenConfig
            - For 'ace_step': ACEStepConfig

    Returns:
        Configured MusicGenerator instance.

    Raises:
        ValueError: If the backend name is not recognized.
    """
    backend_lower = backend.lower().replace("-", "_")

    if backend_lower == "musicgen":
        from immich_memories.audio.generators.musicgen_backend import MusicGenBackend

        if config is not None and hasattr(config, "base_url"):
            # It's already a MusicGenClientConfig or compatible
            from immich_memories.audio.music_generator import MusicGenClientConfig

            if not isinstance(config, MusicGenClientConfig):
                # Convert from app's MusicGenConfig (pydantic) to client config
                config = MusicGenClientConfig.from_app_config(config)

        return MusicGenBackend(config)

    elif backend_lower == "ace_step":
        from immich_memories.audio.generators.ace_step_backend import (
            ACEStepBackend,
            ACEStepConfig,
        )

        if config is None:
            config = ACEStepConfig()
        elif not isinstance(config, ACEStepConfig):
            # Convert from app config (pydantic model) to ACEStepConfig
            config = _app_config_to_ace_step(config)

        return ACEStepBackend(config)

    else:
        raise ValueError(
            f"Unknown music generation backend: {backend!r}. "
            f"Supported backends: 'musicgen', 'ace_step'"
        )


def _app_config_to_ace_step(app_config) -> Any:
    """Convert an app-level ACEStepConfig (pydantic) to the dataclass config."""
    from immich_memories.audio.generators.ace_step_backend import ACEStepConfig

    kwargs = {}
    # Map fields that exist on both
    for field_name in (
        "mode",
        "api_url",
        "model_variant",
        "lm_model_size",
        "use_lm",
        "disable_offload",
        "num_versions",
        "hemisphere",
        "timeout_seconds",
        "bf16",
    ):
        if hasattr(app_config, field_name):
            kwargs[field_name] = getattr(app_config, field_name)

    # Pass API key via extra_args (used for Bearer auth in API mode)
    api_key = getattr(app_config, "api_key", "")
    if api_key:
        kwargs["extra_args"] = {"api_key": api_key}

    return ACEStepConfig(**kwargs)
