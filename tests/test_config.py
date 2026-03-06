"""Tests for configuration management."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from immich_memories.config import (
    AnalysisConfig,
    Config,
    DefaultsConfig,
    ImmichConfig,
    OutputConfig,
    expand_env_vars,
)


class TestExpandEnvVars:
    """Tests for environment variable expansion."""

    def test_expand_braces_format(self):
        """Test ${VAR} format expansion."""
        os.environ["TEST_VAR"] = "test_value"
        result = expand_env_vars("prefix_${TEST_VAR}_suffix")
        assert result == "prefix_test_value_suffix"
        del os.environ["TEST_VAR"]

    def test_expand_dollar_format(self):
        """Test $VAR format expansion."""
        os.environ["TEST_VAR2"] = "another_value"
        result = expand_env_vars("$TEST_VAR2")
        assert result == "another_value"
        del os.environ["TEST_VAR2"]

    def test_missing_var_unchanged(self):
        """Test that missing variables are left unchanged."""
        result = expand_env_vars("${NONEXISTENT_VAR_12345}")
        assert result == "${NONEXISTENT_VAR_12345}"

    def test_no_expansion_needed(self):
        """Test strings without variables."""
        result = expand_env_vars("plain string")
        assert result == "plain string"


class TestImmichConfig:
    """Tests for ImmichConfig."""

    def test_default_values(self):
        """Test default configuration values."""
        config = ImmichConfig()
        assert config.url == ""
        assert config.api_key == ""

    def test_env_expansion(self):
        """Test environment variable expansion in config."""
        os.environ["TEST_IMMICH_URL"] = "https://test.example.com"
        config = ImmichConfig(url="${TEST_IMMICH_URL}", api_key="direct_key")
        assert config.url == "https://test.example.com"
        assert config.api_key == "direct_key"
        del os.environ["TEST_IMMICH_URL"]


class TestDefaultsConfig:
    """Tests for DefaultsConfig."""

    def test_default_values(self):
        """Test default configuration values."""
        config = DefaultsConfig()
        assert config.target_duration_minutes == 10
        assert config.output_orientation == "auto"
        assert config.scale_mode == "smart_crop"
        assert config.transition == "smart"
        assert config.transition_duration == 0.5

    def test_validation(self):
        """Test validation of values."""
        with pytest.raises(ValueError):
            DefaultsConfig(target_duration_minutes=0)

        with pytest.raises(ValueError):
            DefaultsConfig(target_duration_minutes=100)

        with pytest.raises(ValueError):
            DefaultsConfig(transition_duration=5.0)


class TestAnalysisConfig:
    """Tests for AnalysisConfig."""

    def test_default_values(self):
        """Test default configuration values."""
        config = AnalysisConfig()
        assert config.scene_threshold == 27.0
        assert config.min_scene_duration == 1.0
        assert config.duplicate_hash_threshold == 8
        assert config.keyframe_interval == 1.0


class TestOutputConfig:
    """Tests for OutputConfig."""

    def test_default_values(self):
        """Test default configuration values."""
        config = OutputConfig()
        assert config.format == "mp4"
        assert config.resolution == "1080p"
        assert config.codec == "h264"
        assert config.crf == 18

    def test_output_path(self):
        """Test output path expansion."""
        config = OutputConfig(directory="~/Videos/Test")
        assert config.output_path == Path.home() / "Videos" / "Test"

    def test_resolution_tuple(self):
        """Test resolution tuple conversion."""
        config = OutputConfig(resolution="1080p")
        assert config.resolution_tuple == (1920, 1080)

        config = OutputConfig(resolution="4k")
        assert config.resolution_tuple == (3840, 2160)


class TestConfig:
    """Tests for main Config class."""

    def test_default_config(self):
        """Test default configuration."""
        config = Config()
        assert config.immich.url == ""
        assert config.defaults.target_duration_minutes == 10
        assert config.output.format == "mp4"

    def test_yaml_roundtrip(self):
        """Test saving and loading from YAML."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"

            # Create config with custom values
            original = Config(
                immich=ImmichConfig(url="https://test.com", api_key="test_key"),
                defaults=DefaultsConfig(target_duration_minutes=15),
            )

            # Save to YAML
            original.save_yaml(config_path)

            # Load from YAML
            loaded = Config.from_yaml(config_path)

            assert loaded.immich.url == "https://test.com"
            assert loaded.immich.api_key == "test_key"
            assert loaded.defaults.target_duration_minutes == 15

    def test_missing_yaml_returns_defaults(self):
        """Test that missing YAML file returns default config."""
        config = Config.from_yaml(Path("/nonexistent/path/config.yaml"))
        assert config.immich.url == ""
        assert config.defaults.target_duration_minutes == 10

    def test_get_default_path(self):
        """Test default path generation."""
        path = Config.get_default_path()
        assert path == Path.home() / ".immich-memories" / "config.yaml"
