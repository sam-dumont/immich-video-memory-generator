"""Tests for run_id injection into log records."""

from __future__ import annotations

import json
import logging


class TestRunIdWiredInPipeline:
    """generate_memory should set run_id for logging."""

    def test_generate_memory_calls_set_current_run_id(self):
        """generate_memory must call set_current_run_id."""
        import inspect

        import immich_memories.generate as gen_mod

        source = inspect.getsource(gen_mod.generate_memory)
        # Should import and call set_current_run_id somewhere in the pipeline
        assert "set_current_run_id" in source or "set_current_run_id" in inspect.getsource(
            gen_mod._generate_memory_inner
            if hasattr(gen_mod, "_generate_memory_inner")
            else gen_mod.generate_memory
        )


class TestRunIdContextVar:
    """set_current_run_id / get_current_run_id manage a context variable."""

    def test_default_is_none(self):
        from immich_memories.logging_config import get_current_run_id, set_current_run_id

        # Reset to default state
        set_current_run_id(None)
        assert get_current_run_id() is None

    def test_set_and_get(self):
        from immich_memories.logging_config import get_current_run_id, set_current_run_id

        set_current_run_id("20250101_143052_a7b3")
        try:
            assert get_current_run_id() == "20250101_143052_a7b3"
        finally:
            set_current_run_id(None)


class TestRunIdFilter:
    """RunIdFilter should inject run_id into every log record."""

    def test_adds_run_id_to_record(self):
        from immich_memories.logging_config import RunIdFilter, set_current_run_id

        set_current_run_id("20250101_143052_a7b3")
        try:
            f = RunIdFilter()
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="hello",
                args=(),
                exc_info=None,
            )
            f.filter(record)
            assert record.run_id == "20250101_143052_a7b3"  # type: ignore[attr-defined]
        finally:
            set_current_run_id(None)

    def test_dash_when_no_run_id(self):
        from immich_memories.logging_config import RunIdFilter, set_current_run_id

        set_current_run_id(None)
        f = RunIdFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        f.filter(record)
        assert record.run_id == "-"  # type: ignore[attr-defined]


class TestTextFormatIncludesRunId:
    """Text log format should include run_id field."""

    def test_run_id_in_text_output(self):
        from immich_memories.logging_config import RunIdFilter, set_current_run_id

        set_current_run_id("20250101_143052_a7b3")
        try:
            handler = logging.StreamHandler()
            from immich_memories.logging_config import TEXT_FORMAT

            handler.setFormatter(logging.Formatter(TEXT_FORMAT))
            handler.addFilter(RunIdFilter())

            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="hello",
                args=(),
                exc_info=None,
            )
            RunIdFilter().filter(record)
            output = handler.format(record)
            assert "20250101_143052_a7b3" in output
        finally:
            set_current_run_id(None)


class TestJsonFormatIncludesRunId:
    """JSON log format should include run_id field."""

    def test_run_id_in_json_output(self):
        from immich_memories.logging_config import JsonFormatter, RunIdFilter, set_current_run_id

        set_current_run_id("20250101_143052_a7b3")
        try:
            formatter = JsonFormatter()
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="hello",
                args=(),
                exc_info=None,
            )
            RunIdFilter().filter(record)
            output = formatter.format(record)
            data = json.loads(output)
            assert data["run_id"] == "20250101_143052_a7b3"
        finally:
            set_current_run_id(None)

    def test_no_run_id_field_when_dash(self):
        """When no run_id is set, JSON output should omit or use dash."""
        from immich_memories.logging_config import JsonFormatter, RunIdFilter, set_current_run_id

        set_current_run_id(None)
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        RunIdFilter().filter(record)
        output = formatter.format(record)
        data = json.loads(output)
        # run_id should not be present when there's no active run
        assert "run_id" not in data
