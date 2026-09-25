"""Restore a household snapshot into a fresh Immich, run its films, judge them.

    uv run python -m tests.public_e2e.run dog-owner [--tier rules|model] [--films id,id] [--keep]

The snapshot comes from the maintainer's work folder, or from the private release the
lock names (`gh release download`, needs access to the private CI mirror). Every part is
checked against the lock's SHA-256 before anything is restored. Exit status 1 when a
film breaks a hard promise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

from tests.public_e2e.films import run_film
from tests.public_e2e.household import Household, read_manifest
from tests.public_e2e.judge import Library, contact_sheet, judge_film, write_report
from tests.public_e2e.paths import household_dir, household_work
from tests.public_e2e.stack import Admin, Stack, admin_api_key

RUN_PORT = 2296


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_parts(lock: dict, folder: Path) -> list[Path]:
    """The lock's parts, downloaded if missing, each one checked against its hash."""
    folder.mkdir(parents=True, exist_ok=True)
    missing = [p["name"] for p in lock["parts"] if not (folder / p["name"]).exists()]
    if missing:
        release = lock["release"]
        subprocess.run(  # noqa: S603 -- fixed argv
            [
                "gh",
                "release",
                "download",
                release["tag"],
                "-R",
                release["repo"],
                "-D",
                str(folder),
                *sum((["-p", name] for name in missing), []),
            ],
            check=True,
        )
    parts = []
    for part in lock["parts"]:
        path = folder / part["name"]
        if _sha256(path) != part["sha256"]:
            raise SystemExit(f"{path} does not match snapshot.lock; delete it and run again")
        parts.append(path)
    return parts


def restore(stack: Stack, parts: list[Path]) -> str:
    """A fresh database and library volume, filled from the snapshot; the server without ML."""
    stack.fresh("database", "redis")
    stack.restore_database(next(p for p in parts if p.name == "db.sql.gz"))
    stack.load_library(sorted(p for p in parts if p.name.startswith("library.tar.part-")))
    stack.compose("up", "-d", "--wait", "--wait-timeout", "600", "immich-server")
    return admin_api_key(stack.url, sign_up=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("household")
    parser.add_argument("--tier", choices=["rules", "model"], default="rules")
    parser.add_argument("--films", default="")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--target", choices=["snapshot", "test-immich"], default="snapshot")
    args = parser.parse_args(argv)
    source = household_dir(args.household)
    household = Household.load(source / "household.yaml")
    lock = yaml.safe_load((source / "snapshot.lock").read_text())
    work = household_work(household.name)
    stack: Stack | None = None
    if args.target == "test-immich":
        # The shared test Immich: no restore, the household's own user and scoped key.
        from tests.public_e2e.provision import load_secrets

        store = load_secrets()
        entry = store.get("households", {}).get(household.name, {})
        if not entry.get("api_key") or not entry.get("people_file"):
            raise SystemExit(f"{household.name} is not provisioned: run make public-e2e-provision")
        url, api_key, people = store["url"], entry["api_key"], Path(entry["people_file"])
        restore_seconds = 0.0
    else:
        parts = snapshot_parts(lock, work / "snapshot")
        stack = Stack(f"public-e2e-run-{household.name}", RUN_PORT)
        started = time.monotonic()
        api_key = restore(stack, parts)
        url, people = stack.url, work / "snapshot" / "people.yaml"
        restore_seconds = round(time.monotonic() - started, 1)
        print(f"restored {household.name} in {restore_seconds:.0f}s", flush=True)
    out = work / "runs" / f"{datetime.now():%Y%m%dT%H%M%S}-{args.tier}-{args.target}"
    out.mkdir(parents=True)
    home_json = out / "home.json"
    home_json.write_text(json.dumps(lock.get("home")))
    wanted = set(filter(None, args.films.split(",")))
    films = [f for f in household.films if not wanted or f.id in wanted]
    admin = Admin(url, api_key)
    library = Library.read(admin, read_manifest(source / "manifest.csv"), household)
    verdicts = []
    for film in films:
        run = run_film(film, out, url, api_key, home_json, people, args.tier)
        verdict = judge_film(run, library, source)
        contact_sheet(verdict, admin, out / "sheets" / f"{film.id}.jpg")
        verdicts.append(verdict)
        print(
            f"{film.id}: {verdict.status} {run.wall_seconds:.0f}s "
            f"{verdict.metrics.get('shots', 0)} shots {'; '.join(verdict.hard)}",
            flush=True,
        )
    runs = sorted(p for p in (work / "runs").glob(f"*-{args.tier}-{args.target}") if p != out)
    previous_file = runs[-1] / "results.json" if runs else None
    previous = (
        json.loads(previous_file.read_text()) if previous_file and previous_file.exists() else None
    )
    page = write_report(out, household, args.tier, verdicts, library, previous, restore_seconds)
    print(f"report: {page}")
    if stack is not None and not args.keep:
        stack.down(volumes=True)
    return 1 if any(v.hard for v in verdicts) else 0


if __name__ == "__main__":
    sys.exit(main())
