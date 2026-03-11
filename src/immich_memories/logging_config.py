"""Logging configuration with text and JSON output formats.

Supports two modes:
- "text" (default): Human-readable format for development.
- "json": Structured JSON format for production/observability.

The format can be selected via:
- Environment variable: IMMICH_MEMORIES_LOG_FORMAT=json
- Calling configure_logging(fmt="json") directly.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as JSON.

        Args:
            record: The log record to format.

        Returns:
            Single-line JSON string.
        """
        log_entry: dict = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info and record.exc_info[1] is not None:
            log_entry["exception"] = traceback.format_exception(*record.exc_info)

        return json.dumps(log_entry, default=str)


TEXT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


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
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)

    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(TEXT_FORMAT))

    root.addHandler(handler)
