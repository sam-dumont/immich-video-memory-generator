"""Discovering the days worth a memory of their own."""

from __future__ import annotations

import calendar
import json
from datetime import date
from pathlib import Path

import click

from immich_memories.automation.catalogue import (
    default_catalogue_path,
    entries_from,
    load_catalogue,
)
from immich_memories.cli._helpers import console, print_success


def register_special_day_commands(main: click.Group) -> None:
    """Register the special-days commands on the main CLI group."""
    _register_discover(main)
    _register_due(main)


def _register_discover(main: click.Group) -> None:
    @main.command("discover-days")
    @click.option("--since", type=int, default=2007, help="First year to scan")
    @click.option("--until", type=int, default=date.today().year, help="Last year to scan")
    @click.option("--per-year", type=int, default=6, help="Busiest candidates to ask about")
    @click.option(
        "--also-skip",
        multiple=True,
        metavar="HOLIDAY",
        help="A holiday name or MM-DD this library keeps that the defaults miss",
    )
    @click.option(
        "--out",
        type=click.Path(dir_okay=False, path_type=Path),
        default=default_catalogue_path(),
        help="Where to write the catalogue",
    )
    @click.option(
        "--rescan",
        is_flag=True,
        help="Start over, ignoring and replacing the existing catalogue",
    )
    def discover_days(
        since: int,
        until: int,
        per_year: int,
        also_skip: tuple[str, ...],
        out: Path,
        rescan: bool,
    ) -> None:
        """Find days something happened on, and remember them for later.

        Meant to run occasionally rather than per generation: the point of a
        catalogue is a memory nobody asked for — five years to the day since
        the wedding — and that needs the days found in advance.

        Days inside a trip are skipped, since a trip memory already tells that
        story, and so are holidays, which have their own.

        Resumes by default: years already in the catalogue are not scanned
        again, which matters for a command that runs for hours. --rescan
        starts over.
        """
        found = _scan_library(since, until, per_year, also_skip, out, rescan=rescan)
        _write_catalogue(out, found, rescan=rescan)
        days = sum(1 for entry in found if entry.get("day"))
        print_success(f"{days} special days in {out}")


def _register_due(main: click.Group) -> None:
    @main.command("days-due")
    @click.option(
        "--on",
        type=click.DateTime(formats=["%Y-%m-%d"]),
        default=None,
        help="The date to look around (default today)",
    )
    @click.option(
        "--catalogue",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        default=default_catalogue_path(),
    )
    def days_due(on: object, catalogue: Path) -> None:
        """Show which discovered days have an anniversary about now."""
        from immich_memories.automation.special_day_scan import anniversaries_due

        when = on.date() if on is not None else date.today()  # type: ignore[attr-defined]
        entries = entries_from(catalogue)

        for entry, years in anniversaries_due(entries, when):
            line = f"[bold]{years} years ago[/bold]  {entry.day}  {entry.title or entry.what}"
            if entry.window:
                start, end = entry.window
                line += f"  [dim]{start:%H:%M}-{end:%H:%M}[/dim]"
            if entry.active_hours:
                line += f"  [dim]{entry.active_hours}h[/dim]"
            console.print(line)
            if entry.subtitle:
                console.print(f"                {entry.subtitle}")
        print_success(f"{len(entries)} days in the catalogue, checked against {when}")


def _years_in(catalogue: list[dict]) -> set[int]:
    """Years the scan finished, so a resumed run does not repeat them.

    Recorded, not inferred from what was found. Reading finds as the record
    got it wrong in both directions: a year whose queries partly failed left
    one entry behind and was frozen half-scanned forever, and a year that was
    scanned cleanly and simply held nothing left no entry at all and was
    re-scanned in full on every resume.
    """
    return {int(entry["scanned"]) for entry in catalogue if isinstance(entry.get("scanned"), int)}


def _write_catalogue(path: Path, found: list[dict], *, rescan: bool) -> None:
    """Write the catalogue, refusing to put nothing over something.

    `found` starts empty, and with the monthly errors that used to be
    swallowed an unreachable Immich wrote [] over twenty years of scanning
    and called it a success.
    """
    if not found and not rescan and load_catalogue(path):
        console.print(
            f"[yellow]Found nothing; leaving {path} as it was. "
            f"Pass --rescan to replace it.[/yellow]"
        )
        return
    path.write_text(json.dumps(found, indent=1))


