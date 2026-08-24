"""What `generate`'s flags mean once the config, the presets and the conflicts settle.

`_date_resolution` turns date flags into windows; this is the layer above it —
which flags outrank the config file, which combinations contradict each other,
and which presets fill a gap without overruling anything typed explicitly.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import click

from immich_memories.cli._date_resolution import resolve_date_range
from immich_memories.cli._helpers import print_error
from immich_memories.timeperiod import DateRange

if TYPE_CHECKING:
    from immich_memories.config_loader import Config


def resolve_special_day(day: date | None, memory_type: str | None) -> dict | None:
    """What the catalogue records about one day, as preset parameters.

    ``--day`` carries a date and the catalogue is re-read here rather than
    having the title passed in, because the runner logs the whole argv and argv
    is readable in `ps` and in launchd's logs. The catalogue's titles name real
    people and places; a date is the most a scheduled run should say out loud.

    Refuse over fake throughout: a day the catalogue never found, or one the
    model could not name, is a day with nothing truthful to put on its title
    card, so it errors rather than rendering "Memories from 12 June 2016".
    """
    from immich_memories.automation.catalogue import (
        default_catalogue_path,
        entries_from,
        hours_awake,
    )

    if day is None:
        # The missing half of the pair is reported where the scope is resolved,
        # beside --season's and --holiday's identical rule.
        return None
    if memory_type != "special_day":
        raise click.UsageError("--day requires --memory-type special_day")

    path = default_catalogue_path()
    entry = next((e for e in entries_from(path) if e.day == day), None)
    if entry is None:
        raise click.UsageError(
            f"{day.isoformat()} is not one of the days in {path}. Run "
            "`immich-memories days-due` to see which days it holds, or "
            "`immich-memories discover-days` to look for more."
        )

    name = (entry.title or "").strip() or (entry.what or "").strip()
    if not name:
        raise click.UsageError(
            f"{path} has neither a title nor a 'what' for {day.isoformat()}, so there "
            "is nothing truthful to call the memory. Re-run `immich-memories "
            "discover-days --rescan` to name it."
        )

    return {
        "day": entry.day,
        "window": entry.window,
        "title": name,
        "subtitle": entry.subtitle,
        "active_hours": hours_awake(entry),
    }


def name_from_catalogue(
    special_day: dict | None,
    title_override: str | None,
    subtitle_override: str | None,
) -> tuple[str | None, str | None]:
    """Let the catalogue name the memory, unless the run named it itself.

    The catalogue's title is the point of a special day and also the one thing
    that must never travel on the command line, so it is picked up from the
    file rather than passed in. --title and --subtitle still win: this fills a
    gap, it does not argue.
    """
    if special_day is None:
        return title_override, subtitle_override
    return (
        title_override or special_day["title"],
        subtitle_override or special_day["subtitle"] or None,
    )


def _resolve_generation_scope(
    *,
    from_album: str | None,
    year: int | None,
    start: str | None,
    end: str | None,
    period: str | None,
    birthday: str | None,
    memory_type: str | None,
    season: str | None,
    month: int | None,
    hemisphere: str,
    years_back: int | None,
    on_this_day_target: date | None,
    holiday: str | None = None,
    preset_params: dict | None = None,
) -> tuple[DateRange, list[DateRange]]:
    """Resolve what a memory covers: date range(s), or an album that defines its own.

    Returns the display range plus the ranges to search. Album mode returns no ranges
    at all — its span comes from the album's assets, which need a connection to read —
    so the returned range is a stand-in that album mode replaces and never displays.
    """
    if from_album:
        now = datetime.now()
        return DateRange(start=now, end=now), []

    # WHY: birthday="auto" means detect from Immich later — don't pass to parser
    initial_birthday = None if birthday == "auto" else birthday
    date_result = resolve_date_range(
        year,
        start,
        end,
        period,
        initial_birthday,
        memory_type=memory_type,
        season=season,
        month=month,
        hemisphere=hemisphere,
        years_back=years_back,
        on_this_day_target=on_this_day_target,
        holiday=holiday,
        preset_params=preset_params,
    )

    # Normalize to single DateRange for display (multi-range for on_this_day)
    if not isinstance(date_result, list):
        return date_result, [date_result]
    if not date_result:
        print_error("No date ranges generated for On This Day")
        sys.exit(1)
    return DateRange(start=date_result[-1].start, end=date_result[0].end), date_result


def _reject_album_scope_conflicts(
    *,
    year: int | None,
    start: str | None,
    end: str | None,
    period: str | None,
    birthday: str | None,
    season: str | None,
    month: int | None,
    memory_type: str | None,
    person_names: list[str] | tuple[str, ...],
) -> None:
    """Album mode replaces date-range discovery, so date scoping is meaningless."""
    conflicts = {
        "--year": year,
        "--start": start,
        "--end": end,
        "--period": period,
        "--birthday": birthday,
        "--season": season,
        "--month": month,
        "--memory-type": memory_type,
        "--person": person_names,
    }
    used = sorted(flag for flag, value in conflicts.items() if value)
    if used:
        raise click.UsageError(f"--from-album selects its own assets; drop {', '.join(used)}")


SHORT_FORM_SECONDS = ("15", "30", "60", "90")


@dataclass(frozen=True, slots=True)
class ShortForm:
    """What a short-form preset resolves to."""

    duration: float | None
    orientation: str


def resolve_short_form(
    short_form: str | None,
    *,
    duration: float | None,
    orientation: str,
    orientation_was_given: bool = False,
) -> ShortForm:
    """Apply a short-form preset without overruling anything asked for explicitly.

    The preset is vertical because that is the shape Reels, Shorts and TikTok
    take, but square short-form is real, so an orientation the user actually
    typed wins. Same for a duration: the preset fills a gap, it does not argue.
    """
    if short_form is None:
        return ShortForm(duration=duration, orientation=orientation)
    return ShortForm(
        duration=duration if duration is not None else int(short_form),
        orientation=orientation if orientation_was_given else "portrait",
    )


def _apply_scalar_overrides(
    config: Config,
    *,
    photo_duration: float | None,
    refinement_passes: int | None,
) -> None:
    """Let a flag outrank the config file for the dials that have both."""
    if photo_duration is not None:
        config.photos.duration = photo_duration
    if refinement_passes is not None:
        config.analysis.max_refinement_passes = refinement_passes


def resolve_inclusion(flag: bool | None, *, config_enabled: bool) -> bool:
    """Resolve a content-inclusion choice from an optional CLI flag and config.

    `flag or config_enabled` made the flag one-way: with the feature enabled in
    config there was no way to ask for a run without it. None means "not
    specified", so the config decides; an explicit True or False wins.
    """
    if flag is None:
        return config_enabled
    return flag


def _arm_selection_trace(path: Path | None) -> None:
    """Tell run_selection where to write its stage-by-stage report."""
    if path:
        os.environ["IMMICH_MEMORIES_SELECTION_TRACE"] = str(path)
