"""Tests for the interactive LiveDisplay and logging integration."""

from __future__ import annotations

import logging

from rich.console import Console

from immich_memories.cli._helpers import (
    _active_display,
    print_error,
    print_info,
    print_success,
    set_active_display,
)
from immich_memories.cli._live_display import LiveDisplay, ProgressDisplay, _TaskState
from immich_memories.logging_config import (
    LiveDisplayLogHandler,
    configure_logging,
    install_live_handler,
    restore_handlers,
)

# ---------------------------------------------------------------------------
# ProgressDisplay protocol
# ---------------------------------------------------------------------------


class TestProgressDisplayProtocol:
    def test_live_display_satisfies_protocol(self) -> None:
        """LiveDisplay is a ProgressDisplay (structural subtype check)."""
        assert issubclass(LiveDisplay, ProgressDisplay)

    def test_live_display_instance_satisfies_protocol(self) -> None:
        console = Console(force_terminal=False)
        display = LiveDisplay(console=console)
        assert isinstance(display, ProgressDisplay)


# ---------------------------------------------------------------------------
# _TaskState
# ---------------------------------------------------------------------------


class TestTaskState:
    def test_create_task_state(self) -> None:
        state = _TaskState(description="Testing", total=100.0, done=False)
        assert state.description == "Testing"
        assert state.total == 100.0
        assert state.done is False

    def test_task_state_indeterminate(self) -> None:
        state = _TaskState(description="Spinner", total=None, done=False)
        assert state.total is None


# ---------------------------------------------------------------------------
# LiveDisplay — task lifecycle
# ---------------------------------------------------------------------------


class TestLiveDisplayTaskLifecycle:
    def _make_display(self) -> LiveDisplay:
        # WHY: force_terminal=False avoids ANSI output in tests
        return LiveDisplay(console=Console(force_terminal=False))

    def test_add_task_returns_id(self) -> None:
        display = self._make_display()
        with display:
            task_id = display.add_task("Step 1", total=None)
            assert isinstance(task_id, int)
            assert task_id in display._tasks

    def test_add_task_auto_completes_prior_spinner(self) -> None:
        display = self._make_display()
        with display:
            t1 = display.add_task("Step 1", total=None)
            t2 = display.add_task("Step 2", total=None)
            # First task should be auto-completed
            assert display._tasks[t1].done is True
            assert display._tasks[t2].done is False
            assert display._active_task_id == t2

    def test_update_completed_true_finishes_spinner(self) -> None:
        display = self._make_display()
        with display:
            t = display.add_task("Connecting...", total=None)
            display.update(t, completed=True)
            assert display._tasks[t].done is True
            assert display._active_task_id is None

    def test_update_with_progress_value(self) -> None:
        display = self._make_display()
        with display:
            t = display.add_task("Analyzing...", total=100)
            display.update(t, completed=50)
            # Task should NOT be done (it has progress, not "completed=True")
            assert display._tasks[t].done is False

    def test_update_description(self) -> None:
        display = self._make_display()
        with display:
            t = display.add_task("Phase 1", total=100)
            display.update(t, description="Phase 2")
            assert display._tasks[t].description == "Phase 2"

    def test_exit_completes_remaining_task(self) -> None:
        display = self._make_display()
        with display:
            t = display.add_task("Running...", total=None)
        # After exit, the task should be done
        assert display._tasks[t].done is True

    def test_completed_lines_accumulate(self) -> None:
        display = self._make_display()
        with display:
            t1 = display.add_task("Step 1", total=None)
            display.update(t1, completed=True)
            t2 = display.add_task("Step 2", total=None)
            display.update(t2, completed=True)
        assert len(display._completed_lines) >= 2


# ---------------------------------------------------------------------------
# LiveDisplay — log lines
# ---------------------------------------------------------------------------


class TestLiveDisplayLogLines:
    def test_add_log_stores_lines(self) -> None:
        display = LiveDisplay(console=Console(force_terminal=False))
        with display:
            display.add_log("First message")
            display.add_log("Second message")
            assert len(display._log_lines) == 2

    def test_log_lines_capped_at_max(self) -> None:
        display = LiveDisplay(console=Console(force_terminal=False))
        with display:
            for i in range(20):
                display.add_log(f"Line {i}")
            # Should be capped at MAX_LOG_LINES (5)
            assert len(display._log_lines) == 5
            assert "Line 19" in display._log_lines[-1]

    def test_print_message_adds_completed_line(self) -> None:
        display = LiveDisplay(console=Console(force_terminal=False))
        with display:
            display.print_message("[green]✓[/green] Custom message")
        assert any("Custom message" in line for line in display._completed_lines)


# ---------------------------------------------------------------------------
# LiveDisplay — stop method
# ---------------------------------------------------------------------------


