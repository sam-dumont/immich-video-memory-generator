"""Interactive CLI display with spinner, progress bar, and live log lines."""

from __future__ import annotations

import io
import logging
import os
import sys
import threading
import time
from collections import deque
from types import TracebackType
from typing import Any, Protocol, Self, runtime_checkable

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.progress import BarColumn, SpinnerColumn, TaskID, TaskProgressColumn, TextColumn
from rich.progress import Progress as RichProgress
from rich.text import Text

from immich_memories.cli._helpers import set_active_display
from immich_memories.logging_config import install_live_handler, restore_handlers


@runtime_checkable
class ProgressDisplay(Protocol):
    """Minimal protocol matching both LiveDisplay and rich.progress.Progress."""

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: TracebackType | None,
    ) -> None: ...

    def add_task(
        self,
        description: str,
        start: bool = ...,
        total: float | None = ...,
        completed: int = ...,
        visible: bool = ...,
        **fields: Any,
    ) -> TaskID: ...

    def update(self, task_id: TaskID, **kwargs: Any) -> None: ...

    def stop(self) -> None: ...


# Sentinel for "no value passed"
_MISSING: object = object()

MAX_LOG_LINES = 5
MAX_VISIBLE_COMPLETED = 20


class LiveDisplay:
    """Interactive display combining spinner/bar, completed steps, and live logs.

    Usage::

        with LiveDisplay(console=console) as display:
            task = display.add_task("Connecting...", total=None)
            # ... work ...
            display.update(task, completed=True)
            # Shows: ✓ Connecting...

            task2 = display.add_task("Analyzing clips...", total=100)
            display.update(task2, completed=50)
            # Shows progress bar at 50%

            display.add_log("Clustered 47 clips into 46 groups")
            # Shows log line in dim panel below
    """

    def __init__(self, console: Console) -> None:
        self._console = console
        self._live: Live | None = None
        self._completed_lines: list[str] = []
        self._log_lines: deque[str] = deque(maxlen=MAX_LOG_LINES)
        self._lock = threading.Lock()
        self._original_handlers: list[logging.Handler] | None = None

        # Internal progress bar (used for rendering, not shown directly)
        self._progress = RichProgress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        )
        self._tasks: dict[int, _TaskState] = {}
        self._active_task_id: int | None = None
        self._start_time: float | None = None

    def __enter__(self) -> LiveDisplay:
        self._start_time = time.monotonic()
        self._progress.start()
        self._live = Live(
            self.render(),
            console=self._console,
            refresh_per_second=8,
            transient=False,
        )
        self._live.__enter__()
        # WHY: Route logging through the live display so log lines
        # appear in the scrolling panel instead of breaking Rich's output
        self._original_handlers = install_live_handler(self)
        # WHY: Redirect raw stdout/stderr at the OS level so C libraries
        # (Taichi banner, FFmpeg warnings) can't corrupt Rich's display.
        # Python's sys.stdout is left intact — Rich uses it for rendering.
        self._stdout_capture = _FDCapture(1, self)  # stdout
        self._stderr_capture = _FDCapture(2, self)  # stderr
        set_active_display(self)
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: TracebackType | None,
    ) -> None:
        # Restore FD captures before anything else
        if hasattr(self, "_stderr_capture"):
            self._stderr_capture.restore()
        if hasattr(self, "_stdout_capture"):
            self._stdout_capture.restore()
        # Restore original logging and console routing before teardown
        set_active_display(None)
        if self._original_handlers is not None:
            restore_handlers(self._original_handlers)
            self._original_handlers = None

        # Complete any remaining active task
        if self._active_task_id is not None:
            state = self._tasks.get(self._active_task_id)
            if state and not state.done:
                self._finish_task(self._active_task_id)

        self._progress.stop()
        if self._live:
            # Final render showing all completed lines
            self._live.update(self.render_final())
            self._live.__exit__(None, None, None)
            self._live = None

    def add_task(
        self,
        description: str,
        start: bool = True,
        total: float | None = None,
        completed: int = 0,
        visible: bool = True,
        **fields: Any,
    ) -> TaskID:
        """Start a new task, auto-completing any prior spinner-only task."""
        with self._lock:
            # Auto-complete previous indeterminate (spinner) task
            if self._active_task_id is not None:
                prev = self._tasks.get(self._active_task_id)
                if prev and not prev.done:
                    self._finish_task(self._active_task_id)

            # Create task in the internal Progress widget
            task_id = self._progress.add_task(
                description,
                start=start,
                total=total,
                completed=completed,
                visible=visible,
                **fields,
            )
            self._tasks[int(task_id)] = _TaskState(
                description=description,
                total=total,
                done=False,
            )
            self._active_task_id = int(task_id)
            self._refresh()
            return task_id

    def update(self, task_id: TaskID, **kwargs: Any) -> None:
        """Update task progress or description."""
        state = self._tasks.get(int(task_id))
        if state is None:
            return

        with self._lock:
            completed = kwargs.get("completed", _MISSING)
            description = kwargs.get("description", _MISSING)

            if description is not _MISSING:
                state.description = str(description)

            # completed=True on an indeterminate task means "done"
            if completed is True and state.total is None:
                self._finish_task(int(task_id))
            elif completed is not _MISSING and completed is not True:
                self._progress.update(task_id, **kwargs)
            elif description is not _MISSING:
                self._progress.update(task_id, description=state.description)

            self._refresh()

    def add_log(self, message: str) -> None:
        """Add a log line to the scrolling panel beneath the active task."""
        with self._lock:
            self._log_lines.append(message)
            self._refresh()

    def print_message(self, message: str) -> None:
        """Print a rich-formatted message as a completed line."""
        with self._lock:
            self._completed_lines.append(message)
            self._refresh()

    def stop(self) -> None:
        """Stop the live display so raw console output can be used.

        Called by trip detection flow before printing interactive tables.
        The display can't be resumed after this — use __exit__ instead.
        """
        # Restore FD captures first
        if hasattr(self, "_stderr_capture"):
            self._stderr_capture.restore()
        if hasattr(self, "_stdout_capture"):
            self._stdout_capture.restore()
        if self._active_task_id is not None:
            state = self._tasks.get(self._active_task_id)
            if state and not state.done:
                self._finish_task(self._active_task_id)

        self._progress.stop()
        if self._live:
            self._live.update(self.render_final())
            self._live.stop()

        # Restore logging so subsequent console output works normally
        set_active_display(None)
        if self._original_handlers is not None:
            restore_handlers(self._original_handlers)
            self._original_handlers = None

    def _finish_task(self, task_id: int) -> None:
        """Mark task as done and add a checkmark line."""
        state = self._tasks.get(task_id)
        if state is None or state.done:
            return
        state.done = True
        self._completed_lines.append(f"[green]✓[/green] {state.description}")
        self._progress.update(TaskID(task_id), visible=False)
        if self._active_task_id == task_id:
            self._active_task_id = None

    def _refresh(self) -> None:
        if self._live:
            self._live.update(self.render())

    def render(self) -> RenderableType:
        """Build the composite renderable for the current display state."""
        parts: list[RenderableType] = []

        # Cap visible completed lines to prevent terminal overflow
        total = len(self._completed_lines)
        if total > MAX_VISIBLE_COMPLETED:
            hidden = total - MAX_VISIBLE_COMPLETED
            parts.append(Text(f"  ({hidden} earlier steps hidden)", style="dim"))
            visible = self._completed_lines[-MAX_VISIBLE_COMPLETED:]
        else:
            visible = self._completed_lines

        parts.extend(Text.from_markup(line) for line in visible)

        # Active progress bar (spinner or bar) + elapsed time
        if self._active_task_id is not None:
            state = self._tasks.get(self._active_task_id)
            if state and not state.done:
                parts.append(self._progress)
                time_line = self._build_time_line(state)
                if time_line:
                    parts.append(time_line)

        # Log lines panel
        parts.extend(Text(f"  │ {log_line}", style="dim") for log_line in self._log_lines)

        if not parts:
            return Text("")

        return Group(*parts)

    def _build_time_line(self, state: _TaskState) -> Text | None:
        if self._start_time is None:
            return None
        elapsed = time.monotonic() - self._start_time
        time_text = f"  ⏱ {_format_duration(elapsed)} elapsed"
        if state.total is not None and self._active_task_id is not None:
            rich_task = self._progress.tasks[self._active_task_id]
            pct = rich_task.percentage
            if pct and pct > 5:
                remaining = elapsed * (100 - pct) / pct
                time_text += f", ~{_format_duration(remaining)} remaining"
        return Text(time_text, style="dim")

    def render_final(self) -> RenderableType:
        """Render final state: just completed lines, no spinner or logs."""
        parts = [Text.from_markup(line) for line in self._completed_lines]
        return Group(*parts) if parts else Text("")


