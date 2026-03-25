"""Tests for the interactive LiveDisplay and logging integration.

Tests verify behavior through public interfaces (rendered output, context
manager side effects, logging routing) — not internal state.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from rich.console import Console, RenderableType

from immich_memories.cli._helpers import (
    get_active_display,
    print_error,
    print_info,
    print_success,
    set_active_display,
)
from immich_memories.cli._live_display import LiveDisplay, ProgressDisplay
from immich_memories.logging_config import (
    LiveDisplayLogHandler,
    configure_logging,
    install_live_handler,
    restore_handlers,
)


def _render_text(renderable: RenderableType) -> str:
    """Render a Rich renderable to plain text for assertion."""
    console = Console(force_terminal=False, no_color=True, width=120)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


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
# LiveDisplay — task lifecycle (verified through rendered output)
# ---------------------------------------------------------------------------


class TestLiveDisplayTaskLifecycle:
    def _make_display(self) -> LiveDisplay:
        # WHY: force_terminal=False avoids ANSI output in tests
        return LiveDisplay(console=Console(force_terminal=False))

    def test_add_task_returns_task_id(self) -> None:
        display = self._make_display()
        with display:
            task_id = display.add_task("Step 1", total=None)
            assert isinstance(task_id, int)

    def test_completed_spinner_renders_checkmark(self) -> None:
        """Finishing a spinner task adds a '✓' line to rendered output."""
        display = self._make_display()
        with display:
            t = display.add_task("Connecting...", total=None)
            display.update(t, completed=True)
            rendered = _render_text(display.render())
        assert "✓" in rendered
        assert "Connecting..." in rendered

    def test_auto_complete_prior_spinner_on_new_task(self) -> None:
        """Adding a new task auto-completes the previous spinner task."""
        display = self._make_display()
        with display:
            display.add_task("Step 1", total=None)
            display.add_task("Step 2", total=None)
            rendered = _render_text(display.render())
        # Step 1 should appear as completed (checkmark), Step 2 still active
        assert "✓" in rendered
        assert "Step 1" in rendered

    def test_progress_task_not_auto_completed(self) -> None:
        """A task with a total (progress bar) is not marked done by update(completed=50)."""
        display = self._make_display()
        with display:
            t = display.add_task("Analyzing...", total=100)
            display.update(t, completed=50)
            rendered = _render_text(display.render())
        # Should show progress (50%), not a checkmark for this task
        assert "50%" in rendered

    def test_update_description_changes_rendered_text(self) -> None:
        display = self._make_display()
        with display:
            t = display.add_task("Phase 1", total=100)
            display.update(t, description="Phase 2")
            rendered = _render_text(display.render())
        assert "Phase 2" in rendered

    def test_exit_completes_remaining_task(self) -> None:
        """Exiting the context manager completes any active spinner."""
        display = self._make_display()
        with display:
            display.add_task("Running...", total=None)
        # After exit, final render should show checkmark
        rendered = _render_text(display.render_final())
        assert "✓" in rendered
        assert "Running..." in rendered

    def test_multiple_completed_tasks_accumulate(self) -> None:
        display = self._make_display()
        with display:
            t1 = display.add_task("Step 1", total=None)
            display.update(t1, completed=True)
            t2 = display.add_task("Step 2", total=None)
            display.update(t2, completed=True)
        rendered = _render_text(display.render_final())
        assert rendered.count("✓") >= 2


# ---------------------------------------------------------------------------
# LiveDisplay — log lines (verified through rendered output)
# ---------------------------------------------------------------------------


class TestLiveDisplayLogLines:
    def test_add_log_appears_in_render(self) -> None:
        display = LiveDisplay(console=Console(force_terminal=False))
        with display:
            display.add_task("Working...", total=None)
            display.add_log("First message")
            display.add_log("Second message")
            rendered = _render_text(display.render())
        assert "First message" in rendered
        assert "Second message" in rendered

    def test_log_lines_capped_at_max(self) -> None:
        """Only the last MAX_LOG_LINES (5) are shown."""
        display = LiveDisplay(console=Console(force_terminal=False))
        with display:
            display.add_task("Working...", total=None)
            for i in range(20):
                display.add_log(f"Line {i}")
            rendered = _render_text(display.render())
        # Oldest lines should be gone, newest should remain
        assert "Line 0" not in rendered
        assert "Line 19" in rendered

    def test_log_lines_prefixed_with_pipe(self) -> None:
        """Log lines are displayed with '│' prefix."""
        display = LiveDisplay(console=Console(force_terminal=False))
        with display:
            display.add_task("Working...", total=None)
            display.add_log("test log")
            rendered = _render_text(display.render())
        assert "│" in rendered

    def test_print_message_appears_in_render(self) -> None:
        display = LiveDisplay(console=Console(force_terminal=False))
        with display:
            display.print_message("[green]✓[/green] Custom message")
        rendered = _render_text(display.render_final())
        assert "Custom message" in rendered

    def test_final_render_excludes_log_lines(self) -> None:
        """render_final() shows only completed lines, not log lines."""
        display = LiveDisplay(console=Console(force_terminal=False))
        with display:
            t = display.add_task("Step 1", total=None)
            display.add_log("transient log")
            display.update(t, completed=True)
        rendered = _render_text(display.render_final())
        assert "Step 1" in rendered
        assert "transient log" not in rendered


# ---------------------------------------------------------------------------
# LiveDisplay — stop method
# ---------------------------------------------------------------------------


class TestLiveDisplayStop:
    def test_stop_clears_active_display(self) -> None:
        display = LiveDisplay(console=Console(force_terminal=False))
        with display:
            assert get_active_display() is display
            display.stop()
            assert get_active_display() is None

    def test_stop_finalizes_active_task_in_render(self) -> None:
        display = LiveDisplay(console=Console(force_terminal=False))
        with display:
            display.add_task("Running...", total=None)
            display.stop()
            rendered = _render_text(display.render_final())
        assert "✓" in rendered
        assert "Running..." in rendered


# ---------------------------------------------------------------------------
# LiveDisplay — context manager sets/clears active display
# ---------------------------------------------------------------------------


class TestLiveDisplayActiveDisplay:
    def test_enter_sets_active_display(self) -> None:
        display = LiveDisplay(console=Console(force_terminal=False))
        with display:
            assert get_active_display() is display
        assert get_active_display() is None

    def test_print_helpers_route_through_display(self) -> None:
        """When active, print_success/error/info go through the display."""
        display = LiveDisplay(console=Console(force_terminal=False))
        with display:
            print_success("Good")
            print_error("Bad")
            print_info("Info")
        rendered = _render_text(display.render_final())
        assert "Good" in rendered
        assert "Bad" in rendered
        assert "Info" in rendered

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
        # Verify the message appears in the display's rendered output
        rendered = _render_text(display.render())
        assert "Hello from handler" in rendered


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
        """Logging.info() messages appear in the LiveDisplay during install."""
        configure_logging(fmt="text", level="INFO")
        display = LiveDisplay(console=Console(force_terminal=False))
        original = install_live_handler(display)

        try:
            logger = logging.getLogger("test.install")
            logger.info("Routed message")
            rendered = _render_text(display.render())
            assert "Routed message" in rendered
        finally:
            restore_handlers(original)


# ---------------------------------------------------------------------------
# configure_logging — file handler (dual stdout + file)
# ---------------------------------------------------------------------------


class TestConfigureLoggingFile:
    def test_log_file_creates_both_handlers(self) -> None:
        """When log_file is set, both stdout and file handlers exist."""
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            log_path = f.name

        try:
            configure_logging(fmt="text", level="INFO", log_file=log_path)
            root = logging.getLogger()
            file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
            stream_handlers = [
                h
                for h in root.handlers
                if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
            ]
            assert len(file_handlers) == 1
            assert len(stream_handlers) == 1
        finally:
            Path(log_path).unlink(missing_ok=True)
            configure_logging(fmt="text", level="INFO")

    def test_log_file_receives_messages(self) -> None:
        """Log messages are written to the file."""
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            log_path = f.name

        try:
            configure_logging(fmt="text", level="INFO", log_file=log_path)
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

    def test_dual_output_during_live_display(self) -> None:
        """File handler continues to receive logs even when LiveDisplay is active."""
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            log_path = f.name

        try:
            configure_logging(fmt="text", level="INFO", log_file=log_path)
            display = LiveDisplay(console=Console(force_terminal=False))

            with display:
                logger = logging.getLogger("test.dual")
                logger.info("Dual output message")

                # Should appear in LiveDisplay render
                rendered = _render_text(display.render())
                assert "Dual output message" in rendered

            # Should also appear in file
            content = Path(log_path).read_text()
            assert "Dual output message" in content
        finally:
            Path(log_path).unlink(missing_ok=True)
            configure_logging(fmt="text", level="INFO")
