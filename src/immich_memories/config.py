"""Configuration management for Immich Memories.

Re-export shim: all config models and loader functions are defined in the
``config_models*`` modules and config_loader.py.
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
    CacheConfig,
    HardwareAccelConfig,
    ImmichConfig,
    expand_env_vars,
)
from immich_memories.config_models_analysis import (  # noqa: F401
    AnalysisConfig,
    AudioContentConfig,
    ContentAnalysisConfig,
)
from immich_memories.config_models_llm import LLMConfig  # noqa: F401
from immich_memories.config_models_render import (  # noqa: F401
    DefaultsConfig,
    OutputConfig,
    PhotoConfig,
    TitleScreenConfig,
)
from immich_memories.config_models_server import ServerConfig
from immich_memories.config_models_soundtrack import (  # noqa: F401
    ACEStepConfig,
    AudioConfig,
    MusicGenConfig,
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
    "PhotoConfig",
    "ServerConfig",
    "TitleScreenConfig",
    "expand_env_vars",
    "get_config",
    "init_config_dir",
    "set_config",
]
