"""Configuration loader and main Config class for Immich Memories.

Contains the top-level Config settings class plus global config management
functions (get_config, set_config, init_config_dir).
Re-exported from config.py for backwards compatibility.
"""

from __future__ import annotations

import contextlib
import logging
import os
import stat
from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from immich_memories.config_models import (
    ACEStepConfig,
    AnalysisConfig,
    AudioConfig,
    AudioContentConfig,
    CacheConfig,
    ContentAnalysisConfig,
    DefaultsConfig,
    HardwareAccelConfig,
    ImmichConfig,
    LLMConfig,
    MusicGenConfig,
    OutputConfig,
    PhotoConfig,
    ScoringPriorityConfig,
    ServerConfig,
    TitleScreenConfig,
    TripsConfig,
    UploadConfig,
)
from immich_memories.config_models_auth import AuthConfig
from immich_memories.scheduling.models import SchedulerConfig

# Tier 2 sections — grouped under `advanced:` in YAML, flat on Config at runtime.
_TIER2_SECTIONS = frozenset(
    {
        "analysis",
        "hardware",
        "llm",
        "musicgen",
        "ace_step",
        "content_analysis",
        "audio_content",
        "server",
        "auth",
    }
)


def _load_yaml_data(path: Path) -> dict:
    """Load and flatten YAML config data (advanced: → top-level)."""
    if not path.exists():
        return {}
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    if "advanced" in data and isinstance(data["advanced"], dict):
        advanced = data.pop("advanced")
        for key, value in advanced.items():
            if key not in data:
                data[key] = value
    return data


_yaml_source_data: dict = {}


class _YamlSettingsSource(PydanticBaseSettingsSource):
    """Pydantic-settings source backed by a YAML dict.

    Sits below env vars in the priority chain so that
    IMMICH_MEMORIES_FOO__BAR=x always overrides foo.bar in config.yaml.
    """

    def get_field_value(self, field, field_name):  # type: ignore[override]  # noqa: ANN001
        val = _yaml_source_data.get(field_name)
        return val, field_name, val is not None

    def __call__(self) -> dict:
        return _yaml_source_data.copy()


class Config(BaseSettings):
    """Main configuration for Immich Memories.

    Config tiers (YAML layout):
      Tier 1 (top level): immich, defaults, output, audio, title_screens,
                           cache, upload, trips, photos
      Tier 2 (advanced:):  analysis, hardware, llm, musicgen, ace_step,
                           content_analysis, audio_content, server
      Tier 3 (internal):   scheduler, title_llm

    At runtime, ALL sections are flat fields on Config (config.analysis, etc.).
    The tier grouping only affects YAML serialization.
    """

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
    photos: PhotoConfig = Field(default_factory=PhotoConfig)
    scoring_priority: ScoringPriorityConfig = Field(default_factory=ScoringPriorityConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    trips: TripsConfig = Field(default_factory=TripsConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)

    @classmethod
    def from_yaml(cls, path: Path) -> Config:
        """Load configuration from a YAML file.

        Priority order (highest wins): env vars > YAML file > defaults.
        This ensures IMMICH_MEMORIES_AUTH__ENABLED=false always overrides
        auth.enabled: true in config.yaml.
        """
        global _yaml_source_data
        _yaml_source_data = _load_yaml_data(path)
        try:
            return cls()
        finally:
            _yaml_source_data = {}

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        yaml_source = _YamlSettingsSource(settings_cls)
        # dotenv_settings intentionally excluded — YAML source replaces it
        return (init_settings, env_settings, yaml_source, dotenv_settings, file_secret_settings)

    def save_yaml(self, path: Path) -> None:
        """Save configuration to a YAML file (tiered format)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.model_dump()

        # Group tier 2 sections under advanced:
        advanced: dict = {}
        for key in _TIER2_SECTIONS:
            if key in data:
                advanced[key] = data.pop(key)
        if advanced:
            data["advanced"] = advanced

        with path.open("w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        # Restrict config file permissions (contains API keys)
        with contextlib.suppress(OSError):
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600 - owner read/write only

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

    # Auth shortcut: set both USERNAME + PASSWORD to auto-enable basic auth
    if (auth_user := os.environ.get("IMMICH_MEMORIES_AUTH_USERNAME")) and (
        auth_pass := os.environ.get("IMMICH_MEMORIES_AUTH_PASSWORD")
    ):
        config.auth.enabled = True
        config.auth.provider = "basic"
        config.auth.username = auth_user
        config.auth.password = auth_pass


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
    with contextlib.suppress(OSError):
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

    return config_dir
