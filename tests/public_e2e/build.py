"""Build a household once: fetch, stamp, load into Immich with ML, name the cast, snapshot.

    uv run --with duckdb python -m tests.public_e2e.build dog-owner [--steps prepare,load,snapshot]

`prepare` writes the camera roll and the public manifest and credits. `load` starts a
pinned Immich with machine learning, uploads the roll, waits for faces, places and
embeddings, names the cast from its seed pictures and writes `people.yaml`. `snapshot`
dumps the database and tars the library into the private snapshot folder and writes
`snapshot.lock`. The snapshot is never published; see the design doc.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import subprocess
import sys
import time
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from PIL import Image

from tests.public_e2e import sources, stamping
from tests.public_e2e.household import (
    Household,
    ManifestRow,
    credits_markdown,
    home_point,
    read_manifest,
    shift_years,
    snap_home,
    write_manifest,
)
from tests.public_e2e.paths import household_dir, household_work
from tests.public_e2e.stack import IMAGES, POSTGRES_IMAGE, Admin, Stack, admin_api_key
from tests.public_e2e.timelines import Shot, episodes

BUILD_PORT = 2297
PART_BYTES = 1900 * 1024 * 1024
_DEFAULT_TITLE = re.compile(
    r"^(img|dsc|dscn|dscf|p\d|pict|imgp|_mg|mvi|sam|photo)[_\-\s]?\d+", re.I
)
_CLUTTER_SHARES = {"screenshot": 0.05, "document": 0.02, "burst": 0.03, "blur": 0.02, "dark": 0.02}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


def _places(photos: list[sources.Photo]) -> dict[int, tuple[float, float] | None]:
    """Each picture's place: its own geotag, else the nearest one within six hours (scripted)."""
    tagged = [(p.taken, (p.latitude, p.longitude)) for p in photos if p.latitude is not None]
    places: dict[int, tuple[float, float] | None] = {}
    for photo in photos:
        if photo.latitude is not None and photo.longitude is not None:
            places[photo.photoid] = (photo.latitude, photo.longitude)
            continue
        near = min(tagged, key=lambda t: abs(t[0] - photo.taken), default=None)
        ok = near is not None and abs(near[0] - photo.taken) <= timedelta(hours=6)
        places[photo.photoid] = near[1] if ok and near else None  # type: ignore[assignment]
    return places


def _favourite(photo: sources.Photo, rng: random.Random) -> bool:
    """A photographer's effort as a keeper signal: a picture they bothered to title."""
    title = photo.title.strip()
    return bool(title) and not _DEFAULT_TITLE.match(title) and rng.random() < 0.08


def _photo_row(
    photo: sources.Photo, file: Path, taken: datetime, place, scripted: str
) -> ManifestRow:
    return ManifestRow(
        key=file.stem,
        file=file.name,
        kind="photo",
        source="commoncatalog",
        source_id=str(photo.photoid),
        source_page=photo.page,
        creator=photo.creator,
        licence=photo.licence,
        licence_url=photo.licence_url,
        taken_at=_iso(taken),
        latitude=f"{place[0]:.4f}" if place else "",
        longitude=f"{place[1]:.4f}" if place else "",
        scripted=scripted,
    )


def prepare(household: Household) -> list[ManifestRow]:
    """Fetch and stamp the roll into the work folder; write the public manifest and credits."""
    work = household_work(household.name)
    library = work / "library"
    library.mkdir(exist_ok=True)
    rng = random.Random(household.seed)
    photos = sources.fetch_photos(sources.timeline(household), work / "fetched")
    raw_places = _places(photos)
    home = home_point(p for p in raw_places.values() if p)
    rows: list[ManifestRow] = []
    favourites: set[str] = set()
    cameras: dict[str, tuple[str, str]] = {}
    for photo in photos:
        taken = shift_years(photo.taken, household.date_shift_years)
        own = photo.latitude is not None
        place = snap_home(raw_places[photo.photoid], home)
        scripted = ["taken_at", "camera"] + (["place"] if not own or place == home else [])
        target = library / f"cc-{photo.photoid}.jpg"
        cameras[target.stem] = stamping.camera_of(photo.camera)
        with Image.open(work / "fetched" / target.name) as image:
            stamping.write_photo(image, target, taken, cameras[target.stem], place)
        rows.append(_photo_row(photo, target, taken, place, ",".join(scripted)))
        if _favourite(photo, rng):
            favourites.add(target.stem)
    rows += _clutter(rows, cameras, library, rng)
    rows += _videos(household, rows, library, work, rng)
    rows = _unique([replace(row, sha256=_sha256(library / row.file)) for row in rows])
    out = household_dir(household.name)
    write_manifest(out / "manifest.csv", rows)
    (out / "CREDITS.md").write_text(credits_markdown(household, rows))
    (work / "favourites.json").write_text(json.dumps(sorted(favourites)))
    (work / "home.json").write_text(json.dumps(home))
    return rows


