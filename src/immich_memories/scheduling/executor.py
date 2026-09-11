"""Job executor — resolves schedule entries into generation parameters.

Auto-fills date parameters based on fire time:
- year_in_review: generates for the previous year
- monthly_highlights: generates for the previous month
- on_this_day: no date param — the run covers the day it fires
- album: no date param — --from-album is the whole scope
- season: generates for the current/most recent season
- Others: uses the fire time's year

Every param resolved here has to be expressible as a `generate` option, or the
schedule means something the run cannot carry out.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from immich_memories.scheduling.models import ScheduleEntry

logger = logging.getLogger(__name__)


def resolve_schedule_params(entry: ScheduleEntry, fire_time: datetime) -> dict[str, Any]:
    """Resolve a schedule entry + fire time into params for preset creation.

    Auto-fills year/month based on memory_type and fire_time, then merges with
    explicit params (explicit wins).
    """
    auto: dict[str, Any] = {"memory_type": entry.memory_type}

    if entry.person_names:
        auto["person_names"] = entry.person_names.copy()

    if entry.duration_minutes:
        auto["duration_minutes"] = entry.duration_minutes

    if entry.upload_to_immich:
        auto["upload_to_immich"] = True

    if entry.album_name:
        auto["album_name"] = entry.album_name

    # Auto-resolve date params based on memory type
    mt = entry.memory_type

    if mt == "year_in_review":
        # Fire in January → generate for previous year
        auto["year"] = fire_time.year - 1

    elif mt == "monthly_highlights":
        # Fire on 1st → generate for previous month
        if fire_time.month == 1:
            auto["year"] = fire_time.year - 1
            auto["month"] = 12
        else:
            auto["year"] = fire_time.year
            auto["month"] = fire_time.month - 1

    elif mt in ("on_this_day", "album"):
        # Neither names a date the schedule could fill in. on_this_day covers
        # the day it fires: `generate` has no option naming another one, and
        # the child's own date is the day the library is living in. An album
        # is its own scope, and `generate` refuses date flags beside it.
        pass

    elif mt == "season":
        auto["year"] = fire_time.year

    elif mt == "trip":
        # Trips: scan previous year (same as year_in_review)
        auto["year"] = fire_time.year - 1

    else:
        # Default: use fire time's year
        auto["year"] = fire_time.year

    # Explicit params override auto-resolved
    auto.update(entry.params)

    return auto
