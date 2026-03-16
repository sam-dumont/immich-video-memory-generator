"""Tests for structured logging configuration."""

from __future__ import annotations

import json
import logging

from immich_memories.logging_config import JsonFormatter, configure_logging


class TestConfigureLogging:
    """Test configure_logging() setup."""

    def test_text_format_default(self):
        """Default format is text with StreamHandler."""
        configure_logging(fmt="text")
        root = logging.getLogger()
        assert root.handlers
        handler = root.handlers[-1]
        assert not isinstance(handler.formatter, JsonFormatter)

    def test_json_format(self):
        """JSON format uses JsonFormatter."""
        configure_logging(fmt="json")
        root = logging.getLogger()
        handler = root.handlers[-1]
        assert isinstance(handler.formatter, JsonFormatter)

    def test_default_is_text(self, monkeypatch):
        """Calling with no args defaults to text."""
        monkeypatch.delenv("IMMICH_MEMORIES_LOG_FORMAT", raising=False)
        configure_logging()
        root = logging.getLogger()
        handler = root.handlers[-1]
        assert not isinstance(handler.formatter, JsonFormatter)

    def test_env_var_override(self, monkeypatch):
        """IMMICH_MEMORIES_LOG_FORMAT env var overrides default."""
        monkeypatch.setenv("IMMICH_MEMORIES_LOG_FORMAT", "json")
        configure_logging()
        root = logging.getLogger()
        handler = root.handlers[-1]
        assert isinstance(handler.formatter, JsonFormatter)

    def test_log_level_setting(self):
        """Log level is set correctly."""
        configure_logging(fmt="text", level="DEBUG")
        root = logging.getLogger()
        assert root.level == logging.DEBUG

        # Reset
        configure_logging(fmt="text", level="INFO")


class TestJsonFormatter:
    """Test JsonFormatter output."""

    def test_format_produces_valid_json(self):
        """Format output is valid JSON with expected fields."""
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Hello %s",
            args=("world",),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)

        assert data["level"] == "INFO"
        assert data["logger"] == "test.logger"
        assert data["message"] == "Hello world"
        assert "timestamp" in data

    def test_format_includes_exception(self):
        """Exception info is included in JSON output."""
        formatter = JsonFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys

            record = logging.LogRecord(
                name="test.logger",
                level=logging.ERROR,
                pathname="test.py",
                lineno=1,
                msg="Something failed",
                args=(),
                exc_info=sys.exc_info(),
            )

        output = formatter.format(record)
        data = json.loads(output)

        assert "exception" in data
        assert any("ValueError" in line for line in data["exception"])

    def test_format_no_exception_when_none(self):
        """No exception field when exc_info is None."""
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="ok",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert "exception" not in data

    def test_single_line_output(self):
        """JSON output is a single line (no embedded newlines in JSON)."""
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="line one",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        assert "\n" not in output
