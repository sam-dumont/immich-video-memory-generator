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
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Container

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


def record_for(entry: DiscoveredDay) -> dict:
    """One scan result as the catalogue stores it.

    The write side of `_entry_from_record`, here so the two cannot drift. A day
    nobody could judge is stored under `unjudged` rather than `day`: every
    reader of this file goes by `day`, so an unreadable day is recorded without
    ever being offered as a memory.
    """
    stamp = {"prompt_version": entry.prompt_version, "app_version": entry.app_version}
    if not entry.judged:
        return {"unjudged": entry.day.isoformat(), "photos": entry.photos} | stamp
    return {
        "day": entry.day.isoformat(),
        "title": entry.title,
        "subtitle": entry.subtitle,
        "what": entry.what,
        "photos": entry.photos,
        "window": [when.isoformat() for when in entry.window] if entry.window else None,
        "active_hours": entry.active_hours,
        "run_start": entry.run_start.isoformat() if entry.run_start else None,
        "run_end": entry.run_end.isoformat() if entry.run_end else None,
        "window_photos": entry.window_photos,
    } | stamp


def scope_window(entry: DiscoveredDay) -> tuple[datetime, datetime] | None:
    """The window a film of this day should actually be cut from.

    A recorded window is the scope, and that is the point of it: the memory
    starts at the circuit rather than at the cat on the balcony that morning.
    But a window that holds almost none of the day hides the day, and one real
    catalogue held two of those — a five-hour window over 24 of a 379-picture
    day, and forty minutes of a twelve-hour one. Both were cut from a sliver
    and one was refused outright for want of material.

    The count the scan now records is what decides it. Rows written before
    #1067 have none and are taken as written rather than second-guessed;
    `discover-days --replace` is what repairs those.
    """
    from immich_memories.analysis.special_day import window_holds_enough

    if entry.window is None or not entry.window_photos or not entry.photos:
        return entry.window
    return entry.window if window_holds_enough(entry.window_photos, entry.photos) else None


# How long a stretch of photography may run and still be one occasion. A run
# is bounded by five hours of photographic silence and by nothing else, which
# is an honest boundary but not a short one: a library that never quite stops
# shooting could chain date onto date into a single run. Measured on a real
# catalogue of twenty-one judged days, twenty runs are under fifteen hours, and a
# long occasion measured at 42.5 hours sets the cap; the module that finds runs
# describes a labelled 45-hour one. Two full days is the first round number
# above both, and past it the run is describing a habit rather than an
# occasion — so the scope refuses to extend and keeps today's calendar date
# instead of swallowing a week.
_LONGEST_RUN_HOURS = 48

SCOPE_FROM_WINDOW = "the window the catalogue recorded for this day, trimmed inside its run"
SCOPE_FROM_RUN = "the run of photographs the day left, not the calendar date it began on"
SCOPE_FROM_DATE = "the calendar date: the catalogue recorded no run for this day"
SCOPE_UNCATALOGUED = "the calendar date: this day is not in the catalogue"
SCOPE_RUN_TOO_LONG = (
    "the calendar date: the recorded run is longer than any one occasion, so it was not used"
)
SCOPE_RUN_MEETS_ANOTHER_DAY = (
    "the calendar date: the recorded run reaches into another catalogued day, so it was not used"
)


@dataclass(frozen=True)
class SpecialDayScope:
    """The bounds a film of one catalogued day is cut from, and why those.

    Both bounds travel, narrowest first, because the window is a trim *inside*
    the run and the layer that builds the fetch range needs to know what it is
    trimming. `origin` is the sentence the run record carries, so a reader can
    tell a 42-hour occasion from a five-hour slice of one without re-deriving
    the decision.
    """

    window: tuple[datetime, datetime] | None
    run: tuple[datetime, datetime] | None
    origin: str


def scope_of(entry: DiscoveredDay, *, other_days: Container[date] = frozenset()) -> SpecialDayScope:
    """Which bounds this day should be filmed between, and the reason for them.

    The run is the outer scope: an occasion is a stretch of photography, and
    the calendar cuts the long ones in half. A window the scan recorded is a
    trim inside that run and still wins when it holds enough of it — shrink,
    don't pad.

    `other_days` are the dates the rest of the catalogue already claims. A run
    is bounded by its own first and last picture rather than by whole dates, so
    two occasions sharing a date cannot in practice reach into each other; the
    check is here because a catalogue is a file people merge and hand-edit, and
    two occasions must not silently become one.
    """
    window = scope_window(entry)
    run, refusal = _run_to_film(entry, other_days)
    if window is not None:
        return SpecialDayScope(window=window, run=run, origin=SCOPE_FROM_WINDOW)
    return SpecialDayScope(window=None, run=run, origin=refusal or SCOPE_FROM_RUN)


def _run_to_film(
    entry: DiscoveredDay, other_days: Container[date]
) -> tuple[tuple[datetime, datetime] | None, str | None]:
    """The recorded run when it can be used as a scope, or nothing and why not."""
    if entry.run_start is None or entry.run_end is None:
        return None, SCOPE_FROM_DATE
    if hours_awake(entry) > _LONGEST_RUN_HOURS:
        return None, SCOPE_RUN_TOO_LONG
    if any(day in other_days for day in _dates_after_the_first(entry.run_start, entry.run_end)):
        return None, SCOPE_RUN_MEETS_ANOTHER_DAY
    return (entry.run_start, entry.run_end), None


def _dates_after_the_first(start: datetime, end: datetime) -> list[date]:
    """The extra dates a run reaches into beyond the one it began on."""
    span = (end.date() - start.date()).days
    return [start.date() + timedelta(days=n) for n in range(1, span + 1)]


def judged_by_this_build(entry: DiscoveredDay) -> bool:
    """Whether this build's scan would ask the same question of this day again.

    Rows written before #1065 carry no stamp at all, and the question they were
    judged against let a pleasant afternoon at home into one real catalogue.
    They read as stale, which is the only honest reading of an unstamped row.
    """
    from immich_memories.analysis.special_day_sequence import SCAN_VERSION

    return entry.prompt_version == SCAN_VERSION


def rows_outside(rows: list[dict], since: int, until: int) -> tuple[list[dict], int]:
    """The catalogue with a period lifted out of it, and how many rows that was.

    Everything the period covers goes: judged days, unjudged days and the year
    markers that would otherwise make a resumed scan skip the very years it was
    asked to redo. Rows outside are untouched, including the canonical event
    records, which no scan writes.
    """
    kept = [row for row in rows if not _inside(row, since, until)]
    return kept, len(rows) - len(kept)


def _inside(row: object, since: int, until: int) -> bool:
    if not isinstance(row, dict):
        return False
    scanned = row.get("scanned")
    if isinstance(scanned, int):
        return since <= scanned <= until
    written = row.get("day") or row.get("unjudged")
    if not isinstance(written, str):
        return False
    try:
        return since <= date.fromisoformat(written).year <= until
    except ValueError:
        return False


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
        window_photos=raw.get("window_photos", 0),
        prompt_version=raw.get("prompt_version", ""),
        app_version=raw.get("app_version", ""),
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
