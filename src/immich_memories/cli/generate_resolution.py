"""What `generate`'s flags mean once the config, the presets and the conflicts settle.

`_date_resolution` turns date flags into windows; this is the layer above it —
which flags outrank the config file, which combinations contradict each other,
and which presets fill a gap without overruling anything typed explicitly.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from immich_memories.api.models import Person
from immich_memories.api.person_expression import PersonExpression
from immich_memories.cli._date_resolution import resolve_date_range
from immich_memories.cli._helpers import print_error, print_info
from immich_memories.people.expression_window import DerivedPeopleWindow, library_people_window
from immich_memories.timeperiod import DateRange

if TYPE_CHECKING:
    from immich_memories.automation.special_day_scan import DiscoveredDay
    from immich_memories.config_loader import Config

logger = logging.getLogger(__name__)


def resolve_special_day(
    day: date | None, memory_type: str | None, event_id: str | None = None
) -> dict | None:
    """One day as preset parameters, from the catalogue where it has a row.

    ``--day`` carries a date and the catalogue is re-read here rather than
    having the title passed in, because the runner logs the whole argv and argv
    is readable in `ps` and in launchd's logs. The catalogue's titles name real
    people and places; only a date and opaque event selector travel in argv.

    A day with no row is still that day, and #1067 was opened because it could
    not be filmed at all: the owner named a race day the scan had never found.
    It comes back scoped to itself with no title, and the naming ladder above
    this takes over. Nothing here writes a row: filming a day is not
    discovering it, and a catalogue built by a render is a catalogue nobody
    judged.
    """
    from immich_memories.automation.catalogue import default_catalogue_path

    if event_id is not None and (day is None or memory_type != "special_day"):
        raise click.UsageError("--event-id requires --memory-type special_day and --day")
    if day is None:
        # The missing half of the pair is reported where the scope is resolved,
        # beside --season's and --holiday's identical rule.
        return None
    if memory_type == "on_this_day":
        # There the day is the anniversary being looked back from, not an
        # occasion the catalogue has a name for, so there is nothing to read.
        return None
    if memory_type != "special_day":
        raise click.UsageError("--day requires --memory-type special_day or on_this_day")

    entry = _catalogued_event(default_catalogue_path(), day, event_id)
    if entry is None:
        return {"day": day, "window": None, "title": "", "subtitle": "", "active_hours": 0.0}
    return _special_day_params(entry)


def _catalogued_event(path: Path, day: date, event_id: str | None) -> DiscoveredDay | None:
    """The row for a day, or nothing when the catalogue has never heard of it.

    An explicit --event-id still has to match something: it selects between
    rows the catalogue holds, so a miss there is a typo rather than a day the
    scan has not reached.
    """
    from immich_memories.automation.catalogue import entries_from

    matches = [entry for entry in entries_from(path) if entry.day == day]
    if event_id is not None:
        matches = [entry for entry in matches if entry.event_id == event_id]
        if not matches:
            raise click.UsageError(f"No catalogued event {event_id!r} on {day.isoformat()}")
    if len(matches) > 1:
        raise click.UsageError(
            f"{day.isoformat()} has multiple catalogued events; choose one with --event-id"
        )
    return matches[0] if matches else None


def _special_day_params(entry: DiscoveredDay) -> dict[str, Any]:
    """The row as preset parameters, with its description kept apart from its title.

    A row the scan described but never named carries words like "an outdoor
    music festival with multiple performances". Handed over as the title, that
    pins the memory before the rest of the naming ladder runs, so the reader is
    never asked and the album the pictures sit in is never looked up. It goes
    over as ``what`` instead: a fact the title prompt is told, and the preset's
    own fallback name when no reader answers.
    """
    from immich_memories.automation.catalogue import hours_awake, scope_window

    params: dict[str, Any] = {
        "day": entry.day,
        "window": scope_window(entry),
        "title": entry.title.strip(),
        "subtitle": entry.subtitle,
        "what": entry.what.strip(),
        "active_hours": hours_awake(entry),
    }
    if entry.event_id is not None:
        params["event_id"] = entry.event_id
    if entry.asset_ids:
        params["asset_ids"] = entry.asset_ids
    if entry.event_admission is not None:
        params["event_admission"] = entry.event_admission.as_record()
    return params


def resolve_people_condition(
    person_expression: str | None,
    *,
    person_names: list[str],
    person_match_typed: bool,
    from_album: str | None,
    memory_type: str | None,
    birthday: str | None,
) -> tuple[PersonExpression | None, list[str]]:
    """Read --people-expression, which replaces --person rather than refining it.

    A grouped condition names its own people, so mixing it with --person or
    --person-match would leave two disagreeing answers to the same question.
    """
    if person_expression is None:
        return None, person_names
    if person_names or person_match_typed:
        raise click.UsageError(
            "Use --people-expression separately from --person and --person-match"
        )
    try:
        condition = PersonExpression.parse(person_expression)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    if from_album or memory_type in {"trip", "album", "person_spotlight"} or birthday:
        raise click.UsageError(
            "Grouped people conditions currently support date-range memories, "
            "not trips, albums or a single-person birthday"
        )
    return condition, list(condition.leaf_values)


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

    A day with no row, or a row the scan could not name, returns None rather
    than an empty string, because None is what the layer above reads as "ask
    the model" instead of "the run named it itself".
    """
    if special_day is None:
        return title_override, subtitle_override
    return (
        title_override or special_day["title"] or None,
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
    people_window: DerivedPeopleWindow | None = None,
) -> tuple[DateRange, list[DateRange]]:
    """Resolve what a memory covers: date range(s), or an album that defines its own.

    Returns the display range plus the ranges to search. Album mode returns no ranges
    at all — its span comes from the album's assets, which need a connection to read —
    so the returned range is a stand-in that album mode replaces and never displays.

    ``people_window`` is the span a dateless people memory derived from its
    people's birth dates; it is only ever supplied when nothing was typed.
    """
    if from_album:
        now = datetime.now()
        return DateRange(start=now, end=now), []
    if people_window is not None:
        return people_window.range, [people_window.range]

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


