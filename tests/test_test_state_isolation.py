"""Regression coverage for pytest's user-state isolation."""

from pathlib import Path

from immich_memories.config_loader import Config


def test_default_config_uses_pytest_paths(isolated_user_paths: Path) -> None:
    """Default configuration must never target a developer's user directories."""
    config = Config()

    assert config.cache.database_path.is_relative_to(isolated_user_paths)
    assert config.cache.cache_path.is_relative_to(isolated_user_paths)
    assert config.output.output_path.is_relative_to(isolated_user_paths)
