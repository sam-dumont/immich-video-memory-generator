"""Job executor — resolves schedule entries into generation parameters.

Auto-fills date parameters based on fire time:
- year_in_review: generates for the previous year
- monthly_highlights: generates for the previous month
- on_this_day: uses the fire date
- season: generates for the current/most recent season
- Others: uses the fire time's year
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from immich_memories.scheduling.models import ScheduleEntry

logger = logging.getLogger(__name__)


def resolve_schedule_params(entry: ScheduleEntry, fire_time: datetime) -> dict[str, Any]:
    """Resolve a schedule entry + fire time into params for preset creation.

    Auto-fills year/month/target_date based on memory_type and fire_time,
    then merges with explicit params (explicit wins).
    """
    auto: dict[str, Any] = {"memory_type": entry.memory_type}

    if entry.person_names:
        auto["person_names"] = list(entry.person_names)

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

    elif mt == "on_this_day":
        auto["target_date"] = fire_time.date()

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