def _unique(rows: list[ManifestRow]) -> list[ManifestRow]:
    """One row per distinct file: a photographer who uploaded a picture twice gets it once.

    Immich keeps one asset per checksum, so a second identical file would be a manifest row
    with no asset of its own.
    """
    seen: set[str] = set()
    kept = []
    for row in sorted(rows, key=lambda r: (r.taken_at, r.key)):
        if row.sha256 not in seen:
            seen.add(row.sha256)
            kept.append(row)
    return kept


def _clutter(
    rows: list[ManifestRow],
    cameras: dict[str, tuple[str, str]],
    library: Path,
    rng: random.Random,
) -> list[ManifestRow]:
    """The share of a real roll that is not a picture anyone would keep."""
    photos = [r for r in rows if r.kind == "photo"]
    made: list[ManifestRow] = []
    for kind, share in _CLUTTER_SHARES.items():
        for index in range(round(len(photos) * share)):
            base = rng.choice(photos)
            moment = stamping.jitter(datetime.fromisoformat(base.taken_at), rng, 1, 3 * 3600)
            place = (float(base.latitude), float(base.longitude)) if base.latitude else None
            key = f"syn-{kind}-{index:04d}"
            if kind == "screenshot":
                target = library / f"{key}.png"
                stamping.screenshot(rng).save(target)
                row = ManifestRow(
                    key,
                    target.name,
                    f"clutter-{kind}",
                    "synthetic",
                    "",
                    "",
                    "",
                    "none (rendered)",
                    "",
                    _iso(moment),
                    scripted="all",
                )
                made.append(row)
                continue
            target = library / f"{key}.jpg"
            if kind == "document":
                image = stamping.document(rng)
                licence, derived, creator = "none (rendered)", "", ""
            else:
                with Image.open(library / base.file) as source:
                    make = {
                        "burst": stamping.burst_frame,
                        "blur": stamping.blurred,
                        "dark": stamping.dark,
                    }[kind]
                    image = make(source.convert("RGB"), rng)
                moment = stamping.jitter(datetime.fromisoformat(base.taken_at), rng, 0.4, 4)
                licence, derived, creator = base.licence, base.key, base.creator
            camera = stamping.PHONE if kind == "document" else cameras[base.key]
            stamping.write_photo(image, target, moment, camera, place)
            made.append(
                ManifestRow(
                    key,
                    target.name,
                    f"clutter-{kind}",
                    "synthetic",
                    "",
                    "",
                    creator,
                    licence,
                    "",
                    _iso(moment),
                    latitude=base.latitude if place else "",
                    longitude=base.longitude if place else "",
                    derived_from=derived,
                    scripted="all",
                )
            )
    return made


def _videos(
    household: Household, rows: list[ManifestRow], library: Path, work: Path, rng: random.Random
) -> list[ManifestRow]:
    """Borrowed CC videos, each dropped into an episode of the household's own days."""
    if not household.videos:
        return []
    clips = sources.commons_videos_by_id(list(household.videos))
    (work / "clips.json").write_text(sources.clip_json(clips))
    photos = [r for r in rows if r.kind == "photo"]
    shots = [Shot(i, datetime.fromisoformat(r.taken_at), "") for i, r in enumerate(photos)]
    groups = [g for g in episodes(shots) if len(g) >= 5]
    raw = work / "clips"
    raw.mkdir(exist_ok=True)
    made: list[ManifestRow] = []
    for clip in clips:
        if not groups:
            break
        encoded = raw / f"cm-{clip.pageid}.mp4"
        suffix = Path(clip.url).suffix or ".bin"
        if not sources.fetch_clip(clip, raw / f"cm-{clip.pageid}{suffix}", encoded):
            continue
        anchor = photos[rng.choice(rng.choice(groups)).photoid]
        moment = stamping.jitter(datetime.fromisoformat(anchor.taken_at), rng, 30, 600)
        place = (float(anchor.latitude), float(anchor.longitude)) if anchor.latitude else None
        target = library / encoded.name
        stamping.stamp_video(encoded, target, moment, place)
        made.append(
            ManifestRow(
                target.stem,
                target.name,
                "video",
                "commons",
                str(clip.pageid),
                clip.page,
                clip.creator,
                clip.licence,
                clip.licence_url,
                _iso(moment),
                latitude=anchor.latitude if place else "",
                longitude=anchor.longitude if place else "",
                scripted="taken_at,place",
            )
        )
    return made


