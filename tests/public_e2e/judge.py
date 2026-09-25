"""Judge the films of a public run: hard promises, soft metrics, sheets and a report.

Hard (the run fails): the film rendered, its recorded cut broke no promise, it plays in
capture order, it keeps no screenshot, document, blurred or dark frame, and a person film
shows only pictures of that person. Soft (reported, compared with the previous run,
never failing): counts, variety, per-person share, repeats, time. The report shows every
cut as a contact sheet and credits every picture it shows. It stays on the maintainer's
machine: a household's pictures may show children, and the snapshot is private.
"""

from __future__ import annotations

import html
import io
import json
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml
from PIL import Image, ImageDraw

from immich_memories.analysis.editorial_cut_invariants import broken_promises
from immich_memories.operations.storyboard import read_storyboard
from tests.public_e2e.films import FilmRun
from tests.public_e2e.household import Household, ManifestRow
from tests.public_e2e.stack import Admin

HARD_CLUTTER = frozenset({"clutter-screenshot", "clutter-document", "clutter-blur", "clutter-dark"})


@dataclass
class Library:
    """What the judge needs to know about the restored Immich, read once."""

    key_of: dict[str, str]
    favourite: set[str]
    manifest: dict[str, ManifestRow]
    people: dict[str, set[str]]

    @classmethod
    def read(cls, admin: Admin, rows: list[ManifestRow], household: Household) -> Library:
        key_of, favourite = {}, set()
        for asset in admin.assets():
            key_of[asset["id"]] = Path(asset["originalFileName"]).stem
            if asset.get("isFavorite"):
                favourite.add(asset["id"])
        people = {}
        for person in admin.people():
            if person.get("name") in {m.name for m in household.cast}:
                people[person["name"]] = admin.person_assets(person["id"])
        return cls(key_of, favourite, {r.key: r for r in rows}, people)


@dataclass
class Verdict:
    film: str
    args: list[str]
    status: str
    hard: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    shots: list[dict[str, Any]] = field(default_factory=list)
    video: str = ""
    sheet: str = ""
    golden: dict[str, Any] = field(default_factory=dict)


def film_seconds(video: Path) -> float:
    probe = subprocess.run(  # noqa: S603 -- fixed argv
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(video),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return round(float(probe.stdout.strip()), 1)
    except ValueError:
        return 0.0


def _people_in(args: list[str]) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args[:-1]) if a in {"--person", "-p"}]


def cut_metrics(shots: list[dict[str, Any]], library: Library) -> dict[str, Any]:
    """The soft numbers of one cut."""
    keys = [s["key"] for s in shots]
    kinds = Counter(s["kind"] for s in shots)
    derived = {library.manifest[k].derived_from for k in keys if k in library.manifest}
    twins = sum(1 for k in keys if k in derived)
    return {
        "shots": len(shots),
        "videos": kinds.get("video", 0),
        "motion_shots": sum(1 for s in shots if s["motion"]),
        "distinct_days": len({s["day"] for s in shots}),
        "favourites": sum(1 for s in shots if s["favourite"]),
        "burst_frames": kinds.get("clutter-burst", 0),
        "repeats": (len(keys) - len(set(keys))) + twins,
        "per_person": {
            name: sum(1 for s in shots if name in s["people"]) for name in library.people
        },
    }


def _golden(household_path: Path, film: str, keys: list[str]) -> dict[str, Any]:
    path = household_path / "golden" / f"{film}.yaml"
    if not path.exists():
        return {}
    golden = yaml.safe_load(path.read_text()) or {}
    keep, never = set(golden.get("must_keep", [])), set(golden.get("must_not", []))
    return {
        "verdict": golden.get("verdict", ""),
        "must_keep_recall": round(len(keep & set(keys)) / len(keep), 2) if keep else None,
        "must_not_hits": sorted(never & set(keys)),
    }


def judge_film(run: FilmRun, library: Library, household_path: Path) -> Verdict:
    args = list(run.film.args)
    verdict = Verdict(run.film.id, args, "ok")
    attempt = run.attempt_dir()
    board = read_storyboard(attempt) if attempt else None
    for shot in board.shots if board else ():
        key = library.key_of.get(shot.asset_id, "?")
        row = library.manifest.get(key)
        verdict.shots.append(
            {
                "asset_id": shot.asset_id,
                "key": key,
                "kind": row.kind if row else "?",
                "taken": shot.taken,
                "day": shot.day,
                "motion": shot.motion,
                "story": shot.story_title,
                "favourite": shot.asset_id in library.favourite,
                "people": sorted(n for n, ids in library.people.items() if shot.asset_id in ids),
            }
        )
    video = run.video()
    verdict.video = str(video) if video else ""
    if run.film.expect == "no_film":
        return _judge_no_film(verdict, run, video)
    if run.exit_code != 0:
        verdict.hard.append(f"generate exited {run.exit_code}")
    if video is None or film_seconds(video) <= 0:
        verdict.hard.append("no playable film")
    broken = broken_promises(attempt)
    if broken is None:
        verdict.hard.append("the cut recorded no invariant check")
    elif broken:
        verdict.hard.append(f"{broken} broken cut promise(s)")
    taken = [s["taken"] for s in verdict.shots]
    if taken != sorted(taken):
        verdict.hard.append("shots are not in capture order")
    clutter = [s["key"] for s in verdict.shots if s["kind"] in HARD_CLUTTER]
    if clutter:
        verdict.hard.append(f"clutter in the cut: {', '.join(clutter)}")
    for person in _people_in(args):
        missing = [s["key"] for s in verdict.shots if person not in s["people"]]
        if missing:
            verdict.hard.append(
                f"{len(missing)} shot(s) without {person}: {', '.join(missing[:5])}"
            )
    verdict.metrics = {
        **cut_metrics(verdict.shots, library),
        "wall_seconds": run.wall_seconds,
        "film_seconds": film_seconds(video) if video else 0.0,
    }
    verdict.golden = _golden(household_path, run.film.id, [s["key"] for s in verdict.shots])
    verdict.status = "FAIL" if verdict.hard else "ok"
    return verdict


