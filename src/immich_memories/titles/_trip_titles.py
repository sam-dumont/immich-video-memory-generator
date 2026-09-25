"""Trip title text generation for map overview frames.

Produces titles like "TWO WEEKS IN LANZAROTE, SPAIN, SUMMER 2025".
Duration-aware and season-aware.
"""

from __future__ import annotations

from datetime import date

from immich_memories.i18n import film_text, film_text_n, month_name_forms
from immich_memories.i18n_places import comma, localise_country, localise_place
from immich_memories.place_phrases import Place, place_phrase
from immich_memories.place_phrases.place import infer_place_kind
from immich_memories.processing.clip_caption import resolve_caption_locale
from immich_memories.titles.letter_case import display_upper


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


_DURATION_KEYS = {1: "day", 2: "weekend", 3: "weekend", 7: "week", 14: "two_weeks"}
_DURATION_KEYS |= {15: "two_weeks", 21: "three_weeks"}


def _get_duration_label(days: int, locale: str = "en") -> str:
    """Convert trip duration to human-readable label ("A WEEK", "DEUX SEMAINES")."""
    key = "month" if days >= 28 else _DURATION_KEYS.get(days)
    label = film_text(f"trip.{key}", locale) if key else film_text_n("trip.days", days, locale)
    return display_upper(label)


def _get_time_label(start_date: date, end_date: date, locale: str = "en") -> str:
    """Get the time period label (season or month name + year).

    Single month → month name ("DECEMBER 2024").
    Cross-month → season name ("SUMMER 2025").
    """
    if start_date.month == end_date.month and start_date.year == end_date.year:
        forms = month_name_forms(start_date.month, locale)
        return display_upper(film_text("title.month_year", locale, year=start_date.year, **forms))
    season = film_text(f"season.{_get_season(start_date).lower()}", locale)
    if start_date.year == end_date.year:
        return display_upper(
            film_text("title.season_year", locale, season=season, year=start_date.year)
        )
    return display_upper(
        film_text(
            "title.season_year_span",
            locale,
            season=season,
            start_year=start_date.year,
            end_year=end_date.year,
        )
    )


def _localised(place: Place, locale: str) -> str:
    if place.kind == "countries":
        return " → ".join(localise_country(c, locale) for c in place.english.split(" → "))
    return localise_place(place.english, locale) or place.english


def generate_trip_title(
    location_name: str,
    start_date: date,
    end_date: date,
    locale: str = "en",
    kind: str | None = None,
) -> str:
    """Generate a trip title string for a map overview frame.

    `kind` is the scale trip naming chose ("city", "island", "region",
    "regions", "country", "countries"); a label from before it was recorded
    has it inferred. The phrase comes from the language's own rules
    (`place_phrases`); a language or a place with none gets a title with no
    preposition, place first, so a wrong one never reaches the screen.

    Examples:
        "TWO WEEKS IN THE NETHERLANDS, SUMMER 2025"
        "DEUX SEMAINES EN CRÈTE, GRÈCE, ÉTÉ 2025"
        "CRÈTE, GRÈCE · DEUX SEMAINES, ÉTÉ 2025" (no phrase for it)
    """
    locale = resolve_caption_locale(locale)
    days = (end_date - start_date).days + 1
    duration = _get_duration_label(days, locale)
    time_label = _get_time_label(start_date, end_date, locale)
    place = Place(location_name, kind or infer_place_kind(location_name))
    phrase = place_phrase(locale, place)
    if phrase is None:
        return f"{display_upper(_localised(place, locale))} · {duration}{comma(locale)}{time_label}"
    return f"{duration} {display_upper(phrase)}, {time_label}"
