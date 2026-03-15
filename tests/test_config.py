"""Tests for configuration management."""

from __future__ import annotations

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
from immich_memories.config_models import ServerConfig


class TestExpandEnvVars:
    """Tests for environment variable expansion."""

    def test_expand_braces_format(self, monkeypatch):
        """Test ${VAR} format expansion."""
        monkeypatch.setenv("TEST_VAR", "test_value")
        result = expand_env_vars("prefix_${TEST_VAR}_suffix")
        assert result == "prefix_test_value_suffix"

    def test_expand_dollar_format(self, monkeypatch):
        """Test $VAR format expansion."""
        monkeypatch.setenv("TEST_VAR2", "another_value")
        result = expand_env_vars("$TEST_VAR2")
        assert result == "another_value"

    def test_missing_var_unchanged(self):
        """Test that missing variables are left unchanged."""
        result = expand_env_vars("${NONEXISTENT_VAR_12345}")
        assert result == "${NONEXISTENT_VAR_12345}"

    def test_no_expansion_needed(self):
        """Test strings without variables."""
        result = expand_env_vars("plain string")
        assert result == "plain string"

    def test_empty_string(self):
        """Empty string returns empty string."""
        assert expand_env_vars("") == ""

    def test_multiple_vars_in_one_string(self, monkeypatch):
        """Multiple variables expand independently."""
        monkeypatch.setenv("A_VAR", "hello")
        monkeypatch.setenv("B_VAR", "world")
        result = expand_env_vars("${A_VAR} ${B_VAR}")
        assert result == "hello world"

    def test_var_set_to_empty_string(self, monkeypatch):
        """Env var set to empty string expands to empty."""
        monkeypatch.setenv("EMPTY_VAR", "")
        assert expand_env_vars("${EMPTY_VAR}") == ""


class TestImmichConfig:
    """Tests for ImmichConfig."""

    def test_default_values(self):
        """Test default configuration values."""
        config = ImmichConfig()
        assert config.url == ""
        assert config.api_key == ""

    def test_env_expansion(self, monkeypatch):
        """Test environment variable expansion in config."""
        monkeypatch.setenv("TEST_IMMICH_URL", "https://test.example.com")
        config = ImmichConfig(url="${TEST_IMMICH_URL}", api_key="direct_key")
        assert config.url == "https://test.example.com"
        assert config.api_key == "direct_key"


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

    @pytest.mark.parametrize(
        "field,value,match",
        [
            pytest.param("target_duration_minutes", 0, "greater than", id="duration-zero"),
            pytest.param("target_duration_minutes", -1, "greater than", id="duration-negative"),
            pytest.param("target_duration_minutes", 100, "less than", id="duration-over-max"),
            pytest.param("transition_duration", 5.0, "less than", id="transition-over-max"),
            pytest.param("transition_duration", -0.1, "greater than", id="transition-negative"),
        ],
    )
    def test_validation_rejects_out_of_range(self, field, value, match):
        """Out-of-range values are rejected with a descriptive error."""
        with pytest.raises(ValueError, match=match):
            DefaultsConfig(**{field: value})

    def test_boundary_values_accepted(self):
        """Exact boundary values are accepted."""
        config = DefaultsConfig(target_duration_minutes=1, transition_duration=0.0)
        assert config.target_duration_minutes == 1
        assert config.transition_duration == 0.0

        config = DefaultsConfig(target_duration_minutes=60, transition_duration=2.0)
        assert config.target_duration_minutes == 60
        assert config.transition_duration == 2.0


class TestAnalysisConfig:
    """Tests for AnalysisConfig."""

    def test_default_values(self):
        """Test default configuration values."""
        config = AnalysisConfig()
        assert config.scene_threshold == 27.0
        assert config.min_scene_duration == 1.0
        assert config.duplicate_hash_threshold == 8
        assert config.keyframe_interval == 1.0

    @pytest.mark.parametrize(
        "field,value",
        [
            pytest.param("scene_threshold", 0.5, id="threshold-below-min"),
            pytest.param("scene_threshold", 101.0, id="threshold-above-max"),
            pytest.param("min_scene_duration", 0.1, id="scene-dur-below-min"),
            pytest.param("duplicate_hash_threshold", -1, id="hash-negative"),
            pytest.param("duplicate_hash_threshold", 65, id="hash-above-max"),
            pytest.param("keyframe_interval", 0.1, id="keyframe-below-min"),
        ],
    )
    def test_validation_rejects_out_of_range(self, field, value):
        """AnalysisConfig rejects out-of-range values."""
        with pytest.raises(ValueError):
            AnalysisConfig(**{field: value})

    def test_boundary_values_accepted(self):
        """Exact boundary values are accepted."""
        config = AnalysisConfig(
            scene_threshold=1.0,
            min_scene_duration=0.5,
            duplicate_hash_threshold=0,
            keyframe_interval=0.5,
        )
        assert config.scene_threshold == 1.0
        assert config.duplicate_hash_threshold == 0


