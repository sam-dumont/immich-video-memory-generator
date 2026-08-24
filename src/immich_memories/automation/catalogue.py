"""Reading the catalogue `discover-days` writes.

More than one reader wants this file -- `days-due` prints from it and the
wizard's Surprise me card offers from it -- and they have to agree on where it
lives and on what a half-written entry means, so the reading happens once here.

Nothing raises. A scan of twenty years is not something to ask anybody to run
again for a field they can live without, so every key falls back and an
unreadable file reads as an empty catalogue.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from immich_memories.automation.special_day_scan import DiscoveredDay

logger = logging.getLogger(__name__)


def default_catalogue_path() -> Path:
    """Where the catalogue lives when nobody said otherwise.

    Resolved per call rather than at import: the home directory is the one
    thing a test, a container, or a service account changes underneath us.
    """
    return Path.home() / ".immich-memories" / "special-days.json"


def load_catalogue(path: Path) -> list[dict]:
    """What an earlier run already found, or nothing readable.

    A scan runs for hours across twenty years. Starting from scratch every
    time is the difference between a command you can interrupt and one you
    have to babysit.
    """
    if not path.exists():
        return []
    try:
        loaded = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("%s is not readable as a catalogue; starting fresh", path)
        return []
    return loaded if isinstance(loaded, list) else []


def entries_from(path: Path) -> list[DiscoveredDay]:
    """The catalogue as discovered days, skipping anything without a date."""
    from immich_memories.automation.special_day_scan import DiscoveredDay

    return [
        DiscoveredDay(
            day=date.fromisoformat(raw["day"]),
            title=raw.get("title", ""),
            subtitle=raw.get("subtitle", ""),
            what=raw.get("what", ""),
            photos=raw.get("photos", 0),
            window=_window_in(raw.get("window")),
            active_hours=raw.get("active_hours", 0),
            run_start=_moment_in(raw.get("run_start")),
            run_end=_moment_in(raw.get("run_end")),
        )
        for raw in load_catalogue(path)
        if raw.get("day")
    ]


def _moment_in(raw: object) -> datetime | None:
    """One end of the run a catalogue entry recorded, if it recorded any."""
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        logger.warning("Ignoring an unreadable time in the catalogue: %r", raw)
        return None


def _window_in(raw: object) -> tuple[datetime, datetime] | None:
    """The hours a catalogue entry recorded for its event, if it recorded any.

    Entries written before the scan looked for one have no window, and a scan
    of twenty years is not something to ask anybody to run again.
    """
    if not isinstance(raw, list) or len(raw) != 2:
        return None
    try:
        return (datetime.fromisoformat(raw[0]), datetime.fromisoformat(raw[1]))
    except (TypeError, ValueError):
        logger.warning("Ignoring an unreadable window in the catalogue: %r", raw)
        return None
