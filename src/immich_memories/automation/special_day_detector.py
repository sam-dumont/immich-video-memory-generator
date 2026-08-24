"""Proposing the days the catalogue already decided were worth something.

The other detectors read the calendar or a bulk statistic. This one reads a
judgement that was made in advance and written down, which is what makes the
result feel unrequested rather than scheduled.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from immich_memories.automation.candidates import (
    CandidateCategory,
    MemoryCandidate,
    make_memory_key,
)
from immich_memories.automation.catalogue import hours_awake
from immich_memories.automation.special_day_scan import anniversaries_due, same_day_in

if TYPE_CHECKING:
    from immich_memories.automation.special_day_scan import DiscoveredDay

# Ten years reads louder than nine, and five louder than seven. The ladder is
# what puts a decade above every other detector and leaves a seventh
# anniversary competing rather than pre-empting.
_DECADE = 1.00
_HALF_DECADE = 0.85
_ORDINARY = 0.60


def _roundness(years: int) -> float:
    if years % 10 == 0:
        return _DECADE
    return _HALF_DECADE if years % 5 == 0 else _ORDINARY


class SpecialDayDetector:
    """Propose a catalogued day when its anniversary comes round."""

    BASE_SCORE = 0.8
    WINDOW_DAYS = 3

    def detect(
        self,
        assets_by_month: dict[str, int],
        people: list[Any],
        generated_keys: set[str],
        config: Any,
        today: date,
        catalogue: list[DiscoveredDay] | None = None,
    ) -> list[MemoryCandidate]:
        """Emit one candidate per catalogued day with an anniversary near today."""
        if not catalogue:
            return []

        candidates: list[MemoryCandidate] = []
        for entry, years in anniversaries_due(catalogue, today, window_days=self.WINDOW_DAYS):
            # Keyed by the anniversary as well as the day, so a day that got
            # its memory at ten can still get one at fifteen.
            key = make_memory_key("special_day", entry.day, entry.day, discriminator=f"{years}y")
            if key in generated_keys:
                continue
            candidates.append(
                MemoryCandidate(
                    memory_type="special_day",
                    category=CandidateCategory.EMERGENT_DAY,
                    date_range_start=entry.day,
                    date_range_end=entry.day,
                    person_names=[],
                    memory_key=key,
                    score=self.BASE_SCORE * _roundness(years),
                    # Evidence, never the title. This string reaches
                    # `auto suggest --json`, the attempt row, and the
                    # notification payload, and the title names real people.
                    reason=(
                        f"{years} years ago today · {entry.photos} photos "
                        f"over {round(hours_awake(entry))} hours"
                    ),
                    asset_count=entry.photos,
                    extra_params={
                        # The dates above are the day itself, honestly. What is
                        # timely is the anniversary, and the scorer is told so
                        # here instead of being handed a doctored date range.
                        "recency_date": same_day_in(entry.day, entry.day.year + years),
                        "source": "special-days catalogue",
                    },
                )
            )
        return candidates
