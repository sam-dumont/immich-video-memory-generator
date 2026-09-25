"""Photographer timelines in the CommonCatalog index: rank them per theme, cut them into episodes.

    uv run --with duckdb python -m tests.public_e2e.timelines rank --theme dog
    uv run --with duckdb python -m tests.public_e2e.timelines sheet --uid 12345@N00 [--from 2008 --to 2011]

`rank` lists the photographers whose own uploads look like a household with the theme in
it: enough pictures over enough years, geotagged, and the theme recurring. `sheet` draws
one small picture per episode of one photographer, so a person can pick by eye.
"""

from __future__ import annotations

import argparse
import io
import sys
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw

from tests.public_e2e.paths import USER_AGENT, work_root

# Several languages: Flickr tags follow the photographer, not the dataset.
THEMES = {
    "dog": r"\b(dogs?|pupp(y|ies)|hunde?|chiens?|perros?|cane|hond(en)?)\b",
    "horse": r"\b(horses?|pony|ponies|equestrian|dressage|pferde?|chevals?|caballos?|paard(en)?)\b",
    "travel": r"\b(travel|vacation|holidays?|trip|roadtrip|urlaub|vacances|viaje)\b",
    "cycling": r"\b(cycling|bike|bicycle|marathon|triathlon|race|running|velo|radsport)\b",
    "family": r"\b(family|familie|famille|wedding|christmas|reunion|grandma|grandpa)\b",
}
EPISODE_GAP = timedelta(hours=36)


@dataclass(frozen=True)
class Shot:
    """One indexed photo, only what episodes and sheets need."""

    photoid: int
    taken: datetime
    url: str


def episodes(shots: Sequence[Shot], gap: timedelta = EPISODE_GAP) -> list[list[Shot]]:
    """Group shots into episodes: a new one starts after `gap` with no picture."""
    ordered = sorted(shots, key=lambda shot: shot.taken)
    groups: list[list[Shot]] = []
    for shot in ordered:
        if groups and shot.taken - groups[-1][-1].taken <= gap:
            groups[-1].append(shot)
        else:
            groups.append([shot])
    return groups


def index_glob() -> str:
    return str(work_root() / "commoncatalog-index" / "*.parquet")


def rank(theme: str, min_photos: int, min_years: int, limit: int) -> list[tuple]:
    import duckdb

    pattern = THEMES[theme]
    return duckdb.sql(
        f"""
        WITH rows AS (
          SELECT uid, unickname, year(datetaken) AS y,
                 (latitude IS NOT NULL AND NOT isnan(latitude) AND latitude <> 0) AS geo,
                 regexp_matches(lower(coalesce(usertags, '') || ' ' || coalesce(title, '')),
                                '{pattern}') AS hit
          FROM read_parquet('{index_glob()}')
          WHERE datetaken IS NOT NULL AND year(datetaken) BETWEEN 2000 AND 2014
        )
        SELECT uid, any_value(unickname) AS nick, count(*) AS photos,
               count(DISTINCT y) AS years, min(y) AS first, max(y) AS last,
               round(avg(geo::int), 2) AS geo_share, sum(hit::int) AS theme_hits,
               count(DISTINCT CASE WHEN hit THEN y END) AS theme_years
        FROM rows GROUP BY uid
        HAVING count(*) >= {int(min_photos)} AND count(DISTINCT y) >= {int(min_years)}
        ORDER BY theme_years DESC, theme_hits DESC
        LIMIT {int(limit)}
        """  # noqa: S608 -- the pattern comes from THEMES and the path is the local index
    ).fetchall()


def shots_of(uid: str, first: int, last: int) -> list[Shot]:
    import duckdb

    rows = duckdb.execute(
        f"SELECT photoid, datetaken, downloadurl FROM read_parquet('{index_glob()}') "  # noqa: S608 -- local index path and fixed columns, values bound
        "WHERE uid = ? AND year(datetaken) BETWEEN ? AND ?",
        [uid, first, last],
    ).fetchall()
    return [Shot(int(photoid), taken, url) for photoid, taken, url in rows]


def small_url(url: str) -> str:
    """The 240 px rendition of a Flickr static URL."""
    return url.removesuffix(".jpg") + "_m.jpg"


def _thumb(url: str) -> Image.Image | None:
    request = urllib.request.Request(small_url(url), headers={"User-Agent": USER_AGENT})  # noqa: S310 -- fixed https Flickr host
    try:
        with urllib.request.urlopen(request, timeout=30) as answer:  # noqa: S310 -- Flickr static
            if "unavailable" in answer.url:
                return None
            return Image.open(io.BytesIO(answer.read())).convert("RGB")
    except OSError:
        return None


def sheet(uid: str, first: int, last: int, out: Path, per_episode: int = 1) -> Path:
    """One tile per episode (its middle shot), labelled with date, size and photo id."""
    groups = episodes(shots_of(uid, first, last))
    picks = [(group, group[len(group) // 2]) for group in groups]
    cols, tile, label = 8, 200, 30
    rows = (len(picks) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * tile, max(rows, 1) * (tile + label)), "white")
    draw = ImageDraw.Draw(canvas)
    for index, (group, shot) in enumerate(picks):
        x, y = (index % cols) * tile, (index // cols) * (tile + label)
        image = _thumb(shot.url)
        if image is not None:
            image.thumbnail((tile - 4, tile - 4))
            canvas.paste(image, (x + 2, y + 2))
        draw.text((x + 3, y + tile), f"{shot.taken:%Y-%m-%d} n={len(group)}", fill="black")
        draw.text((x + 3, y + tile + 13), str(shot.photoid), fill="gray")
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, quality=80)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    ranked = sub.add_parser("rank")
    ranked.add_argument("--theme", choices=sorted(THEMES), required=True)
    ranked.add_argument("--min-photos", type=int, default=1500)
    ranked.add_argument("--min-years", type=int, default=3)
    ranked.add_argument("--limit", type=int, default=25)
    drawn = sub.add_parser("sheet")
    drawn.add_argument("--uid", required=True)
    drawn.add_argument("--from", dest="first", type=int, default=2000)
    drawn.add_argument("--to", dest="last", type=int, default=2014)
    drawn.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.command == "rank":
        for row in rank(args.theme, args.min_photos, args.min_years, args.limit):
            print("\t".join(str(value) for value in row))
        return 0
    out = args.out or work_root() / "sheets" / f"{args.uid.replace('@', '_')}.jpg"
    print(sheet(args.uid, args.first, args.last, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
