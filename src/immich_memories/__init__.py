"""Immich Memories - Create yearly video compilations from your Immich photo library."""

__author__ = "Immich Memories Contributors"

from immich_memories._version import __version__
from immich_memories.config import Config, get_config

__all__ = ["Config", "get_config", "__version__"]
