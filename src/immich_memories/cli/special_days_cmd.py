"""Discovering the days worth a memory of their own."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import click

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
        default=Path("special-days.json"),
        help="Where to write the catalogue",
    )
    def discover_days(
        since: int, until: int, per_year: int, also_skip: tuple[str, ...], out: Path
    ) -> None:
        """Find days something happened on, and remember them for later.

        Meant to run occasionally rather than per generation: the point of a
        catalogue is a memory nobody asked for — five years to the day since
        the wedding — and that needs the days found in advance.

        Days inside a trip are skipped, since a trip memory already tells that
        story, and so are holidays, which have their own.
        """
        found = _scan_library(since, until, per_year, also_skip, out)
        out.write_text(json.dumps(found, indent=1))
        print_success(f"{len(found)} special days written to {out}")


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
        default=Path("special-days.json"),
    )
    def days_due(on: object, catalogue: Path) -> None:
        """Show which discovered days have an anniversary about now."""
        from immich_memories.automation.special_day_scan import (
            DiscoveredDay,
            anniversaries_due,
        )

        when = on.date() if on is not None else date.today()  # type: ignore[attr-defined]
        entries = [
            DiscoveredDay(
                day=date.fromisoformat(raw["day"]),
                title=raw.get("title", ""),
                subtitle=raw.get("subtitle", ""),
                what=raw.get("what", ""),
                photos=raw.get("photos", 0),
                window=None,
            )
            for raw in json.loads(catalogue.read_text())
        ]

        for entry, years in anniversaries_due(entries, when):
            console.print(
                f"[bold]{years} years ago[/bold]  {entry.day}  {entry.title or entry.what}"
            )
            if entry.subtitle:
                console.print(f"                {entry.subtitle}")
        print_success(f"{len(entries)} days in the catalogue, checked against {when}")


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
    since: int, until: int, per_year: int, also_skip: tuple[str, ...], out: Path
) -> list[dict]:
    """Walk the years, asking about the days that stand out in each."""
    from immich_memories.api.sync_client import SyncImmichClient
    from immich_memories.automation.special_day_scan import scan_year
    from immich_memories.config import get_config

    config = get_config()
    home = _homebase(config)
    found: list[dict] = []

    with SyncImmichClient(base_url=config.immich.url, api_key=config.immich.api_key) as client:
        for year in range(since, until + 1):
            assets = _year_of_assets(client, year)
            if not assets:
                continue
            console.print(f"[dim]{year}: {len(assets)} assets[/dim]")
            for day in scan_year(
                assets,
                llm_config=config.llm,
                home=home,
                thumbnail_for=lambda asset_id: client.get_asset_thumbnail(asset_id, "thumbnail"),
                ask=per_year,
                extra_holidays=also_skip,
            ):
                found.append(
                    {
                        "day": day.day.isoformat(),
                        "title": day.title,
                        "subtitle": day.subtitle,
                        "what": day.what,
                        "photos": day.photos,
                        "window": [w.isoformat() for w in day.window] if day.window else None,
                    }
                )
                console.print(f"  [green]{day.day}[/green]  {day.title or day.what}")
                out.write_text(json.dumps(found, indent=1))
    return found


def _year_of_assets(client: object, year: int) -> list:
    assets: list = []
    for month in range(1, 13):
        last = 28 if month == 2 else 30 if month in (4, 6, 9, 11) else 31
        try:
            res = client.search_metadata(  # type: ignore[attr-defined]
                taken_after=date(year, month, 1),
                taken_before=date(year, month, last),
                size=1000,
            )
            assets.extend(res.all_assets)
        except Exception:  # noqa: BLE001, PERF203 - one bad month must not end the scan
            continue
    return assets