NOTHING_WORTH = "Nothing worth a film in"


def _judge_no_film(verdict: Verdict, run: FilmRun, video: Path | None) -> Verdict:
    """A film whose right outcome is none: nothing rendered, and the run says why."""
    if video is not None or verdict.shots:
        verdict.hard.append(f"a film was made from {len(verdict.shots)} shot(s); none was expected")
    if run.exit_code != 0:
        verdict.hard.append(
            f"generate exited {run.exit_code} for a period with nothing worth a film"
        )
    if NOTHING_WORTH not in run.log.read_text(errors="replace"):
        verdict.hard.append("no film, but the run does not say there was nothing worth one")
    verdict.metrics = {"exit_code": run.exit_code, "wall_seconds": run.wall_seconds}
    verdict.status = "FAIL" if verdict.hard else "ok (no film, as expected)"
    return verdict


def contact_sheet(verdict: Verdict, admin: Admin, target: Path) -> Path:
    """The kept pictures in play order, marked: V video, * favourite, red for clutter."""
    tile, label, cols = 220, 34, 6
    rows = max(1, (len(verdict.shots) + cols - 1) // cols)
    canvas = Image.new("RGB", (cols * tile, rows * (tile + label)), "white")
    draw = ImageDraw.Draw(canvas)
    for index, shot in enumerate(verdict.shots):
        x, y = (index % cols) * tile, (index // cols) * (tile + label)
        try:
            raw = admin.seeder.http.get(
                f"/assets/{shot['asset_id']}/thumbnail", params={"size": "thumbnail"}
            ).content
            image = Image.open(io.BytesIO(raw)).convert("RGB")
            image.thumbnail((tile - 8, tile - 8))
            canvas.paste(image, (x + 4, y + 4))
        except OSError:
            pass
        if shot["kind"] in HARD_CLUTTER:
            draw.rectangle((x + 1, y + 1, x + tile - 2, y + tile - 2), outline="red", width=5)
        marks = ("V " if shot["kind"] == "video" else "") + ("* " if shot["favourite"] else "")
        draw.text((x + 4, y + tile), f"{index + 1}. {shot['day']} {marks}", fill="black")
        draw.text((x + 4, y + tile + 14), ",".join(shot["people"])[:34], fill="gray")
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target, quality=82)
    verdict.sheet = str(target)
    return target


def _delta(now: dict[str, Any], before: dict[str, Any] | None) -> str:
    if not before:
        return ""
    moved = [
        f"{k} {before[k]}→{v}"
        for k, v in now.items()
        if isinstance(v, int | float) and k in before and before[k] != v
    ]
    return "; ".join(moved)


def write_report(
    out: Path,
    household: Household,
    tier: str,
    verdicts: list[Verdict],
    library: Library,
    previous: dict[str, Any] | None,
    restore_seconds: float,
) -> Path:
    """index.html and results.json for one run."""
    results = {
        "household": household.name,
        "tier": tier,
        "restore_seconds": restore_seconds,
        "films": [asdict(v) for v in verdicts],
    }
    (out / "results.json").write_text(json.dumps(results, indent=1, default=str))
    before = {f["film"]: f["metrics"] for f in (previous or {}).get("films", [])}
    parts = [
        f"<h1>{html.escape(household.name)}: {tier} tier</h1>",
        f"<p>Restore {restore_seconds:.0f} s. Private report: never publish it.</p>",
    ]
    for v in verdicts:
        colour = "#c00" if v.hard else "#070"
        parts.append(f"<h2 style='color:{colour}'>{html.escape(v.film)}: {v.status}</h2>")
        parts.append(f"<p><code>{html.escape(' '.join(v.args))}</code></p>")
        if v.hard:
            parts.append("<ul>" + "".join(f"<li>{html.escape(h)}</li>" for h in v.hard) + "</ul>")
        parts.append(f"<pre>{html.escape(json.dumps(v.metrics, indent=1))}</pre>")
        change = _delta(v.metrics, before.get(v.film))
        if change:
            parts.append(f"<p>Since the previous run: {html.escape(change)}</p>")
        if v.golden:
            parts.append(f"<p>Golden: {html.escape(json.dumps(v.golden))}</p>")
        if v.sheet:
            parts.append(f"<img src='{Path(v.sheet).relative_to(out)}' style='max-width:100%'>")
        if v.video:
            parts.append(f"<p><a href='file://{html.escape(v.video)}'>the film</a></p>")
        shown = [library.manifest[s["key"]] for s in v.shots if s["key"] in library.manifest]
        credit = sorted({f"{r.creator} ({r.licence}) {r.source_page}" for r in shown if r.creator})
        parts.append(
            "<details><summary>Credits</summary><ul>"
            + "".join(f"<li>{html.escape(c)}</li>" for c in credit)
            + "</ul></details>"
        )
    page = out / "index.html"
    page.write_text(
        "<!doctype html><meta charset=utf-8><title>Public E2E</title>"
        "<body style='font-family:sans-serif;max-width:1400px;margin:auto'>" + "\n".join(parts)
    )
    return page
