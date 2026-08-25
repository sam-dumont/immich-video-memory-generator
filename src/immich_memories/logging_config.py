"""Logging configuration with text and JSON output formats.

Supports two modes:
- "text" (default): Human-readable format for development.
- "json": Structured JSON format for production/observability.

The format can be selected via:
- Environment variable: IMMICH_MEMORIES_LOG_FORMAT=json
- Calling configure_logging(fmt="json") directly.

run_id injection:
- Call set_current_run_id() at pipeline start to tag all subsequent log lines.
- Enables: jq 'select(.run_id=="...")' for JSON logs.

Secret redaction:
- Call install_secret_redaction() at config load with the configured credentials;
  SecretRedactionFilter then strips those values from every record.
- Config load is the earliest the values are known, so the lines printed before
  it -- startup, a config file that fails to parse -- are unredacted.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import traceback
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Protocol

# Context variable holding the current pipeline run_id.
# WHY: contextvars over threading.local — works with asyncio and threads.
_current_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_run_id", default=None
)


def set_current_run_id(run_id: str | None) -> None:
    """Set the run_id for all subsequent log messages in this context."""
    _current_run_id.set(run_id)


def get_current_run_id() -> str | None:
    """Get the current run_id, or None if not in a pipeline run."""
    return _current_run_id.get()


class RunIdFilter(logging.Filter):
    """Inject run_id from contextvars into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _current_run_id.get() or "-"
        return True


REDACTED = "[redacted]"

# A secret shorter than this is not worth hiding: substring-replacing "abc"
# would shred every innocent line that happens to contain it, and the damage
# would outlast the leak it prevented.
MIN_REDACTABLE_SECRET_LENGTH = 8

# Secret VALUES to strip from log records. Populated at config load, because
# that is the first moment the values are known.
_secret_values: tuple[str, ...] = ()


def install_secret_redaction(values: Iterable[str]) -> None:
    """Register secret values to strip from every subsequent log record.

    Accumulates rather than replaces, so reloading a config never un-redacts a
    secret that earlier code may already have captured. Values shorter than
    MIN_REDACTABLE_SECRET_LENGTH and empty values are ignored.

    Log lines emitted before this is called -- startup, config parse errors --
    are unredacted by construction: nothing knows the secrets yet.
    """
    global _secret_values
    keep = {v for v in values if isinstance(v, str) and len(v) >= MIN_REDACTABLE_SECRET_LENGTH}
    # WHY: longest first -- when one secret contains another (an api key inside
    # a notification URL), replacing the short one first would leave the rest of
    # the long one in the clear.
    _secret_values = tuple(sorted(keep | set(_secret_values), key=len, reverse=True))


def _redact(text: str) -> str:
    for secret in _secret_values:
        text = text.replace(secret, REDACTED)
    return text