def _homebase(config: object) -> tuple[float, float] | None:
    """Home coordinates, or None when this library has not set any.

    Trip exclusion is the scan's first filter and it needs a real home. A
    guessed one reads every day spent at the actual home as time away, and
    the year is swallowed before a single day is considered — so no home
    means no trip exclusion, not a stand-in for somebody else's.
    """
    trips = config.trips  # type: ignore[attr-defined]
    if trips.homebase_latitude == trips.homebase_longitude == 0.0:
        return None
    return (trips.homebase_latitude, trips.homebase_longitude)


def _scan_library(
    since: int,
    until: int,
    per_year: int,
    also_skip: tuple[str, ...],
    out: Path,
    *,
    rescan: bool = False,
) -> list[dict]:
    """Walk the years, asking about the days that stand out in each.

    Carries the existing catalogue forward so an interrupted scan resumes
    where it stopped rather than starting the twenty years again.
    """
    from immich_memories.api.sync_client import SyncImmichClient
    from immich_memories.automation.special_day_scan import scan_year
    from immich_memories.config import get_config

    config = get_config()
    home = _homebase(config)
    found: list[dict] = [] if rescan else load_catalogue(out)
    already = set() if rescan else _years_in(found)

    with SyncImmichClient(base_url=config.immich.url, api_key=config.immich.api_key) as client:
        for year in range(since, until + 1):
            if year in already:
                console.print(f"[dim]{year}: already scanned[/dim]")
                continue
            assets = _year_of_assets(client, year)
            if not assets:
                found.append({"scanned": year})
                continue
            console.print(f"[dim]{year}: {len(assets)} assets[/dim]")
            for day in scan_year(
                assets,
                llm_config=config.llm,
                home=home,
                thumbnail_for=lambda asset_id: client.get_asset_thumbnail(asset_id, "thumbnail"),
                ask=per_year,
                extra_holidays=also_skip,
                analysis_config=config.analysis,
                trips_config=config.trips,
            ):
                found.append(
                    {
                        "day": day.day.isoformat(),
                        "title": day.title,
                        "subtitle": day.subtitle,
                        "what": day.what,
                        "photos": day.photos,
                        "window": [w.isoformat() for w in day.window] if day.window else None,
                        "active_hours": day.active_hours,
                        "run_start": day.run_start.isoformat() if day.run_start else None,
                        "run_end": day.run_end.isoformat() if day.run_end else None,
                    }
                )
                console.print(f"  [green]{day.day}[/green]  {day.title or day.what}")
                # Written as we go: a scan this long is worth keeping in
                # pieces, and `found` already carries the earlier catalogue.
                out.write_text(json.dumps(found, indent=1))

            # Only here, with every month of the year read and asked about.
            # A year interrupted or partly refused says nothing, and gets
            # scanned again rather than standing as half an answer.
            found.append({"scanned": year})
            out.write_text(json.dumps(found, indent=1))
    return found


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    """A month as [first day, last day], both ends included.

    The search API serializes a bare date upper bound as the END of that day,
    so this is a closed window and no day falls between two months. Naming the
    first of the next month instead swallows it whole: December returned New
    Year's Day, which was then catalogued under the following year and made
    resume skip scanning that year at all.

    calendar rather than hardcoded lengths, because February at 28 put the
    leap day in the gap between the February and March queries and made an
    event on one structurally undiscoverable.
    """
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _month_of_assets(client: object, year: int, month: int) -> list:
    """Every asset in one month, following the pages to the end.

    The densest months are the ones this command is looking for, and they are
    exactly the ones a single query truncates.
    """
    start, end = _month_bounds(year, month)
    found: list = []
    page = 1
    while True:
        res = client.search_metadata(  # type: ignore[attr-defined]
            taken_after=start,
            taken_before=end,
            page=page,
            size=1000,
        )
        found.extend(res.all_assets)
        if not res.next_page:
            return found
        page += 1


def _year_of_assets(client: object, year: int) -> list:
    """One year of assets, month by month, deduplicated.

    A month that fails is skipped rather than ending the scan, but a year in
    which every month failed is not an empty year — it is a broken
    connection, and reporting it as success wrote an empty catalogue over a
    good one.
    """
    by_id: dict[str, object] = {}
    failures = 0
    for month in range(1, 13):
        try:
            for asset in _month_of_assets(client, year, month):
                by_id[asset.id] = asset
        except Exception as exc:  # noqa: BLE001, PERF203 - one bad month is not the scan
            failures += 1
            console.print(f"[yellow]{year}-{month:02d}: {type(exc).__name__}[/yellow]")

    if failures == 12:
        msg = f"Every monthly query for {year} failed — is Immich reachable?"
        raise RuntimeError(msg)
    return list(by_id.values())
