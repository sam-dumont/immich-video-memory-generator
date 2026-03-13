"""Configuration management for Immich Memories.

Re-export shim: all config models and loader functions are defined in
config_models.py, config_models_extra.py, and config_loader.py.
This module re-exports everything for backwards compatibility so that
``from immich_memories.config import Config, get_config`` etc. still work.
"""

from immich_memories.config_loader import (  # noqa: F401
    Config,
    get_config,
    init_config_dir,
    set_config,
)
from immich_memories.config_models import (  # noqa: F401
    AnalysisConfig,
    CacheConfig,
    DefaultsConfig,
    HardwareAccelConfig,
    ImmichConfig,
    LLMConfig,
    OutputConfig,
    ServerConfig,
    expand_env_vars,
)
from immich_memories.config_models_extra import (  # noqa: F401
    ACEStepConfig,
    AudioConfig,
    AudioContentConfig,
    ContentAnalysisConfig,
    MusicGenConfig,
    TitleScreenConfig,
)

__all__ = [
    "ACEStepConfig",
    "AnalysisConfig",
    "AudioConfig",
    "AudioContentConfig",
    "CacheConfig",
    "Config",
    "ContentAnalysisConfig",
    "DefaultsConfig",
    "HardwareAccelConfig",
    "ImmichConfig",
    "LLMConfig",
    "MusicGenConfig",
    "OutputConfig",
    "ServerConfig",
    "TitleScreenConfig",
    "expand_env_vars",
    "get_config",
    "init_config_dir",
    "set_config",
]
