"""Reading the catalogue `discover-days` writes.

More than one reader wants this file -- `days-due` prints from it and the
wizard's Surprise me card offers from it -- and they have to agree on where it
lives and on what a half-written entry means, so the reading happens once here.

Legacy entries tolerate missing display fields. Exact event membership is a
source boundary: incomplete or inconsistent event records must not widen to a day.
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
    """Read legacy days and canonical events without discarding event identity."""
    entries = []
    for raw in load_catalogue(path):
        if not isinstance(raw, dict):
            continue
        entry = _entry_from_record(raw, path)
        if entry is not None:
            entries.append(entry)
    return entries


def _event_run(
    raw: dict, start: datetime | None, end: datetime | None
) -> tuple[datetime, datetime, tuple[datetime, datetime]]:
    start = start or _moment_in(raw.get("run_start"))
    end = end or _moment_in(raw.get("run_end"))
    if start is None or end is None or start > end:
        raise ValueError("canonical special event needs its exact start and end")
    return start, end, (start, end)


def _entry_from_record(raw: dict, path: Path) -> DiscoveredDay | None:
    """One catalogue record; a legacy day without any date is skipped, not an error."""
    from immich_memories.analysis.special_event_scope import (
        SpecialEventAdmission,
        validate_special_event_scope,
    )
    from immich_memories.automation.special_day_scan import DiscoveredDay

    event_id = raw.get("event_id")
    members = validate_special_event_scope(event_id, raw.get("asset_ids", ()))
    start, end = _moment_in(raw.get("start")), _moment_in(raw.get("end"))
    window = _window_in(raw.get("window"))
    if event_id is not None:
        start, end, window = _event_run(raw, start, end)
    day = date.fromisoformat(raw["day"]) if raw.get("day") else (start.date() if start else None)
    if day is None:
        return None
    if event_id is not None and start is not None and day != start.date():
        raise ValueError("canonical special event day must match its start")
    return DiscoveredDay(
        day=day,
        title=raw.get("title", ""),
        subtitle=raw.get("subtitle", ""),
        what=raw.get("what", ""),
        photos=len(members) if event_id else raw.get("photos", 0),
        window=window,
        active_hours=raw.get("active_hours", 0),
        run_start=start if event_id else _moment_in(raw.get("run_start")),
        run_end=end if event_id else _moment_in(raw.get("run_end")),
        event_id=event_id,
        asset_ids=members,
        event_admission=SpecialEventAdmission.from_catalogue_record(raw, evidence_ref=str(path))
        if event_id is not None
        else None,
    )


def hours_awake(entry: DiscoveredDay) -> float:
    """How long the day the catalogue found actually lasted.

    `active_hours` counts distinct hours touched, so it saturates at 24 -- and
    a run keyed by the date it began can span 45 hours. Where the extent was
    recorded it is the only reading that survives midnight.
    """
    if entry.run_start is not None and entry.run_end is not None:
        return (entry.run_end - entry.run_start).total_seconds() / 3600.0
    return float(entry.active_hours)


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
