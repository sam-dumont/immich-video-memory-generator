"""Fetch a household's raw material: one photographer's CC BY timeline, and CC videos.

Pictures come from CommonCatalog's index (built by `index_commoncatalog`) and the
Flickr static host at 1024 px, which needs no API key. Videos come from Wikimedia
Commons, keeping only files whose own page says CC0 or CC BY (no share-alike). Every
request sends the project's generic agent string and no personal contact.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from tests.public_e2e.household import Household
from tests.public_e2e.paths import USER_AGENT
from tests.public_e2e.timelines import index_glob

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
# Wikidata ids of the licences a household may use: CC0, CC BY 2.0 / 2.5 / 3.0 / 4.0.
COMMONS_LICENCES = ("Q6938433", "Q19125117", "Q18810333", "Q14947546", "Q20007257")
_ALLOWED_SHORT = re.compile(r"^(CC0( 1\.0)?|CC BY \d\.\d)$", re.IGNORECASE)
_FLICKR_LICENCE = re.compile(r"licenses/by/(\d\.\d)")
_UNAVAILABLE_BYTES = 10_000


@dataclass(frozen=True)
class Photo:
    """One CommonCatalog row a household uses."""

    photoid: int
    taken: datetime
    latitude: float | None
    longitude: float | None
    camera: str
    creator: str
    licence: str
    licence_url: str
    page: str
    url: str
    title: str


@dataclass(frozen=True)
class Clip:
    """One Commons video a household borrows."""

    pageid: int
    title: str
    creator: str
    licence: str
    licence_url: str
    page: str
    url: str


def _licence(url: str) -> str:
    match = _FLICKR_LICENCE.search(url or "")
    return f"CC BY {match.group(1)}" if match else "CC BY"


def _number(value: float | None) -> float | None:
    if value is None or value != value or value == 0:  # NaN marks "no geotag" in the index
        return None
    return float(value)


def timeline(household: Household) -> list[Photo]:
    """The photographer's pictures inside the household's window, minus its drop list."""
    import duckdb

    rows = duckdb.execute(
        f"SELECT photoid, datetaken, latitude, longitude, capturedevice, unickname, "  # noqa: S608 -- local index path and fixed columns, values bound
        f"licenseurl, pageurl, downloadurl, title FROM read_parquet('{index_glob()}') "
        "WHERE uid = ? AND datetaken BETWEEN ? AND ? ORDER BY datetaken, photoid",
        [household.uid, household.first_day, f"{household.last_day} 23:59:59"],
    ).fetchall()
    photos = [
        Photo(
            photoid=int(photoid),
            taken=taken,
            latitude=_number(lat),
            longitude=_number(lon),
            camera=urllib.parse.unquote_plus(device or ""),
            creator=urllib.parse.unquote_plus(nick or ""),
            licence=_licence(licence_url),
            licence_url=licence_url or "",
            page=page or "",
            url=url or "",
            title=urllib.parse.unquote_plus(title or ""),
        )
        for photoid, taken, lat, lon, device, nick, licence_url, page, url, title in rows
        if int(photoid) not in household.drop
    ]
    if len(photos) > household.max_pictures:
        step = len(photos) / household.max_pictures
        photos = [photos[int(i * step)] for i in range(household.max_pictures)]
    return photos


def large_url(url: str) -> str:
    """The 1024 px rendition of a Flickr static URL."""
    return url.removesuffix(".jpg") + "_b.jpg"


def fetch_photo(client: httpx.Client, photo: Photo, target: Path) -> bool:
    """Download one picture; False when Flickr no longer has it."""
    if target.exists():
        return True
    answer = client.get(large_url(photo.url).replace("http://", "https://"))
    if answer.status_code != 200 or "unavailable" in str(answer.url):
        return False
    if len(answer.content) < _UNAVAILABLE_BYTES:
        return False
    target.write_bytes(answer.content)
    return True


def fetch_photos(photos: list[Photo], folder: Path) -> list[Photo]:
    """Download every picture that is still online; return the ones that arrived."""
    folder.mkdir(parents=True, exist_ok=True)
    with (
        httpx.Client(
            headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=60
        ) as client,
        ThreadPoolExecutor(8) as pool,
    ):
        arrived = list(
            pool.map(lambda p: fetch_photo(client, p, folder / f"cc-{p.photoid}.jpg"), photos)
        )
    return [photo for photo, ok in zip(photos, arrived, strict=True) if ok]


def _plain(text: str) -> str:
    """One line of plain text, at most 120 characters: an artist field can hold a whole crew list."""
    flat = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text or "")).split())
    return flat if len(flat) <= 120 else flat[:117] + "..."


def _clip(page: dict) -> Clip | None:
    info = (page.get("imageinfo") or [{}])[0]
    meta = info.get("extmetadata", {})
    short = meta.get("LicenseShortName", {}).get("value", "").strip()
    if not _ALLOWED_SHORT.match(short) or info.get("size", 0) > 400e6:
        return None
    artist = meta.get("Artist", {}).get("value", "")
    creator = _plain(artist)
    if re.fullmatch(r"\[\d+\]", creator):  # only a footnote marker: credit the linked account
        link = re.search(r'href="https?://(?:www\.)?([^"]+?)/?"', artist)
        creator = link.group(1) if link else ""
    return Clip(
        pageid=int(page["pageid"]),
        title=page["title"],
        creator=creator or "unknown",
        licence=short.upper().replace("CC0 1.0", "CC0"),
        licence_url=meta.get("LicenseUrl", {}).get("value", ""),
        page=info.get("descriptionurl", ""),
        url=info["url"],
    )


def commons_videos_by_id(pageids: list[int]) -> list[Clip]:
    """The picked Commons videos, in the order given; a file whose licence no longer fits is left out."""
    clips: dict[int, Clip] = {}
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60) as client:
        for start in range(0, len(pageids), 40):
            answer = client.get(
                COMMONS_API,
                params={
                    "action": "query",
                    "format": "json",
                    "prop": "imageinfo",
                    "iiprop": "url|extmetadata|size|mime",
                    "pageids": "|".join(str(p) for p in pageids[start : start + 40]),
                },
            ).json()
            for page in answer.get("query", {}).get("pages", {}).values():
                clip = _clip(page)
                if clip is not None:
                    clips[clip.pageid] = clip
    return [clips[p] for p in pageids if p in clips]


def commons_videos(query: str, limit: int) -> list[Clip]:
    """Commons videos matching `query` whose own metadata says CC0 or CC BY."""
    statements = "|".join(f"P275={q}" for q in COMMONS_LICENCES)
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrnamespace": 6,
        "gsrlimit": 50,
        "gsrsearch": f"{query} filetype:video haswbstatement:{statements}",
        "prop": "imageinfo",
        "iiprop": "url|extmetadata|size|mime",
    }
    clips: list[Clip] = []
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60) as client:
        cont: dict[str, str] = {}
        while len(clips) < limit:
            answer = client.get(COMMONS_API, params={**params, **cont}).json()
            pages = answer.get("query", {}).get("pages", {}).values()
            for page in sorted(pages, key=lambda p: p.get("index", 0)):
                clip = _clip(page)
                if clip is not None:
                    clips.append(clip)
            if "continue" not in answer:
                break
            cont = answer["continue"]
    return clips[:limit]


def fetch_clip(clip: Clip, raw: Path, target: Path, seconds: float = 20.0) -> bool:
    """Download a Commons video and re-encode it as a phone-like H.264 MP4, at most `seconds`."""
    if target.exists():
        return True
    if not raw.exists():
        with httpx.stream(
            "GET", clip.url, headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=300
        ) as answer:
            if answer.status_code != 200:
                return False
            with raw.open("wb") as handle:
                for chunk in answer.iter_bytes():
                    handle.write(chunk)
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        str(raw),
        "-t",
        str(seconds),
        "-vf",
        "scale='min(1920,iw)':-2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(target),
    ]
    done = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
    if done.returncode != 0:
        target.unlink(missing_ok=True)
        return False
    return True


def clip_json(clips: list[Clip]) -> str:
    return json.dumps([clip.__dict__ for clip in clips], indent=1)


if __name__ == "__main__":
    # Candidates to pick a household's videos from by eye:
    #   uv run python -m tests.public_e2e.sources "beach waves" 20
    import sys

    for found in commons_videos(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 20):
        print(found.pageid, found.licence, found.title, "|", found.creator, sep="\t")