class TestClipStyle:
    """Tests for clip_style parameter mapping."""

    def test_balanced_sets_defaults(self):
        """clip_style balanced maps to the standard duration params."""
        config = AnalysisConfig(clip_style="balanced")
        assert config.optimal_clip_duration == 5.0
        assert config.max_optimal_duration == 10.0
        assert config.target_extraction_ratio == 0.4
        assert config.max_segment_duration == 15.0
        assert config.min_segment_duration == 2.0

    def test_fast_cuts(self):
        """clip_style fast-cuts uses shorter durations."""
        config = AnalysisConfig(clip_style="fast-cuts")
        assert config.optimal_clip_duration == 3.0
        assert config.max_optimal_duration == 6.0
        assert config.target_extraction_ratio == 0.3
        assert config.max_segment_duration == 8.0
        assert config.min_segment_duration == 1.5

    def test_long_cuts(self):
        """clip_style long-cuts uses longer durations."""
        config = AnalysisConfig(clip_style="long-cuts")
        assert config.optimal_clip_duration == 8.0
        assert config.max_optimal_duration == 15.0
        assert config.target_extraction_ratio == 0.5
        assert config.max_segment_duration == 25.0
        assert config.min_segment_duration == 3.0

    def test_explicit_override_wins(self):
        """Explicit values override clip_style defaults."""
        config = AnalysisConfig(clip_style="balanced", optimal_clip_duration=7.0)
        assert config.optimal_clip_duration == 7.0
        assert config.max_optimal_duration == 10.0  # rest from balanced

    def test_no_clip_style_keeps_field_defaults(self):
        """Without clip_style, original field defaults are used."""
        config = AnalysisConfig()
        assert config.optimal_clip_duration == 5.0
        assert config.max_optimal_duration == 15.0  # original default, not balanced's 10.0

    def test_invalid_clip_style_rejected(self):
        """Invalid clip_style value is rejected."""
        with pytest.raises(ValueError):
            AnalysisConfig(clip_style="cinematic")


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

    @pytest.mark.parametrize(
        "resolution,expected",
        [
            pytest.param("1080p", (1920, 1080), id="1080p"),
            pytest.param("4k", (3840, 2160), id="4k"),
        ],
    )
    def test_resolution_tuple(self, resolution, expected):
        """Resolution string maps to correct pixel dimensions."""
        config = OutputConfig(resolution=resolution)
        assert config.resolution_tuple == expected


class TestServerConfig:
    """Tests for server configuration."""

    def test_defaults(self):
        """Default host is 0.0.0.0 and port is 8080."""
        cfg = ServerConfig()
        assert cfg.host == "0.0.0.0"  # noqa: S104
        assert cfg.port == 8080

    def test_port_validation_rejects_zero(self):
        """Port 0 is rejected."""
        with pytest.raises(Exception):  # noqa: B017
            ServerConfig(port=0)

    def test_port_validation_rejects_too_high(self):
        """Port above 65535 is rejected."""
        with pytest.raises(Exception):  # noqa: B017
            ServerConfig(port=70000)

    def test_ipv6_any(self):
        """IPv6 any-address (::) is accepted."""
        cfg = ServerConfig(host="::")
        assert cfg.host == "::"

    def test_ipv6_loopback(self):
        """IPv6 loopback (::1) is accepted."""
        cfg = ServerConfig(host="::1")
        assert cfg.host == "::1"


class TestConfig:
    """Tests for main Config class."""

    def test_default_config(self):
        """Test default configuration."""
        config = Config()
        assert config.immich.url == ""
        assert config.defaults.target_duration_minutes == 10
        assert config.output.format == "mp4"

    def test_default_server_config(self):
        """Default config includes server with standard defaults."""
        config = Config()
        assert config.server.host == "0.0.0.0"  # noqa: S104
        assert config.server.port == 8080

    def test_server_from_yaml(self, tmp_path):
        """Server section loads from YAML with IPv6 and custom port."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("server:\n  host: '::'\n  port: 9090\n")
        loaded = Config.from_yaml(config_path)
        assert loaded.server.host == "::"
        assert loaded.server.port == 9090

    def test_yaml_roundtrip(self, tmp_path):
        """Config survives a save-then-load cycle with values intact."""
        config_path = tmp_path / "config.yaml"

        original = Config(
            immich=ImmichConfig(url="https://test.com", api_key="test_key"),
            defaults=DefaultsConfig(target_duration_minutes=15),
        )

        original.save_yaml(config_path)
        loaded = Config.from_yaml(config_path)

        assert loaded.immich.url == "https://test.com"
        assert loaded.immich.api_key == "test_key"
        assert loaded.defaults.target_duration_minutes == 15

    def test_missing_yaml_returns_defaults(self):
        """Missing YAML file returns default config."""
        config = Config.from_yaml(Path("/nonexistent/path/config.yaml"))
        assert config.immich.url == ""
        assert config.defaults.target_duration_minutes == 10

    def test_get_default_path(self):
        """Default path points to ~/.immich-memories/config.yaml."""
        path = Config.get_default_path()
        assert path == Path.home() / ".immich-memories" / "config.yaml"

    def test_yaml_with_unknown_nested_field_in_known_section(self, tmp_path):
        """Unknown nested fields in a known section do not crash loading."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("defaults:\n  target_duration_minutes: 5\n")
        loaded = Config.from_yaml(config_path)
        assert loaded.defaults.target_duration_minutes == 5

    def test_empty_yaml_returns_defaults(self, tmp_path):
        """Empty YAML file returns default config."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("")
        loaded = Config.from_yaml(config_path)
        assert loaded.defaults.target_duration_minutes == 10