def load(household: Household) -> dict:
    """Upload the roll into a fresh Immich with ML and wait until faces and places exist."""
    work = household_work(household.name)
    rows = read_manifest(household_dir(household.name) / "manifest.csv")
    favourites = set(json.loads((work / "favourites.json").read_text()))
    stack = Stack(f"public-e2e-build-{household.name}", BUILD_PORT, ml=True)
    started = time.monotonic()
    stack.fresh()
    api_key = admin_api_key(stack.url)
    admin = Admin(stack.url, api_key)
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(6) as pool:
        ids = list(
            pool.map(
                lambda r: admin.upload(
                    work / "library" / r.file, r.taken_at + "Z", favourite=r.key in favourites
                ),
                rows,
            )
        )
    uploaded = time.monotonic()
    admin.wait_for_queues(4 * 3600)
    for _round in range(2):  # the second round clusters faces the first one deferred
        admin.call("PUT", "/jobs/facialRecognition", json={"command": "start", "force": False})
        time.sleep(10)
        admin.wait_for_queues(3600)
    (work / "assets.json").write_text(
        json.dumps(dict(zip((r.key for r in rows), ids, strict=True)), indent=0)
    )
    state = {
        "api_key": api_key,
        "assets": len(ids),
        "upload_seconds": round(uploaded - started),
        "ml_seconds": round(time.monotonic() - uploaded),
    }
    (work / "load.json").write_text(json.dumps(state, indent=1))
    return state


def name(household: Household) -> dict:
    """Name the cast in the loaded Immich from the seed pictures in `household.yaml`."""
    work = household_work(household.name)
    state = json.loads((work / "load.json").read_text())
    admin = Admin(
        Stack(f"public-e2e-build-{household.name}", BUILD_PORT, ml=True).url, state["api_key"]
    )
    state["named"] = name_cast(admin, household, json.loads((work / "assets.json").read_text()))
    state["people_clusters"] = len(admin.people())
    (work / "load.json").write_text(json.dumps(state, indent=1))
    return state["named"]


def clusters(household: Household, top: int = 12) -> list[dict]:
    """The biggest unnamed face clusters, with a few of their pictures, to pick the cast by eye."""
    work = household_work(household.name)
    state = json.loads((work / "load.json").read_text())
    admin = Admin(
        Stack(f"public-e2e-build-{household.name}", BUILD_PORT, ml=True).url, state["api_key"]
    )
    key_of = {v: k for k, v in json.loads((work / "assets.json").read_text()).items()}
    found = []
    for person in admin.people():
        assets = admin.person_assets(person["id"])
        found.append(
            {
                "id": person["id"],
                "name": person.get("name", ""),
                "count": len(assets),
                "keys": sorted(key_of.get(a, "?") for a in assets),
            }
        )
    found.sort(key=lambda c: -c["count"])
    (work / "clusters.json").write_text(json.dumps(found[:top], indent=1))
    return found[:top]


def name_cast(admin: Admin, household: Household, asset_of: dict[str, str]) -> dict[str, dict]:
    """Name each cast member's face cluster: the one that shows up in most of their seeds.

    Seeds are pictures picked by eye from that person's cluster in an earlier load; a vote
    over every face in them survives a cluster id changing between builds and a seed that
    also shows somebody else.
    """
    named: dict[str, dict] = {}
    for member in household.cast:
        votes: Counter[str] = Counter()
        for photoid in member.seeds:
            asset = asset_of.get(f"cc-{photoid}")
            faces = admin.faces(asset) if asset else []
            votes.update({str(f["person"]["id"]) for f in faces if f.get("person")})
        if not votes:
            named[member.name] = {"person_id": None, "seeds_matched": 0}
            continue
        person, matched = votes.most_common(1)[0]
        admin.call("PUT", f"/people/{person}", json={"name": member.name})
        named[member.name] = {
            "person_id": person,
            "seeds_matched": f"{matched}/{len(member.seeds)}",
            "assets": len(admin.person_assets(person)),
        }
    return named


