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
    ALBUM = "album"
    # Phase 2 (placeholders)
    HOLIDAY = "holiday"
    TRIP = "trip"
    THEN_AND_NOW = "then_and_now"
    # Discovered, not typed: its scope comes from a catalogue entry the library
    # produced, not from flags a user chose.
    SPECIAL_DAY = "special_day"


# What `generate --memory-type` accepts and the brief page offers, in the CLI's
# order. THEN_AND_NOW stays in the enum for its title branch and is offered on
# neither surface.
OFFERED_MEMORY_TYPES: tuple[MemoryType, ...] = (
    MemoryType.YEAR_IN_REVIEW,
    MemoryType.SEASON,
    MemoryType.PERSON_SPOTLIGHT,
    MemoryType.MULTI_PERSON,
    MemoryType.MONTHLY_HIGHLIGHTS,
    MemoryType.ON_THIS_DAY,
    MemoryType.ALBUM,
    MemoryType.TRIP,
    MemoryType.HOLIDAY,
    MemoryType.SPECIAL_DAY,
)