def _validate_album_scope(
    *,
    from_album: str | None,
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
    """The album is the whole scope, read in both directions.

    With an album, date scoping is meaningless because album mode replaces
    date-range discovery. Without one, --memory-type album has nothing to
    select from -- it is the one type that resolves no window of its own.
    """
    if not from_album:
        if memory_type == "album":
            raise click.UsageError("--memory-type album needs --from-album to name the album")
        return
    conflicts = {
        "--year": year,
        "--start": start,
        "--end": end,
        "--period": period,
        "--birthday": birthday,
        "--season": season,
        "--month": month,
        # --memory-type album says what --from-album already says, so it is the
        # one type that agrees with album mode rather than competing with it.
        "--memory-type": None if memory_type == "album" else memory_type,
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


def _apply_photo_duration_override(config: Config, *, photo_duration: float | None) -> None:
    """Let --photo-duration outrank the configured photos.duration."""
    if photo_duration is not None:
        config.photos.duration = photo_duration


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


def _dateless_people_ask(
    memory_type: str | None,
    *,
    from_album: str | None,
    year: int | None,
    start: str | None,
    end: str | None,
    period: str | None,
    birthday: str | None,
    season: str | None,
    month: int | None,
) -> bool:
    """A people memory asked for with no window at all: "these people, forever"."""
    return memory_type in ("person_spotlight", "multi_person") and not any(
        (from_album, year, start, end, period, birthday, season, month is not None)
    )


def _named_people(
    people_condition: PersonExpression | None,
    person_names: list[str],
    person_match: str,
) -> PersonExpression | None:
    """The condition the run filters on, whether it was typed as a group or as names."""
    if people_condition is not None:
        return people_condition
    leaves = tuple(
        PersonExpression("person", value=name) for name in dict.fromkeys(person_names) if name
    )
    if not leaves:
        return None
    if len(leaves) == 1:
        return leaves[0]
    return PersonExpression("any" if person_match == "or" else "all", children=leaves)


def resolve_people_memory_window(
    *,
    config: Config,
    memory_type: str | None,
    people_condition: PersonExpression | None,
    person_names: list[str],
    person_match: str,
    from_album: str | None,
    year: int | None,
    start: str | None,
    end: str | None,
    period: str | None,
    birthday: str | None,
    season: str | None,
    month: int | None,
) -> DerivedPeopleWindow | None:
    """The window a dateless people memory derives from its people's birth dates.

    None whenever the ask already carries dates, or is not a people memory. A
    people memory whose people have no birth date anywhere is refused here, so
    an empty result is never dressed up as a whole-library film.
    """
    if not _dateless_people_ask(
        memory_type,
        from_album=from_album,
        year=year,
        start=start,
        end=end,
        period=period,
        birthday=birthday,
        season=season,
        month=month,
    ):
        return None
    expression = _named_people(people_condition, person_names, person_match)
    if expression is None:
        return None
    window = library_people_window(expression, read_people=lambda: _library_people(config))
    if window is None:
        raise click.UsageError(
            f"--year is required with --memory-type {memory_type}: none of the named people "
            "has a birth date in Immich or people.yaml, so no window can be derived from them"
        )
    return window


def _library_people(config: Config) -> list[Person]:
    """The roster, or nothing when Immich cannot be reached.

    The run connects again moments later and reports a real outage in its own
    words; here an unreachable server only means the curated people file is the
    one source left to answer from.
    """
    from immich_memories.api.immich import ImmichAPIError, SyncImmichClient

    try:
        with SyncImmichClient(
            base_url=config.immich.url,
            api_key=config.immich.api_key,
            api_version=config.immich.api_version,
        ) as client:
            return client.get_all_people(with_hidden=True)
    except (ImmichAPIError, OSError):
        return []


def announce_people_window(
    derived: DerivedPeopleWindow | None, date_range: DateRange
) -> dict[str, str]:
    """Say where a window nobody typed came from, and hand the run record the same sentence."""
    if derived is None:
        return {}
    logger.info("Memory window %s: %s", date_range.description, derived.origin)
    print_info(f"Memory window: {date_range.description}: {derived.origin}")
    return {"window_origin": derived.origin}
