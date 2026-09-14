"""Regression coverage for pytest's user-state isolation."""

from pathlib import Path

from immich_memories.config_loader import Config, get_config, init_config_dir


def test_default_config_uses_pytest_paths(isolated_user_paths: Path) -> None:
    """Default configuration must never target a developer's user directories."""
    config = Config()

    assert config.cache.database_path.is_relative_to(isolated_user_paths)
    assert config.cache.cache_path.is_relative_to(isolated_user_paths)
    assert config.output.output_path.is_relative_to(isolated_user_paths)


def test_default_config_load_uses_pytest_home(isolated_user_paths: Path) -> None:
    """Default loading and initialization stay in the disposable test home."""
    config_path = Config.get_default_path()
    # Check before writing: a broken fixture must not touch the user's config.
    assert config_path.is_relative_to(isolated_user_paths)
    assert not config_path.exists()

    assert init_config_dir() == config_path.parent
    config_path.write_text("defaults:\n  transition: cut\n")

    assert get_config(reload=True).defaults.transition == "cut"
