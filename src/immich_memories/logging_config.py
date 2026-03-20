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
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import traceback
from datetime import UTC, datetime

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
        record.run_id = _current_run_id.get() or "-"  # type: ignore[attr-defined]
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
            log_entry["exception"] = traceback.format_exception(*record.exc_info)

        return json.dumps(log_entry, default=str)


TEXT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s [%(run_id)s]: %(message)s"


def configure_logging(
    fmt: str | None = None,
    level: str = "INFO",
) -> None:
    """Configure the root logger with the specified format.

    Args:
        fmt: Log format - "text" for human-readable, "json" for structured JSON.
            If None, reads from IMMICH_MEMORIES_LOG_FORMAT env var (default: "text").
        level: Log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    """
    if fmt is None:
        fmt = os.environ.get("IMMICH_MEMORIES_LOG_FORMAT", "text").lower()

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicates
    for handler in root.handlers.copy():
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RunIdFilter())

    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(TEXT_FORMAT))

    root.addHandler(handler)
