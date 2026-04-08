"""Tests for brute-force rate limiter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from immich_memories.ui.auth import (
    _WINDOW_SECONDS,
    is_rate_limited,
    record_failed_login,
    reset_rate_limiter,
)


class TestRateLimiter:
    """In-memory rate limiter blocks after 5 failures in 10 minutes."""

    def setup_method(self):
        reset_rate_limiter()

    def teardown_method(self):
        reset_rate_limiter()

    def test_not_limited_initially(self):
        assert is_rate_limited("10.0.0.1") is False

    def test_not_limited_after_four_failures(self):
        for _ in range(4):
            record_failed_login("10.0.0.1")
        assert is_rate_limited("10.0.0.1") is False

    def test_limited_after_five_failures(self):
        for _ in range(5):
            record_failed_login("10.0.0.1")
        assert is_rate_limited("10.0.0.1") is True

    def test_different_ips_are_independent(self):
        for _ in range(5):
            record_failed_login("10.0.0.1")
        assert is_rate_limited("10.0.0.2") is False

    def test_old_attempts_expire(self):
        """Attempts outside the window are not counted."""
        old_time = datetime.now(UTC) - timedelta(seconds=_WINDOW_SECONDS + 1)
        with patch("immich_memories.ui.auth.datetime") as mock_dt:
            mock_dt.now.return_value = old_time
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            for _ in range(5):
                record_failed_login("10.0.0.3")

        # Now check with real time — old attempts should be expired
        assert is_rate_limited("10.0.0.3") is False

    def test_reset_clears_all(self):
        for _ in range(5):
            record_failed_login("10.0.0.1")
        reset_rate_limiter()
        assert is_rate_limited("10.0.0.1") is False

    def test_cleanup_removes_stale_entries(self):
        """Stale entries are cleaned during record_failed_login."""
        old_time = datetime.now(UTC) - timedelta(seconds=_WINDOW_SECONDS + 60)
        with patch("immich_memories.ui.auth.datetime") as mock_dt:
            mock_dt.now.return_value = old_time
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            record_failed_login("10.0.0.99")

        # Recording a new attempt triggers cleanup of stale entries
        record_failed_login("10.0.0.1")
        assert is_rate_limited("10.0.0.99") is False
