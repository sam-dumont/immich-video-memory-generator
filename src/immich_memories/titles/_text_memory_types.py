"""Memory type title generation helpers.

Extracted from text_builder.py to stay under the 500-line file limit.
"""

from __future__ import annotations

from datetime import date

from immich_memories.titles.text_builder import (
    TITLE_PATTERNS,
    SelectionType,
    TitleInfo,
    get_month_name,
    get_season_name,
)


def generate_season_title(
    season: str | None,
    year: int | None,
    end_year: int | None,
    person_name: str | None,
    locale: str,
) -> TitleInfo:
    """Generate title for a season memory."""
    if season is None or year is None:
        raise ValueError("Season and year required for season selection")
    patterns = TITLE_PATTERNS.get(locale, TITLE_PATTERNS["en"])
    season_name = get_season_name(season, locale)
    if end_year and end_year != year:
        main_title = patterns["season_year_span"].format(
            season=season_name, start_year=year, end_year=end_year
        )
    else:
        main_title = patterns["season_year"].format(season=season_name, year=year)
    return TitleInfo(
        main_title=main_title,
        subtitle=person_name,
        selection_type=SelectionType.SEASON,
    )


def generate_person_spotlight_title(
    year: int | None,
    person_name: str | None,
    locale: str,
) -> TitleInfo:
    """Generate title for a person spotlight memory."""
    if year is None:
        raise ValueError("Year required for person spotlight selection")
    patterns = TITLE_PATTERNS.get(locale, TITLE_PATTERNS["en"])
    subtitle = None
    if person_name:
        subtitle = patterns["person_spotlight_subtitle"].format(person=person_name)
    return TitleInfo(
        main_title=str(year),
        subtitle=subtitle,
        selection_type=SelectionType.PERSON_SPOTLIGHT,
    )


def generate_multi_person_title(
    year: int | None,
    person_names: list[str] | None,
    locale: str,  # noqa: ARG001
) -> TitleInfo:
    """Generate title for a multi-person memory."""
    if year is None:
        raise ValueError("Year required for multi-person selection")
    subtitle = None
    if person_names:
        if len(person_names) == 1:
            subtitle = person_names[0]
        elif len(person_names) == 2:
            subtitle = f"{person_names[0]} & {person_names[1]}"
        else:
            subtitle = f"{', '.join(person_names[:-1])} & {person_names[-1]}"
    return TitleInfo(
        main_title=str(year),
        subtitle=subtitle,
        selection_type=SelectionType.MULTI_PERSON,
    )


def generate_on_this_day_title(
    start_date: date | None,
    locale: str,
) -> TitleInfo:
    """Generate title for an On This Day memory."""
    if start_date is None:
        raise ValueError("Start date required for On This Day selection")
    patterns = TITLE_PATTERNS.get(locale, TITLE_PATTERNS["en"])
    month_name = get_month_name(start_date.month, locale)
    main_title = patterns["on_this_day"].format(month=month_name, day=start_date.day)
    subtitle = patterns["on_this_day_subtitle"]
    return TitleInfo(
        main_title=main_title,
        subtitle=subtitle,
        selection_type=SelectionType.ON_THIS_DAY,
    )
