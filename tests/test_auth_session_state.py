"""Tests for per-session AppState management."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.ui.state import (
    _MAX_SESSIONS,
    AppState,
    _sessions,
    cleanup_stale_sessions,
    get_app_state,
    remove_session,
    reset_app_state,
)


@pytest.fixture(autouse=True)
def _clear_sessions():
    """Clear session store before and after each test."""
    _sessions.clear()
    yield
    _sessions.clear()


def _make_mock_app(storage: dict | None = None) -> MagicMock:
    """Create a mock NiceGUI app with a storage.user dict."""
    mock_app = MagicMock()
    mock_app.storage.user = storage if storage is not None else {}
    return mock_app


class TestGetAppState:
    """Test per-session get_app_state()."""

    def test_creates_new_session(self):
        """First call creates a new session_id in storage and state in _sessions."""
        mock_app = _make_mock_app()
        with patch("nicegui.app", mock_app):
            state = get_app_state()

        assert isinstance(state, AppState)
        session_id = mock_app.storage.user["session_id"]
        assert session_id in _sessions
        assert _sessions[session_id] is state
        assert state.last_accessed is not None

    def test_returns_existing_session(self):
        """Repeated calls with same session_id return the same AppState."""
        mock_app = _make_mock_app()
        with patch("nicegui.app", mock_app):
            state1 = get_app_state()
            state2 = get_app_state()

        assert state1 is state2

    def test_independent_sessions(self):
        """Two different storages get different AppState instances."""
        mock_app_a = _make_mock_app()
        mock_app_b = _make_mock_app()

        with patch("nicegui.app", mock_app_a):
            state_a = get_app_state()
            state_a.step = 3

        with patch("nicegui.app", mock_app_b):
            state_b = get_app_state()

        assert state_a is not state_b
        assert state_a.step == 3
        assert state_b.step == 1  # default


class TestSessionCleanup:
    """Test cleanup_stale_sessions()."""

    def test_evicts_stale_sessions(self):
        """Sessions idle beyond max_age are removed; fresh ones kept."""
        stale_state = AppState()
        stale_state.last_accessed = datetime.now() - timedelta(hours=10)
        _sessions["stale-id"] = stale_state

        fresh_state = AppState()
        fresh_state.last_accessed = datetime.now()
        _sessions["fresh-id"] = fresh_state

        cleanup_stale_sessions(max_age_hours=2)

        assert "stale-id" not in _sessions
        assert "fresh-id" in _sessions

    def test_enforces_max_sessions(self):
        """After cleanup, sessions are capped at _MAX_SESSIONS."""
        for i in range(_MAX_SESSIONS + 5):
            state = AppState()
            state.last_accessed = datetime.now() - timedelta(seconds=i)
            _sessions[f"session-{i}"] = state

        cleanup_stale_sessions(max_age_hours=999)  # nothing stale

        assert len(_sessions) <= _MAX_SESSIONS


class TestRemoveSession:
    """Test remove_session()."""

    def test_removes_existing_session(self):
        """Removes a session that exists."""
        _sessions["to-remove"] = AppState()
        remove_session("to-remove")
        assert "to-remove" not in _sessions

    def test_noop_for_missing_session(self):
        """Does not raise when session doesn't exist."""
        remove_session("nonexistent")  # should not raise


class TestResetAppState:
    """Test reset_app_state() with per-session store."""

    def test_reset_creates_fresh_state(self):
        """reset_app_state() replaces the session's state with a fresh one."""
        mock_app = _make_mock_app()
        with patch("nicegui.app", mock_app):
            state1 = get_app_state()
            state1.step = 3
            state2 = reset_app_state()

        assert state2.step == 1
        assert state1 is not state2
