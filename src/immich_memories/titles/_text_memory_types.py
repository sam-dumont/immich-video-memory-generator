"""Memory type title generation helpers."""

from __future__ import annotations

from datetime import date

from immich_memories.i18n import month_name_forms
from immich_memories.titles.text_builder import (
    SelectionType,
    TitleInfo,
    _generate_date_range_title,
    get_season_name,
    title_pattern,
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
    season_name = get_season_name(season, locale)
    if end_year and end_year != year:
        main_title = title_pattern(
            "season_year_span",
            locale,
            season=season_name,
            start_year=year,
            end_year=end_year,
            end_year_short=f"{end_year % 100:02d}",
        )
    else:
        main_title = title_pattern("season_year", locale, season=season_name, year=year)
    return TitleInfo(
        main_title=main_title,
        subtitle=person_name,
        selection_type=SelectionType.SEASON,
    )


def generate_person_spotlight_title(
    year: int | None,
    person_name: str | None,
    locale: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> TitleInfo:
    """Title a person spotlight: a year with the person, or the person over several years.

    A film over several years opens on the person's name alone. Its span usually starts at a
    birth date and ends today, and a date over a whole life so far says nothing the name does
    not. With no name to show, the span's own dates title it.
    """
    if year is None and start_date is not None and end_date is not None:
        if not person_name:
            return _generate_date_range_title(start_date, end_date, None, locale)
        return TitleInfo(
            main_title=person_name, subtitle=None, selection_type=SelectionType.PERSON_SPOTLIGHT
        )
    if year is None:
        raise ValueError("Year required for person spotlight selection")
    subtitle = None
    if person_name:
        subtitle = title_pattern("person_spotlight_subtitle", locale, person=person_name)
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
    forms = month_name_forms(start_date.month, locale)
    main_title = title_pattern("on_this_day", locale, day=start_date.day, **forms)
    subtitle = title_pattern("on_this_day_subtitle", locale)
    return TitleInfo(
        main_title=main_title,
        subtitle=subtitle,
        selection_type=SelectionType.ON_THIS_DAY,
    )
