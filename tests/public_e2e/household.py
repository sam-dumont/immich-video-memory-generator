"""A public household: its script, its manifest and its credits.

A household is a story told with other people's openly licensed pictures. The script
(`household.yaml`) says whose timeline it borrows, how far its dates move, where home is,
who the people are and which films to run. The manifest records every file the library
ships, where it came from, under which licence, and which of its fields the script
invented. Nothing here fetches or uploads; it is the pure part the other steps share.
"""

from __future__ import annotations

import csv
import math
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path

import yaml

REMOVAL_CONTACT = "sam@dropbars.be"
# A home picture is moved to the centre of its grid cell: about 5.5 km of latitude, so
# the town survives reverse geocoding and the street does not.
HOME_GRID_DEGREES = 0.05
HOME_RADIUS_KM = 3.0


@dataclass(frozen=True)
class CastMember:
    """A person the household names: an adult by a made-up name, a child by role only."""

    name: str
    seeds: tuple[int, ...]
    child: bool = False


@dataclass(frozen=True)
class Film:
    """One film the E2E run makes: an id and the `generate` arguments that make it."""

    id: str
    args: tuple[str, ...]
    # "no_film": the right outcome is no film at all (a month with nothing worth showing).
    expect: str = "film"


@dataclass(frozen=True)
class Household:
    """The script of one household, read from `household.yaml`."""

    name: str
    summary: str
    uid: str
    first_day: str
    last_day: str
    date_shift_years: int
    owner: str
    cast: tuple[CastMember, ...]
    relations: tuple[tuple[str, str, str], ...]
    films: tuple[Film, ...]
    drop: frozenset[int] = frozenset()
    max_pictures: int = 2200
    videos: tuple[int, ...] = ()
    seed: int = 7

    @classmethod
    def load(cls, path: Path) -> Household:
        raw = yaml.safe_load(path.read_text())
        return cls(
            name=raw["name"],
            summary=raw["summary"],
            uid=raw["source"]["uid"],
            first_day=str(raw["source"]["first_day"]),
            last_day=str(raw["source"]["last_day"]),
            date_shift_years=int(raw["date_shift_years"]),
            owner=raw["owner"],
            cast=tuple(
                CastMember(m["name"], tuple(m.get("seeds", ())), bool(m.get("child", False)))
                for m in raw.get("cast", ())
            ),
            relations=tuple(tuple(r) for r in raw.get("relations", ())),  # type: ignore[misc]
            films=tuple(
                Film(f["id"], tuple(str(a) for a in f["args"]), f.get("expect", "film"))
                for f in raw["films"]
            ),
            drop=frozenset(int(p) for p in raw.get("drop", ())),
            max_pictures=int(raw["source"].get("max_pictures", 2200)),
            videos=tuple(int(p) for p in raw.get("videos", ())),
            seed=int(raw.get("seed", 7)),
        )


def shift_years(moment: datetime, years: int) -> datetime:
    """Move a capture time by whole years, so Christmas stays on the 25th.

    A 29 February that lands in a common year becomes the 28th.
    """
    try:
        return moment.replace(year=moment.year + years)
    except ValueError:
        return moment.replace(year=moment.year + years, day=28)


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def home_point(points: Iterable[tuple[float, float]]) -> tuple[float, float] | None:
    """The centre of the grid cell the photographer shot from most: where home is."""
    cells = Counter(
        (math.floor(lat / HOME_GRID_DEGREES), math.floor(lon / HOME_GRID_DEGREES))
        for lat, lon in points
    )
    if not cells:
        return None
    (row, col), _count = cells.most_common(1)[0]
    return (
        round((row + 0.5) * HOME_GRID_DEGREES, 4),
        round((col + 0.5) * HOME_GRID_DEGREES, 4),
    )


def snap_home(
    point: tuple[float, float] | None,
    home: tuple[float, float] | None,
    radius_km: float = HOME_RADIUS_KM,
) -> tuple[float, float] | None:
    """A point near home moves onto home; a trip keeps its real place."""
    if point is None or home is None:
        return point
    return home if _km(point, home) <= radius_km else point


@dataclass
class ManifestRow:
    """One shipped file. `scripted` names the fields the household invented."""

    key: str
    file: str
    kind: str
    source: str
    source_id: str
    source_page: str
    creator: str
    licence: str
    licence_url: str
    taken_at: str
    latitude: str = ""
    longitude: str = ""
    derived_from: str = ""
    scripted: str = ""
    sha256: str = ""


_COLUMNS = [f.name for f in fields(ManifestRow)]


def write_manifest(path: Path, rows: Sequence[ManifestRow]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_COLUMNS)
        writer.writeheader()
        for row in sorted(rows, key=lambda r: (r.taken_at, r.key)):
            writer.writerow(asdict(row))


def read_manifest(path: Path) -> list[ManifestRow]:
    with path.open(newline="") as handle:
        return [ManifestRow(**row) for row in csv.DictReader(handle)]


def credits_markdown(household: Household, rows: Sequence[ManifestRow]) -> str:
    """CREDITS.md: who made the pictures, under which licence, and how to ask for removal."""
    by_creator: dict[tuple[str, str, str], int] = Counter(
        (row.creator, row.licence, row.source) for row in rows if row.creator
    )
    synthetic = sum(1 for row in rows if row.source == "synthetic")
    lines = [
        f"# Credits: the {household.name} household",
        "",
        "Every picture and video in this household is openly licensed (CC0 or CC BY) and",
        "used as published. The story, the people's names, the capture dates (moved",
        f"{household.date_shift_years} years forward) and the home location (snapped to a",
        "grid point) are a script, not facts about the photographs. `manifest.csv` names",
        "each file's own page, licence and SHA-256, and which fields the script changed.",
        "",
        f"Removal: anyone pictured, or any creator, can ask for a file to be removed by writing to {REMOVAL_CONTACT}.",
        "The built library is kept private and never published; only this list is.",
        "",
        "| Creator | Source | Licence | Files |",
        "| --- | --- | --- | --- |",
    ]
    for (creator, licence, source), count in sorted(by_creator.items()):
        lines.append(f"| {creator} | {source} | {licence} | {count} |")
    if synthetic:
        lines += [
            "",
            f"{synthetic} files are synthetic clutter made at build time (screenshots, documents,",
            "burst, blurred and dark copies of the pictures above); they carry the licence of",
            "the picture they were made from, or none when rendered from nothing.",
        ]
    return "\n".join(lines) + "\n"
