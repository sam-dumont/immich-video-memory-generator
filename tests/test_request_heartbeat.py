"""Tests for RequestHeartbeat, the periodic-log helper for long HTTP waits.

A production run once sat at 0% CPU for ~80 minutes with an established
socket to a local LLM server and zero log output — indistinguishable from a
crash. RequestHeartbeat wraps a blocking call so a stall like that produces
periodic log lines instead of silence.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from immich_memories.analysis.request_heartbeat import RequestHeartbeat


class TestRequestHeartbeat:
    def test_fast_operation_logs_nothing(self, caplog: pytest.LogCaptureFixture) -> None:
        """A call that finishes well inside one interval should stay silent."""
        with (
            caplog.at_level(logging.INFO, logger="immich_memories.analysis.request_heartbeat"),
            RequestHeartbeat("test request", interval_seconds=1.0),
        ):
            pass  # finishes instantly, long before the first heartbeat would fire

        assert caplog.records == []

    def test_slow_operation_emits_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """A call that outlasts several intervals should surface a WARNING."""

        def warning_count() -> int:
            return len([r for r in caplog.records if r.levelno >= logging.WARNING])

        with (
            caplog.at_level(logging.INFO, logger="immich_memories.analysis.request_heartbeat"),
            RequestHeartbeat(
                "test request",
                interval_seconds=0.02,
                warn_after_occurrences=2,
            ),
        ):
            # Wait for the heartbeat thread to actually fire rather than sleeping a
            # fixed span and hoping it was scheduled. A loaded CI runner can starve
            # a daemon thread well past several 0.02s intervals.
            deadline = time.monotonic() + 10.0
            while warning_count() < 1 and time.monotonic() < deadline:
                time.sleep(0.01)

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) >= 1
        assert "test request" in warnings[0].getMessage()
        assert "still outstanding" in warnings[0].getMessage()

    def test_thread_cleaned_up_when_wrapped_block_raises(self) -> None:
        """An exception in the wrapped block must not leak the heartbeat thread."""
        with (
            pytest.raises(ValueError, match="boom"),
            RequestHeartbeat("test request", interval_seconds=0.02),
        ):
            raise ValueError("boom")

        heartbeat_threads = [t for t in threading.enumerate() if t.name == "llm-request-heartbeat"]
        assert heartbeat_threads == []
