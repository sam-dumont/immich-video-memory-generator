"""Configuration loader and main Config class for Immich Memories.

Contains the top-level Config settings class plus global config management
functions (get_config, set_config, init_config_dir).
Re-exported from config.py for backwards compatibility.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from immich_memories.config_models import (
    AnalysisConfig,
    CacheConfig,
    DefaultsConfig,
    HardwareAccelConfig,
    ImmichConfig,
    LLMConfig,
    OutputConfig,
    ServerConfig,
    TripsConfig,
)
from immich_memories.config_models_extra import (
    ACEStepConfig,
    AudioConfig,
    AudioContentConfig,
    ContentAnalysisConfig,
    MusicGenConfig,
    TitleScreenConfig,
    UploadConfig,
)
from immich_memories.scheduling.models import SchedulerConfig


class Config(BaseSettings):
    """Main configuration for Immich Memories."""

    model_config = SettingsConfigDict(
        env_prefix="IMMICH_MEMORIES_",
        env_nested_delimiter="__",
        case_sensitive=False,
    )

    server: ServerConfig = Field(default_factory=ServerConfig)
    immich: ImmichConfig = Field(default_factory=ImmichConfig)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    hardware: HardwareAccelConfig = Field(default_factory=HardwareAccelConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    title_llm: LLMConfig | None = Field(
        default=None, description="LLM for title generation (falls back to llm)"
    )
    audio: AudioConfig = Field(default_factory=AudioConfig)
    musicgen: MusicGenConfig = Field(default_factory=MusicGenConfig)
    ace_step: ACEStepConfig = Field(default_factory=ACEStepConfig)
    content_analysis: ContentAnalysisConfig = Field(default_factory=ContentAnalysisConfig)
    audio_content: AudioContentConfig = Field(default_factory=AudioContentConfig)
    title_screens: TitleScreenConfig = Field(default_factory=TitleScreenConfig)
    upload: UploadConfig = Field(default_factory=UploadConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    trips: TripsConfig = Field(default_factory=TripsConfig)

    @classmethod
    def from_yaml(cls, path: Path) -> Config:
        """Load configuration from a YAML file."""
        if not path.exists():
            return cls()

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        return cls(**data)

    def save_yaml(self, path: Path) -> None:
        """Save configuration to a YAML file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(self.model_dump(), f, default_flow_style=False, sort_keys=False)
        # Restrict config file permissions (contains API keys)
        try:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600 - owner read/write only
        except OSError:
            pass  # Best-effort on non-POSIX systems

    @classmethod
    def get_default_path(cls) -> Path:
        """Get the default configuration file path."""
        return Path.home() / ".immich-memories" / "config.yaml"


# Global configuration instance
_config: Config | None = None


def _apply_env_overrides(config: Config) -> None:
    """Apply environment variable overrides to a Config instance."""
    if url := os.environ.get("IMMICH_URL"):
        config.immich.url = url
    if api_key := os.environ.get("IMMICH_API_KEY"):
        config.immich.api_key = api_key
    if openai_key := os.environ.get("OPENAI_API_KEY"):
        config.llm.api_key = openai_key

    # MusicGen env var overrides (also supported via IMMICH_MEMORIES_MUSICGEN__*)
    if musicgen_enabled := os.environ.get("MUSICGEN_ENABLED"):
        config.musicgen.enabled = musicgen_enabled.lower() in ("true", "1", "yes")
    if musicgen_url := os.environ.get("MUSICGEN_BASE_URL"):
        config.musicgen.base_url = musicgen_url
    if musicgen_key := os.environ.get("MUSICGEN_API_KEY"):
        config.musicgen.api_key = musicgen_key

    # ACE-Step env var overrides (also supported via IMMICH_MEMORIES_ACE_STEP__*)
    if ace_step_enabled := os.environ.get("ACE_STEP_ENABLED"):
        config.ace_step.enabled = ace_step_enabled.lower() in ("true", "1", "yes")
    if (ace_step_mode := os.environ.get("ACE_STEP_MODE")) and ace_step_mode in ("lib", "api"):
        config.ace_step.mode = ace_step_mode  # type: ignore[assignment]
    if ace_step_url := os.environ.get("ACE_STEP_API_URL"):
        config.ace_step.api_url = ace_step_url


def get_config(reload: bool = False) -> Config:
    """Get the global configuration instance.

    Args:
        reload: If True, reload from disk even if already loaded.

    Returns:
        The global Config instance.
    """
    global _config

    if _config is None or reload:
        _config = Config.from_yaml(Config.get_default_path())
        _apply_env_overrides(_config)

    return _config


def set_config(config: Config) -> None:
    """Set the global configuration instance."""
    global _config
    _config = config


def init_config_dir() -> Path:
    """Initialize the configuration directory structure.

    Returns:
        Path to the configuration directory.
    """
    logger = logging.getLogger(__name__)
    config_dir = Path.home() / ".immich-memories"
    config_dir.mkdir(parents=True, exist_ok=True)

    cache_dir = config_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    projects_dir = config_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    # Enforce restrictive permissions on config directory (owner-only access)
    # This protects API keys and cached data on multi-user systems.
    try:
        for d in (config_dir, cache_dir, projects_dir):
            d.chmod(0o700)

        # Warn if config file has overly permissive permissions
        config_file = config_dir / "config.yaml"
        if config_file.exists():
            mode = config_file.stat().st_mode
            if mode & (stat.S_IRGRP | stat.S_IROTH):
                logger.warning(
                    "Config file %s is readable by other users. Run: chmod 600 %s",
                    config_file,
                    config_file,
                )
    except OSError:
        pass  # Skip on platforms where chmod is not supported (e.g., some Windows setups)

    return config_dir
