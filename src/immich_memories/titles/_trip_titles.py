"""Trip title text generation for map overview frames.

Produces titles like "TWO WEEKS IN LANZAROTE, SPAIN, SUMMER 2025".
Duration-aware and season-aware.
"""

from __future__ import annotations

from datetime import date

from immich_memories.i18n import get_month_name
from immich_memories.i18n_places import localise_place
from immich_memories.processing.clip_caption import resolve_caption_locale

# Duration thresholds for human-readable text
_DURATION_LABELS = [
    (2, 3, "A WEEKEND"),
    (3, 5, "{days} DAYS"),
    (5, 8, "A WEEK"),
    (8, 15, "{days} DAYS"),
    (15, 15, "TWO WEEKS"),
    (15, 22, "{days} DAYS"),
    (22, 30, "THREE WEEKS"),
    (30, 999, "A MONTH"),
]


def _get_season(d: date) -> str:
    """Get the season name for a date (Northern Hemisphere)."""
    month = d.month
    if month in (3, 4, 5):
        return "SPRING"
    if month in (6, 7, 8):
        return "SUMMER"
    if month in (9, 10, 11):
        return "AUTUMN"
    return "WINTER"


def _get_duration_label(days: int, locale: str = "en") -> str:
    """Convert trip duration to human-readable label."""
    if locale == "fr":
        return {
            1: "UNE JOURNÉE",
            2: "UN WEEK-END",
            3: "UN WEEK-END",
            7: "UNE SEMAINE",
            14: "DEUX SEMAINES",
            15: "DEUX SEMAINES",
            21: "TROIS SEMAINES",
        }.get(days, "UN MOIS" if days >= 28 else f"{days} JOURS")
    if days == 1:
        return "A DAY"
    if days <= 3:
        return "A WEEKEND"
    if days == 7:
        return "A WEEK"
    if days in (14, 15):
        return "TWO WEEKS"
    if days == 21:
        return "THREE WEEKS"
    if days >= 28:
        return "A MONTH"
    return f"{days} DAYS"


def _get_time_label(start_date: date, end_date: date, locale: str = "en") -> str:
    """Get the time period label (season or month name + year).

    Single month → month name ("DECEMBER 2024").
    Cross-month → season name ("SUMMER 2025").
    """
    if start_date.month == end_date.month and start_date.year == end_date.year:
        month_name = get_month_name(start_date.month, locale).upper()
        return f"{month_name} {start_date.year}"
    season = _get_season(start_date)
    if locale == "fr":
        season = {"SPRING": "PRINTEMPS", "SUMMER": "ÉTÉ", "AUTUMN": "AUTOMNE", "WINTER": "HIVER"}[
            season
        ]
    years = (
        str(start_date.year)
        if start_date.year == end_date.year
        else f"{start_date.year}–{end_date.year}"
    )
    return f"{season} {years}"


def generate_trip_title(
    location_name: str,
    start_date: date,
    end_date: date,
    locale: str = "en",
) -> str:
    """Generate a trip title string for a map overview frame.

    Examples:
        "TWO WEEKS IN ÎLE D'OLÉRON, FRANCE, SUMMER 2025"
        "A WEEKEND IN BARCELONA, SPAIN, SPRING 2025"
        "10 DAYS IN LANZAROTE, SPAIN, DECEMBER 2024"
    """
    locale = resolve_caption_locale(locale)
    days = (end_date - start_date).days + 1
    duration = _get_duration_label(days, locale)
    time_label = _get_time_label(start_date, end_date, locale)
    location_upper = (localise_place(location_name, locale) or location_name).upper()
    preposition = "À" if locale == "fr" else "IN"
    return f"{duration} {preposition} {location_upper}, {time_label}"