def write_people_file(household: Household, url: str, api_key: str, home: Path) -> Path:
    """Run the product's own people scan, then confirm the script's relations."""
    from tests.public_e2e.films import CLI, run_env, write_config

    config = write_config(home, url, api_key, household_work(household.name) / "home.json")
    people = home / ".immich-memories" / "people.yaml"
    subprocess.run(  # noqa: S603 -- our own CLI
        [
            *CLI,
            "--config",
            str(config),
            "people",
            "scan",
            "--owner",
            household.owner,
            "--out",
            str(people),
        ],
        check=True,
        env=run_env(home),
    )
    from immich_memories.people.companion import (
        load_document,
        people_entries,
        save_confirmed_relationship,
    )

    ids = {e["name"]: str(e["ids"][0]) for e in people_entries(load_document(people))}
    for source, kind, target in household.relations:
        if source in ids and target in ids:
            save_confirmed_relationship(people, ids[source], kind, ids[target])
    return people


def snapshot(household: Household) -> Path:
    """Dump the database, tar the library, and write `snapshot.lock` (the public half)."""
    work = household_work(household.name)
    state = json.loads((work / "load.json").read_text())
    stack = Stack(f"public-e2e-build-{household.name}", BUILD_PORT, ml=True)
    stack.compose("up", "-d", "--wait", "--wait-timeout", "600")  # a re-run finds it stopped
    people = write_people_file(household, stack.url, state["api_key"], work / "people-home")
    out = work / "snapshot"
    if out.exists():
        for old in out.iterdir():
            old.unlink()
    out.mkdir(exist_ok=True)
    stack.compose("stop", "immich-server", "immich-machine-learning")
    stack.dump_database(out / "db.sql.gz")
    stack.save_library(out / "library.tar.part-", PART_BYTES)
    for name in ("people.yaml", "people-graph.json"):
        (out / name).write_bytes((people.parent / name).read_bytes())
    parts = sorted(p for p in out.iterdir() if p.is_file())
    lock = {
        "household": household.name,
        "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        ).stdout.strip(),
        "release": {
            "repo": "sam-dumont/immich-memories-ci",
            "tag": f"public-e2e-{household.name}-{datetime.now():%Y%m%d}",
        },
        "images": {
            **{k.removeprefix("PUBLIC_E2E_").lower(): v for k, v in IMAGES.items()},
            "postgres": POSTGRES_IMAGE,
        },
        "owner": household.owner,
        "home": json.loads((work / "home.json").read_text()),
        "counts": {
            "assets": state["assets"],
            "people_clusters": state["people_clusters"],
            "named": state["named"],
        },
        "timings_seconds": {"upload": state["upload_seconds"], "ml": state["ml_seconds"]},
        "parts": [{"name": p.name, "bytes": p.stat().st_size, "sha256": _sha256(p)} for p in parts],
        "total_bytes": sum(p.stat().st_size for p in parts),
    }
    target = household_dir(household.name) / "snapshot.lock"
    target.write_text(yaml.safe_dump(lock, sort_keys=False))
    stack.down(volumes=False)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("household")
    parser.add_argument("--steps", default="prepare,load,name,snapshot")
    args = parser.parse_args(argv)
    household = Household.load(household_dir(args.household) / "household.yaml")
    for step in args.steps.split(","):
        started = time.monotonic()
        steps = {
            "prepare": prepare,
            "load": load,
            "clusters": clusters,
            "name": name,
            "snapshot": snapshot,
        }
        result = steps[step](household)
        if isinstance(result, dict):
            result = {k: v for k, v in result.items() if k != "api_key"}
        summary = f"{len(result)} rows" if isinstance(result, list) else result
        print(f"{step}: {summary} ({time.monotonic() - started:.0f}s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