def _format_duration(seconds: float) -> str:
    """Format seconds as M:SS or H:MM:SS."""
    s = int(seconds)
    if s < 3600:
        return f"{s // 60}:{s % 60:02d}"
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


class _FDCapture:
    """Redirect a raw OS file descriptor into a LiveDisplay log panel.

    WHY: Libraries like Taichi print via C-level stdout/stderr, bypassing
    Python's sys.stdout entirely. Rich Live can't coordinate with these
    writes, so they corrupt the cursor-controlled display. This class
    redirects the OS-level FD into a pipe that a background thread reads
    and routes into LiveDisplay.add_log().
    """

    def __init__(self, fd: int, display: LiveDisplay) -> None:
        self._fd = fd
        self._display = display
        self._restored = False

        # Save the original FD and replace it with a pipe
        self._saved_fd = os.dup(fd)
        self._read_fd, self._write_fd = os.pipe()
        os.dup2(self._write_fd, fd)
        os.close(self._write_fd)

        # Also redirect Python's sys.stdout/stderr to use our pipe
        # WHY: Some Python code writes via sys.stdout.write() which uses
        # the Python-level file object, not the raw FD
        if fd == 1:
            self._orig_stream = sys.stdout
            sys.stdout = io.TextIOWrapper(
                os.fdopen(os.dup(self._saved_fd), "wb"), write_through=True
            )
        elif fd == 2:
            self._orig_stream = sys.stderr
            sys.stderr = io.TextIOWrapper(
                os.fdopen(os.dup(self._saved_fd), "wb"), write_through=True
            )
        else:
            self._orig_stream = None

        # Background thread reads from the pipe and routes to display
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def _drain(self) -> None:
        with os.fdopen(self._read_fd, "r", errors="replace") as f:
            for line in f:
                stripped = line.rstrip()
                if stripped:
                    self._display.add_log(stripped)

    def restore(self) -> None:
        if self._restored:
            return
        self._restored = True
        # Restore the original FD
        os.dup2(self._saved_fd, self._fd)
        os.close(self._saved_fd)
        # Restore Python stream
        if self._fd == 1 and self._orig_stream is not None:
            sys.stdout = self._orig_stream
        elif self._fd == 2 and self._orig_stream is not None:
            sys.stderr = self._orig_stream


class _TaskState:
    """Internal state for a tracked task."""

    __slots__ = ("description", "total", "done")

    def __init__(self, description: str, total: float | None, done: bool) -> None:
        self.description = description
        self.total = total
        self.done = done
