"""Tests for run tracker and format_duration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from immich_memories.tracking.run_tracker import RunTracker, format_duration

_TEST_DB_PATH = Path("/tmp/test_tracker.db")


class TestFormatDuration:
    """Tests for format_duration utility."""

    @pytest.mark.parametrize(
        "seconds,expected",
        [
            pytest.param(0, "0s", id="zero"),
            pytest.param(30, "30s", id="half-minute"),
            pytest.param(59, "59s", id="just-under-minute"),
            pytest.param(60, "1m 00s", id="one-minute"),
            pytest.param(90, "1m 30s", id="ninety-seconds"),
            pytest.param(3599, "59m 59s", id="just-under-hour"),
            pytest.param(3600, "1h 00m", id="one-hour"),
            pytest.param(3661, "1h 01m", id="hour-and-one-minute"),
            pytest.param(7200, "2h 00m", id="two-hours"),
            pytest.param(0.4, "0s", id="sub-second-rounds"),
            pytest.param(59.9, "60s", id="rounds-up-to-60s"),
        ],
    )
    def test_format(self, seconds: float, expected: str):
        """Duration is formatted in human-readable form."""
        assert format_duration(seconds) == expected


class TestRunTrackerInit:
    """Tests for RunTracker initialization."""

    # WHY: RunDatabase opens a SQLite connection — isolate tracker logic from disk I/O
    @patch("immich_memories.tracking.run_tracker.RunDatabase")
    def test_generates_run_id_when_none(self, mock_db_cls: MagicMock):
        """RunTracker generates an ID when none provided."""
        tracker = RunTracker(db_path=_TEST_DB_PATH)
        assert tracker.run_id is not None
        assert len(tracker.run_id) == 20

    # WHY: RunDatabase opens a SQLite connection — isolate tracker logic from disk I/O
    @patch("immich_memories.tracking.run_tracker.RunDatabase")
    def test_uses_provided_run_id(self, mock_db_cls: MagicMock):
        """RunTracker uses the provided run ID."""
        tracker = RunTracker(run_id="20250101_000000_abcd", db_path=_TEST_DB_PATH)
        assert tracker.run_id == "20250101_000000_abcd"

    # WHY: RunDatabase opens a SQLite connection — isolate tracker logic from disk I/O
    @patch("immich_memories.tracking.run_tracker.RunDatabase")
    def test_current_run_is_none_initially(self, mock_db_cls: MagicMock):
        """current_run is None before start_run."""
        tracker = RunTracker(db_path=_TEST_DB_PATH)
        assert tracker.current_run is None


class TestRunTrackerPhases:
    """Tests for phase tracking logic."""

    # WHY: RunDatabase opens a SQLite connection — isolate phase tracking from disk I/O
    @patch("immich_memories.tracking.run_tracker.RunDatabase")
    def test_start_phase_sets_state(self, mock_db_cls: MagicMock):
        """start_phase records the phase name and time."""
        tracker = RunTracker(db_path=_TEST_DB_PATH)
        tracker.start_phase("discovery", total_items=100)
        assert tracker._current_phase == "discovery"
        assert tracker._phase_items_total == 100
        assert tracker._phase_start is not None

    # WHY: RunDatabase opens a SQLite connection — isolate phase tracking from disk I/O
    @patch("immich_memories.tracking.run_tracker.RunDatabase")
    def test_start_phase_completes_previous(self, mock_db_cls: MagicMock):
        """Starting a new phase completes the previous one."""
        tracker = RunTracker(db_path=_TEST_DB_PATH)
        tracker.start_phase("phase1", total_items=10)
        tracker.start_phase("phase2", total_items=20)
        # Previous phase should be cleared
        assert tracker._current_phase == "phase2"
        # save_phase_stats should have been called for phase1
        tracker.db.save_phase_stats.assert_called_once()

    # WHY: RunDatabase opens a SQLite connection — isolate phase tracking from disk I/O
    @patch("immich_memories.tracking.run_tracker.RunDatabase")
    def test_complete_phase_resets_state(self, mock_db_cls: MagicMock):
        """complete_phase clears phase state."""
        tracker = RunTracker(db_path=_TEST_DB_PATH)
        tracker.start_phase("analysis", total_items=50)
        tracker.complete_phase(items_processed=50)
        assert tracker._current_phase is None
        assert tracker._phase_start is None
        assert tracker._phase_items_total == 0

    # WHY: RunDatabase opens a SQLite connection — isolate phase tracking from disk I/O
    @patch("immich_memories.tracking.run_tracker.RunDatabase")
    def test_complete_phase_noop_without_active_phase(self, mock_db_cls: MagicMock):
        """complete_phase does nothing if no phase is active."""
        tracker = RunTracker(db_path=_TEST_DB_PATH)
        tracker.complete_phase()  # Should not raise
        tracker.db.save_phase_stats.assert_not_called()

    # WHY: RunDatabase opens a SQLite connection — isolate phase tracking from disk I/O
    @patch("immich_memories.tracking.run_tracker.RunDatabase")
    def test_complete_phase_defaults_items_to_total(self, mock_db_cls: MagicMock):
        """complete_phase defaults items_processed to total_items."""
        tracker = RunTracker(db_path=_TEST_DB_PATH)
        tracker.start_phase("export", total_items=25)
        tracker.complete_phase()
        call_args = tracker.db.save_phase_stats.call_args
        phase_stats = call_args[0][1]
        assert phase_stats.items_processed == 25


class TestRunTrackerStartRun:
    """Tests for start_run with mocked dependencies."""

    # WHY: capture_system_info runs subprocess calls for CPU/GPU/RAM — avoid hardware probing
    @patch("immich_memories.tracking.run_tracker.capture_system_info")
    # WHY: RunDatabase opens a SQLite connection — isolate run lifecycle from disk I/O
    @patch("immich_memories.tracking.run_tracker.RunDatabase")
    def test_start_run_captures_system_info(self, mock_db_cls: MagicMock, mock_capture: MagicMock):
        """start_run captures system info when enabled."""
        mock_capture.return_value = MagicMock()
        tracker = RunTracker(db_path=_TEST_DB_PATH, capture_system=True)
        run_id = tracker.start_run(person_name="Alice")
        assert run_id == tracker.run_id
        mock_capture.assert_called_once()
        tracker.db.save_run.assert_called_once()

    # WHY: capture_system_info runs subprocess calls for CPU/GPU/RAM — verify it's not called
    @patch("immich_memories.tracking.run_tracker.capture_system_info")
    # WHY: RunDatabase opens a SQLite connection — isolate run lifecycle from disk I/O
    @patch("immich_memories.tracking.run_tracker.RunDatabase")
    def test_start_run_skips_system_info_when_disabled(
        self, mock_db_cls: MagicMock, mock_capture: MagicMock
    ):
        """start_run skips system info capture when disabled."""
        tracker = RunTracker(db_path=_TEST_DB_PATH, capture_system=False)
        tracker.start_run()
        mock_capture.assert_not_called()

    # WHY: capture_system_info runs subprocess calls — simulate failure to test resilience
    @patch("immich_memories.tracking.run_tracker.capture_system_info")
    # WHY: RunDatabase opens a SQLite connection — isolate run lifecycle from disk I/O
    @patch("immich_memories.tracking.run_tracker.RunDatabase")
    def test_start_run_handles_system_info_failure(
        self, mock_db_cls: MagicMock, mock_capture: MagicMock
    ):
        """start_run continues if system info capture fails."""
        mock_capture.side_effect = RuntimeError("no GPU")
        tracker = RunTracker(db_path=_TEST_DB_PATH, capture_system=True)
        run_id = tracker.start_run()  # Should not raise
        assert run_id is not None


class TestRunTrackerFailCancel:
    """Tests for fail_run and cancel_run."""

    # WHY: RunDatabase opens a SQLite connection — verify status update without real DB
    @patch("immich_memories.tracking.run_tracker.RunDatabase")
    def test_fail_run_updates_status(self, mock_db_cls: MagicMock):
        """fail_run marks the run as failed in the database."""
        tracker = RunTracker(db_path=_TEST_DB_PATH)
        tracker.fail_run("out of memory", errors_count=3)
        tracker.db.update_run_status.assert_called_once()
        call_kwargs = tracker.db.update_run_status.call_args[1]
        assert call_kwargs["status"] == "failed"
        assert call_kwargs["errors_count"] == 3

    # WHY: RunDatabase opens a SQLite connection — verify phase cleanup on failure
    @patch("immich_memories.tracking.run_tracker.RunDatabase")
    def test_fail_run_completes_active_phase(self, mock_db_cls: MagicMock):
        """fail_run completes any active phase before failing."""
        tracker = RunTracker(db_path=_TEST_DB_PATH)
        tracker.start_phase("analysis")
        tracker.fail_run("crash")
        # Phase should be completed first, then status updated
        tracker.db.save_phase_stats.assert_called_once()
        tracker.db.update_run_status.assert_called_once()

    # WHY: RunDatabase opens a SQLite connection — verify cancellation without real DB
    @patch("immich_memories.tracking.run_tracker.RunDatabase")
    def test_cancel_run_updates_status(self, mock_db_cls: MagicMock):
        """cancel_run marks the run as cancelled."""
        tracker = RunTracker(db_path=_TEST_DB_PATH)
        tracker.cancel_run()
        call_kwargs = tracker.db.update_run_status.call_args[1]
        assert call_kwargs["status"] == "cancelled"
