"""Tests for config error formatting and CLI integration."""

from __future__ import annotations

from unittest.mock import patch

import yaml
from click.testing import CliRunner
from pydantic import ValidationError

from immich_memories.cli._config_errors import format_validation_error, format_yaml_error
from immich_memories.config_models import DefaultsConfig


class TestFormatValidationError:
    """Test format_validation_error()."""

    def test_single_field_error(self):
        """Single field constraint violation is formatted clearly."""
        try:
            DefaultsConfig(target_duration_minutes=-1)
        except ValidationError as e:
            result = format_validation_error(e)
            assert "Configuration error:" in result
            assert "target_duration_minutes" in result

    def test_includes_field_path(self):
        """Error includes the field path."""
        try:
            DefaultsConfig(avg_clip_duration=-5)
        except ValidationError as e:
            result = format_validation_error(e)
            assert "avg_clip_duration" in result

    def test_includes_input_value(self):
        """Error includes the actual value that was provided."""
        try:
            DefaultsConfig(target_duration_minutes=-1)
        except ValidationError as e:
            result = format_validation_error(e)
            assert "Got:" in result
            assert "-1" in result


class TestFormatYamlError:
    """Test format_yaml_error()."""

    def test_scanner_error_with_mark(self):
        """Scanner error with problem_mark shows line/column."""
        bad_yaml = "key: [\ninvalid yaml {"
        try:
            yaml.safe_load(bad_yaml)
        except yaml.YAMLError as e:
            result = format_yaml_error(e)
            assert "line" in result.lower()
            assert "YAML syntax error" in result
            assert "Check your config file" in result

    def test_generic_yaml_error(self):
        """Generic YAML error without mark still formats nicely."""
        error = yaml.YAMLError("something went wrong")
        result = format_yaml_error(error)
        assert "YAML syntax error" in result
        assert "Check your config file" in result


class TestCLIConfigErrorIntegration:
    """Test that CLI handles config errors gracefully."""

    def test_invalid_yaml_shows_friendly_error(self, tmp_path):
        """Invalid YAML config file shows a friendly error message."""
        from immich_memories.cli import main

        bad_config = tmp_path / "bad.yaml"
        bad_config.write_text("key: [\ninvalid {")

        runner = CliRunner()
        with patch("immich_memories.cli.init_config_dir"):
            result = runner.invoke(main, ["-c", str(bad_config), "config", "--help"])

        assert result.exit_code != 0
        assert "YAML syntax error" in result.output or "Error" in result.output

    def test_invalid_field_shows_friendly_error(self, tmp_path):
        """Config with invalid field values shows field-level error."""
        from immich_memories.cli import main

        bad_config = tmp_path / "bad.yaml"
        bad_config.write_text("defaults:\n  target_duration_minutes: -999\n")

        runner = CliRunner()
        with patch("immich_memories.cli.init_config_dir"):
            result = runner.invoke(main, ["-c", str(bad_config), "config", "--help"])

        assert result.exit_code != 0
        assert "target_duration_minutes" in result.output or "Configuration error" in result.output
