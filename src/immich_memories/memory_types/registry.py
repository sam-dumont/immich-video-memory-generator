"""Memory type registry — defines all supported memory types."""

from enum import StrEnum


class MemoryType(StrEnum):
    """Enumeration of all supported memory video types."""

    # Phase 1
    YEAR_IN_REVIEW = "year_in_review"
    SEASON = "season"
    PERSON_SPOTLIGHT = "person_spotlight"
    MULTI_PERSON = "multi_person"
    MONTHLY_HIGHLIGHTS = "monthly_highlights"
    ON_THIS_DAY = "on_this_day"
    # Phase 2 (placeholders)
    HOLIDAY = "holiday"
    TRIP = "trip"
    THEN_AND_NOW = "then_and_now"