class TestLiveDisplayStop:
    def test_stop_finalizes_active_task(self) -> None:
        display = LiveDisplay(console=Console(force_terminal=False))
        with display:
            t = display.add_task("Running...", total=None)
            display.stop()
            assert display._tasks[t].done is True

    def test_stop_clears_active_display(self) -> None:
        display = LiveDisplay(console=Console(force_terminal=False))
        with display:
            assert _active_display.get() is display
            display.stop()
            assert _active_display.get() is None


# ---------------------------------------------------------------------------
# LiveDisplay — context manager sets/clears active display
# ---------------------------------------------------------------------------


class TestLiveDisplayActiveDisplay:
    def test_enter_sets_active_display(self) -> None:
        display = LiveDisplay(console=Console(force_terminal=False))
        with display:
            assert _active_display.get() is display
        assert _active_display.get() is None

    def test_print_helpers_route_through_display(self) -> None:
        display = LiveDisplay(console=Console(force_terminal=False))
        with display:
            print_success("Good")
            print_error("Bad")
            print_info("Info")
        # All three should appear in completed lines
        assert any("Good" in line for line in display._completed_lines)
        assert any("Bad" in line for line in display._completed_lines)
        assert any("Info" in line for line in display._completed_lines)

    def test_print_helpers_use_console_when_no_display(self) -> None:
        """Without active display, helpers should use console (no crash)."""
        set_active_display(None)
        # Should not raise
        print_success("test")
        print_error("test")
        print_info("test")


# ---------------------------------------------------------------------------
# LiveDisplayLogHandler
# ---------------------------------------------------------------------------


class TestLiveDisplayLogHandler:
    def test_handler_routes_to_display(self) -> None:
        display = LiveDisplay(console=Console(force_terminal=False))
        handler = LiveDisplayLogHandler(display)
        handler.addFilter(logging.Filter())

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Hello from handler",
            args=(),
            exc_info=None,
        )
        handler.emit(record)
        assert any("Hello from handler" in line for line in display._log_lines)


# ---------------------------------------------------------------------------
# install_live_handler / restore_handlers
# ---------------------------------------------------------------------------


class TestInstallRestoreHandlers:
    def test_install_replaces_stream_handlers(self) -> None:
        configure_logging(fmt="text", level="INFO")
        root = logging.getLogger()
        original_count = len(root.handlers)

        display = LiveDisplay(console=Console(force_terminal=False))
        original = install_live_handler(display)
        assert len(original) == original_count

        # Should have a LiveDisplayLogHandler now
        live_handlers = [h for h in root.handlers if isinstance(h, LiveDisplayLogHandler)]
        assert len(live_handlers) == 1

        # Stream handlers should be removed
        stream_handlers = [
            h
            for h in root.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, (logging.FileHandler, LiveDisplayLogHandler))
        ]
        assert len(stream_handlers) == 0

        # Restore
        restore_handlers(original)
        live_handlers = [h for h in root.handlers if isinstance(h, LiveDisplayLogHandler)]
        assert len(live_handlers) == 0

    def test_file_handlers_preserved_during_install(self) -> None:
        """File handlers should survive install/restore cycle."""
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            log_path = f.name

        try:
            configure_logging(fmt="text", level="INFO", log_file=log_path)
            root = logging.getLogger()
            file_handlers_before = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
            assert len(file_handlers_before) == 1

            display = LiveDisplay(console=Console(force_terminal=False))
            original = install_live_handler(display)

            # File handler should still be present
            file_handlers_during = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
            assert len(file_handlers_during) == 1

            restore_handlers(original)

            # File handler still present after restore
            file_handlers_after = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
            assert len(file_handlers_after) == 1
        finally:
            Path(log_path).unlink(missing_ok=True)

    def test_logging_routes_to_display_during_install(self) -> None:
        configure_logging(fmt="text", level="INFO")
        display = LiveDisplay(console=Console(force_terminal=False))
        original = install_live_handler(display)

        try:
            logger = logging.getLogger("test.install")
            logger.info("Routed message")
            assert any("Routed message" in line for line in display._log_lines)
        finally:
            restore_handlers(original)


# ---------------------------------------------------------------------------
# configure_logging — file handler
# ---------------------------------------------------------------------------


class TestConfigureLoggingFile:
    def test_log_file_creates_file_handler(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            log_path = f.name

        try:
            configure_logging(fmt="text", level="INFO", log_file=log_path)
            root = logging.getLogger()
            file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
            assert len(file_handlers) == 1

            logger = logging.getLogger("test.file")
            logger.info("File log test")

            content = Path(log_path).read_text()
            assert "File log test" in content
        finally:
            Path(log_path).unlink(missing_ok=True)
            configure_logging(fmt="text", level="INFO")

    def test_no_log_file_means_no_file_handler(self) -> None:
        configure_logging(fmt="text", level="INFO", log_file=None)
        root = logging.getLogger()
        file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 0