class SecretRedactionFilter(logging.Filter):
    """Strip registered secret values out of a record's message and traceback."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not _secret_values:
            return True

        # WHY: collapse msg % args into one string before redacting. A secret
        # can arrive as a non-str arg (an exception object, a URL wrapper) whose
        # value only surfaces once %-formatting has run, so redacting msg and
        # args separately would miss it. Only a record that actually carried a
        # secret is rewritten; getMessage() skips %-formatting when args is
        # None, so the substituted text is safe to re-emit verbatim.
        message = record.getMessage()
        redacted = _redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = None

        if record.exc_text:
            record.exc_text = _redact(record.exc_text)
        elif record.exc_info and record.exc_info[1] is not None:
            # WHY: formatters render the traceback from exc_info, out of a
            # filter's reach. Pre-rendering it into exc_text -- the cache
            # logging.Formatter prefers -- is the only place redaction can land.
            record.exc_text = _redact("".join(traceback.format_exception(*record.exc_info)))

        return True


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include run_id only when a pipeline run is active
        run_id = getattr(record, "run_id", "-")
        if run_id != "-":
            log_entry["run_id"] = run_id

        if record.exc_info and record.exc_info[1] is not None:
            # WHY: exc_text carries SecretRedactionFilter's already-cleaned
            # traceback. Re-rendering from exc_info here would undo it and put
            # the secrets straight back into the JSON.
            text = record.exc_text or "".join(traceback.format_exception(*record.exc_info))
            log_entry["exception"] = text.splitlines(keepends=True)

        return json.dumps(log_entry, default=str)


TEXT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s [%(run_id)s]: %(message)s"
LOG_LINE_FORMAT = "[%(levelname)s] %(message)s"


class _LogSink(Protocol):
    """Minimal interface for routing log lines (avoids circular import)."""

    def add_log(self, message: str) -> None: ...


class LiveDisplayLogHandler(logging.Handler):
    """Routes log records into a LiveDisplay's scrolling log panel.

    WHY: Standard StreamHandler writes raw lines that break Rich's
    cursor-controlled Live display. This handler feeds formatted
    messages through LiveDisplay.add_log() so Rich coordinates all output.
    """

    def __init__(self, display: _LogSink) -> None:
        super().__init__()
        self._display = display
        self.setFormatter(logging.Formatter(LOG_LINE_FORMAT))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._display.add_log(msg)
        except Exception:  # noqa: BLE001
            self.handleError(record)


def install_live_handler(display: _LogSink) -> list[logging.Handler]:
    """Replace stdout/stderr handlers with one that feeds a LiveDisplay.

    File handlers are kept so logs still go to log files (useful for
    containers and debugging). Returns the removed handlers for restore.
    """
    root = logging.getLogger()
    original_handlers = root.handlers.copy()

    # WHY: Remove ALL handlers except FileHandlers. Stream handlers write
    # to stdout/stderr which corrupts Rich Live's cursor-controlled display.
    # We also remove any unknown handler types as a safety measure — during
    # the Live context, ALL log output should go through LiveDisplayLogHandler.
    for h in root.handlers.copy():
        if not isinstance(h, logging.FileHandler):
            root.removeHandler(h)

    handler = LiveDisplayLogHandler(display)
    handler.addFilter(RunIdFilter())
    handler.addFilter(SecretRedactionFilter())
    root.addHandler(handler)

    # WHY: Disable lastResort handler — Python's logging module has a
    # built-in StreamHandler(stderr) that fires when the root logger has
    # no handlers at WARNING+ level. Our LiveDisplayLogHandler handles
    # everything, but lastResort doesn't know that.
    logging.lastResort = None

    return original_handlers


def restore_handlers(original_handlers: list[logging.Handler]) -> None:
    """Restore original logging handlers after LiveDisplay exits.

    Removes the LiveDisplayLogHandler and re-adds the original stream handlers.
    File handlers that were kept during install are left untouched.
    """
    # Restore Python's lastResort handler
    logging.lastResort = logging.StreamHandler(sys.stderr)

    root = logging.getLogger()
    # Remove the LiveDisplayLogHandler
    for h in root.handlers.copy():
        if isinstance(h, LiveDisplayLogHandler):
            root.removeHandler(h)
    # Re-add original stream handlers that were removed
    current = set(root.handlers)
    for h in original_handlers:
        if h not in current:
            root.addHandler(h)


def configure_logging(
    fmt: str | None = None,
    level: str = "INFO",
    log_file: str | None = None,
) -> None:
    """Configure the root logger with the specified format.

    Args:
        fmt: Log format - "text" for human-readable, "json" for structured JSON.
            If None, reads from IMMICH_MEMORIES_LOG_FORMAT env var (default: "text").
        level: Log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_file: Path to a log file. If None, reads from IMMICH_MEMORIES_LOG_FILE
            env var. When set, logs go to both stdout and the file.
    """
    if fmt is None:
        fmt = os.environ.get("IMMICH_MEMORIES_LOG_FORMAT", "text").lower()
    if log_file is None:
        log_file = os.environ.get("IMMICH_MEMORIES_LOG_FILE")

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicates
    for handler in root.handlers.copy():
        root.removeHandler(handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.addFilter(RunIdFilter())
    stream_handler.addFilter(SecretRedactionFilter())

    if fmt == "json":
        stream_handler.setFormatter(JsonFormatter())
    else:
        stream_handler.setFormatter(logging.Formatter(TEXT_FORMAT))

    root.addHandler(stream_handler)

    # WHY: File handler enables persistent logs for containers and debugging.
    # In interactive mode, the stream handler is swapped for LiveDisplay
    # but the file handler stays, so logs are never lost.
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.addFilter(RunIdFilter())
        file_handler.addFilter(SecretRedactionFilter())
        if fmt == "json":
            file_handler.setFormatter(JsonFormatter())
        else:
            file_handler.setFormatter(logging.Formatter(TEXT_FORMAT))
        root.addHandler(file_handler)
