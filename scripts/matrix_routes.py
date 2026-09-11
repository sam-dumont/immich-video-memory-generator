#!/usr/bin/env python3
"""Render a set of memory routes through the public CLI and verify what comes out.

The routes themselves are public (`examples/matrix-routes.example.json`): a memory
type, a duration, a bundled loop, and a scope whose values are `@placeholders`.
Everything that would name a person, an album, a place or a day lives in a private
overlay outside the repository and is only ever joined in at `build` time.

    python scripts/matrix_routes.py build   --routes examples/matrix-routes.example.json \\
                                            --private ~/.immich-memories-matrix/routes.private.json \\
                                            --manifest ~/.immich-memories-matrix/manifest.private.json
    python scripts/matrix_routes.py run     --manifest ~/.immich-memories-matrix/manifest.private.json
    python scripts/matrix_routes.py collect --manifest ~/.immich-memories-matrix/manifest.private.json
    python scripts/matrix_routes.py report  --manifest ~/.immich-memories-matrix/manifest.private.json

`run` is serial and resumable: the manifest is flock'd, the frozen config is hash
checked before every case, and a case that already produced a film is skipped once
its recorded digest still matches. `collect` is the part that decides whether a
film is watchable — it never trusts an exit code on its own.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from matrix_routes_report import console_table, markdown_report, report_rows  # noqa: E402

PUBLIC_SCHEMA = "matrix-routes-v1"
PRIVATE_SCHEMA = "matrix-routes-private-v1"
MANIFEST_SCHEMA = "matrix-routes-manifest-v1"

# A full render keeps its intermediates until FFmpeg is done with them; 15 GiB was
# enough for the 60 s probe films and not for a 180 s year. The overlay may lower it
# (`min_free_gib`) on a machine whose free space is known and watched.
DEFAULT_FREE_DISK_FLOOR_GIB = 50

ALBUM_MEMORY_TYPE = "album"

# Every scope key a route may carry, and the `generate` flag it becomes. Validated
# against the live Click tree at build time, so a renamed flag fails here instead
# of reaching the owner as a mysteriously empty film.
SCOPE_OPTIONS = {
    "year": "--year",
    "month": "--month",
    "person": "--person",
    "expression": "--people-expression",
    "person_match": "--person-match",
    "day": "--day",
    "event_id": "--event-id",
    "trip_index": "--trip-index",
    "near_date": "--near-date",
    "season": "--season",
    "hemisphere": "--hemisphere",
    "holiday": "--holiday",
    "years_back": "--years-back",
    "album": "--from-album",
}

# `on_this_day` is scoped to whatever today is, so it is the one route that can
# never replay: every run asks a different question and needs live calls. A route
# may pin it to a fixed day instead, which makes the case reproducible and lets it
# replay off the bank like the other ten. `generate` accepts that date only from
# the automation runner, and only with the whole identity that runner carries, so
# the pin expands into all four flags rather than one.
ON_THIS_DAY_DATE = "target_date"

# The shape every route shares, so the only thing that varies between films is the
# route: same captions, same resolution, same codec, same silence about progress.
FIXED_FLAGS = ("--include-photos", "--include-live-photos", "--add-date", "--add-place", "--quiet")
FIXED_SETTINGS = (("--resolution", "1080p"), ("--format", "h265"))

RESUMABLE_STATUSES = frozenset({"rendered", "ready"})


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def write_private(path: Path, text: str) -> None:
    """Write owner-only text atomically, never leaving a world-readable window."""
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.touch(mode=0o600)
    temporary.write_text(text)
    temporary.replace(path)


def save_private(path: Path, record: Mapping[str, Any]) -> None:
    write_private(path, json.dumps(record, indent=2, ensure_ascii=False) + "\n")


def generate_command() -> Any:
    """The real `generate` command, so the flags below cannot drift from the CLI.

    `generate_options.py` declares nearly all of them, but `--quiet` is applied on
    the command itself. Registering the command is the only reading that sees every
    flag the driver actually passes.
    """
    import click

    from immich_memories.cli.generate import register_generate_commands

    group = click.Group("immich-memories")
    register_generate_commands(group)
    return group.commands["generate"]


def generate_option_flags() -> set[str]:
    return {
        flag for param in generate_command().params for flag in (*param.opts, *param.secondary_opts)
    }


def memory_type_choices() -> set[str]:
    """The `--memory-type` values Click will accept, plus album mode's virtual one."""
    memory_type = next(p for p in generate_command().params if "--memory-type" in p.opts)
    return {*memory_type.type.choices, ALBUM_MEMORY_TYPE}


def resolve_scope(route: Mapping[str, Any], values: Mapping[str, Any]) -> dict[str, Any]:
    """Join the public route's `@placeholders` with the private overlay's values.

    A placeholder with no value is a hard error. Silently dropping it would render
    a different memory than the one the route names, and the owner would grade the
    wrong film without ever being told.
    """
    resolved: dict[str, Any] = {}
    for key, raw in (route.get("scope") or {}).items():
        if key not in SCOPE_OPTIONS and key != ON_THIS_DAY_DATE:
            raise RuntimeError(f"{route['id']}: unknown scope key {key!r}")
        if not (isinstance(raw, str) and raw.startswith("@")):
            resolved[key] = raw
            continue
        name = raw[1:]
        if name not in values or values[name] is None:
            raise RuntimeError(f"{route['id']}: private overlay has no value for {raw}")
        resolved[key] = values[name]
    return resolved


def _pinned_day_args(route: Mapping[str, Any], day: str) -> list[str]:
    """The four flags that pin `on_this_day` to a chosen date.

    The memory key is the one `automation/candidates.make_memory_key` builds for
    that day, because `generate` only trusts the date when the rest of the
    automation identity agrees with it.
    """
    if route["memory_type"] != "on_this_day":
        raise RuntimeError(f"{route['id']}: {ON_THIS_DAY_DATE} only applies to on_this_day")
    datetime.strptime(day, "%Y-%m-%d")  # noqa: DTZ007 - a date, not a moment
    return [
        "--source",
        "auto",
        "--memory-category",
        "on_this_day",
        "--memory-key",
        f"on_this_day:{day}:{day}:",
        "--automation-target-date",
        day,
    ]


def generate_args(
    route: Mapping[str, Any],
    scope: Mapping[str, Any],
    *,
    music: Path,
    output: Path,
    album_name: str | None,
    upload: bool,
) -> list[str]:
    """The `generate` argument list for one route, fixed flags included."""
    args: list[str] = []
    if route["memory_type"] != ALBUM_MEMORY_TYPE:
        args += ["--memory-type", route["memory_type"]]
    elif "album" not in scope:
        raise RuntimeError(f"{route['id']}: album routes need an `album` scope value")
    for key, value in scope.items():
        if key == ON_THIS_DAY_DATE:
            args += _pinned_day_args(route, str(value))
            continue
        option = SCOPE_OPTIONS[key]
        for item in value if isinstance(value, list) else [value]:
            args += [option, str(item)]
    if route.get("duration_seconds") is not None:
        args += ["--duration", str(route["duration_seconds"])]
    args += list(FIXED_FLAGS)
    for option, value in FIXED_SETTINGS:
        args += [option, value]
    args += ["--music", str(music), "--output", str(output)]
    if upload:
        if not album_name:
            raise RuntimeError(f"{route['id']}: upload requested with no album_name")
        args += ["--upload-to-immich", "--album", album_name]
    return args


def assert_options_exist(args: Sequence[str], known: set[str]) -> None:
    unknown = sorted({token for token in args if token.startswith("--")} - known)
    if unknown:
        raise RuntimeError(f"`generate` has no such option(s): {', '.join(unknown)}")


def bundled_music(name: str) -> Path:
    """Resolve a bundled loop name against the installed music package."""
    try:
        from immich_memories_music import tracks_dir
    except ImportError:
        raise RuntimeError("The bundled music package is not installed (`music` extra)")
    path = tracks_dir() / name
    if not path.is_file():
        raise RuntimeError(f"No bundled loop named {name!r}")
    return path


def _without_output(args: Sequence[str]) -> list[str]:
    trimmed = list(args)
    index = trimmed.index("--output")
    del trimmed[index : index + 2]
    return trimmed


def assert_same_recipe(case: Mapping[str, Any], target: Mapping[str, Any] | None) -> None:
    """A supersede route only tests supersede if it really is the same recipe.

    `supersede_previous_renders` matches on the uploaded file's name, and with
    `--output` set that name is ours — so the re-run reuses the first route's film
    name. Everything else about the two calls has to be identical, or the second
    upload would trash a film of a different memory.
    """
    if target is None:
        raise RuntimeError(f"{case['id']}: supersedes a route that is not in this file")
    if _without_output(case["args"]) != _without_output(target["args"]):
        raise RuntimeError(f"{case['id']}: differs from {target['id']!r}; it cannot supersede it")


def build_case(
    route: Mapping[str, Any],
    private: Mapping[str, Any],
    *,
    known: set[str],
    types: set[str],
) -> dict[str, Any]:
    route_id = route["id"]
    if route["memory_type"] not in types:
        raise RuntimeError(f"{route_id}: unknown memory type {route['memory_type']!r}")
    values = private.get("values") or {}
    # A supersede route repeats another route's recipe, so it inherits its values.
    scope_values = values.get(route_id) or values.get(route.get("supersedes")) or {}
    stem = route.get("supersedes") or route_id
    directory = Path(private["output_root"]).expanduser() / route_id
    args = generate_args(
        route,
        resolve_scope(route, scope_values),
        music=bundled_music(route["music"]),
        output=directory / f"{stem}.mp4",
        album_name=private.get("album_name"),
        upload=bool(private.get("upload")),
    )
    assert_options_exist(args, known)
    return {
        "id": route_id,
        "memory_type": route["memory_type"],
        "status": "pending",
        "output_directory": str(directory),
        "output_stem": stem,
        "music": route["music"],
        "args": args,
    }


def build(routes_path: Path, private_path: Path, manifest_path: Path, cli: str) -> dict[str, Any]:
    """Render every route into a runnable case, with private values joined in."""
    public = json.loads(routes_path.read_text())
    if public.get("schema") != PUBLIC_SCHEMA:
        raise RuntimeError(f"{routes_path} is not a {PUBLIC_SCHEMA} file")
    private = json.loads(private_path.read_text())
    if private.get("schema") != PRIVATE_SCHEMA:
        raise RuntimeError(f"{private_path} is not a {PRIVATE_SCHEMA} file")

    known = generate_option_flags()
    types = memory_type_choices()
    cases: list[dict[str, Any]] = []
    for route in public["routes"]:
        case = build_case(route, private, known=known, types=types)
        if route.get("supersedes"):
            assert_same_recipe(
                case, next((c for c in cases if c["id"] == route["supersedes"]), None)
            )
        cases.append(case)

    config_path = Path(private["config_path"]).expanduser()
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "built_at": now(),
        "cli": cli,
        "config_path": str(config_path),
        "config_sha256": sha256(config_path),
        "database_path": str(Path(private["database_path"]).expanduser()),
        "editorial_runs": str(Path(private["editorial_runs"]).expanduser()),
        "output_root": str(Path(private["output_root"]).expanduser()),
        "album_name": private.get("album_name"),
        "upload": bool(private.get("upload")),
        "ffmpeg": private.get("ffmpeg", "ffmpeg"),
        "ffprobe": private.get("ffprobe", "ffprobe"),
        "reference": private.get("reference") or {},
        "min_free_gib": float(private.get("min_free_gib", DEFAULT_FREE_DISK_FLOOR_GIB)),
        "cases": cases,
    }
    save_private(manifest_path, manifest)
    return manifest


def single_film(directory: Path) -> Path:
    """The one finished film under a case's own directory.

    `generate` treats `--output` as a location: it writes a run directory beside
    it and puts the film inside, and its Live merges leave intermediates in a
    hidden `.live_segments` directory. Hidden paths are never the film.
    """
    films = sorted(
        p
        for p in directory.rglob("*.mp4")
        if not any(part.startswith(".") for part in p.relative_to(directory).parts)
    )
    if len(films) != 1:
        raise RuntimeError(f"Expected one film under {directory}, found {len(films)}")
    return films[0]


def run_case(
    case: dict[str, Any],
    manifest: Mapping[str, Any],
    runner: Callable[[list[str], Path], int],
    checkpoint: Callable[[], None],
) -> None:
    directory = Path(case["output_directory"])
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path = directory / "run.private.log"
    case.update(
        status="running", started_at=now(), started_epoch=time.time(), log_path=str(log_path)
    )
    # Saved before the render, so a killed batch is readable as interrupted rather
    # than as one that never started this case.
    checkpoint()
    command = [manifest["cli"], "--config", manifest["config_path"], "generate", *case["args"]]
    exit_code = runner(command, log_path)
    case.update(
        exit_code=exit_code,
        elapsed_seconds=round(time.time() - case["started_epoch"], 1),
        finished_at=now(),
    )
    if exit_code:
        raise RuntimeError(f"CLI exited {exit_code}; inspect {log_path}")
    film = single_film(directory)
    case.update(status="rendered", film={"path": str(film), "sha256": sha256(film)})


def subprocess_runner(command: list[str], log_path: Path) -> int:
    log_path.touch(mode=0o600)
    with log_path.open("w") as stream:
        completed = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=False)
    return completed.returncode


def _skip_reason(case: Mapping[str, Any], retry_failed: bool) -> str | None:
    """Why this case is not run now — or None, meaning run it."""
    status = case.get("status")
    if status in RESUMABLE_STATUSES:
        recorded = case.get("film") or {}
        film = Path(recorded["path"]) if recorded.get("sha256") else None
        if film is not None and not film.exists():
            raise RuntimeError(f"{case['id']}: the finished film is gone from {film}")
        if film is not None and sha256(film) != recorded["sha256"]:
            raise RuntimeError(f"{case['id']}: the finished film changed on disk")
        return "done"
    if status == "failed" and not retry_failed:
        return "failed earlier; --retry-failed to run it again"
    if status == "running":
        return "left running; collect or inspect it before restarting"
    return None


def run(
    manifest: dict[str, Any],
    manifest_path: Path,
    *,
    only: str | None,
    retry_failed: bool,
    runner: Callable[[list[str], Path], int] = subprocess_runner,
) -> None:
    """Render the outstanding cases one at a time, saving the manifest after each."""
    if sha256(Path(manifest["config_path"])) != manifest["config_sha256"]:
        raise RuntimeError("The frozen configuration changed; rebuild the manifest")
    for case in manifest["cases"]:
        if only and case["id"] != only:
            continue
        reason = _skip_reason(case, retry_failed)
        if reason is not None:
            print(f"{case['id']}: skipped ({reason})", flush=True)
            continue
        free = shutil.disk_usage(manifest_path.parent).free
        floor_gib = manifest.get("min_free_gib", DEFAULT_FREE_DISK_FLOOR_GIB)
        if free < floor_gib * 1024**3:
            raise RuntimeError(
                f"Only {free / 1024**3:.1f} GiB free; the floor is {floor_gib:g} GiB"
            )
        print(f"{now()} {case['id']} rendering", flush=True)
        try:
            run_case(case, manifest, runner, lambda: save_private(manifest_path, manifest))
        except Exception as exc:
            case.update(status="failed", error=str(exc), finished_at=now())
        save_private(manifest_path, manifest)
        print(f"{now()} {case['id']} {case['status']}", flush=True)


def probe_film(film: Path, ffprobe: str) -> dict[str, Any]:
    probe = json.loads(
        subprocess.check_output(
            [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(film)],
            text=True,
        )
    )
    picture = next(s for s in probe["streams"] if s["codec_type"] == "video")
    sound = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
    if (picture["width"], picture["height"]) != (1920, 1080):
        raise RuntimeError(f"{film.name} is {picture['width']}x{picture['height']}, not 1920x1080")
    if sound is None:
        raise RuntimeError(f"{film.name} has no audio stream")
    return {
        "path": str(film),
        "sha256": sha256(film),
        "bytes": film.stat().st_size,
        "seconds": round(float(probe["format"]["duration"]), 2),
        "codec": picture["codec_name"],
        "audio_codec": sound["codec_name"],
    }


def assert_music_applied(log_path: Path) -> None:
    log = log_path.read_text(errors="replace")
    if "Audio mixed successfully" not in log:
        raise RuntimeError(f"The requested soundtrack was not mixed in; see {log_path}")


def decode_fully(film: Path, ffmpeg: str, log_path: Path) -> None:
    """Decode every frame and every sample; a file that only opens is not a film."""
    log_path.touch(mode=0o600)
    with log_path.open("w") as stream:
        completed = subprocess.run(
            [
                *(ffmpeg, "-nostdin", "-v", "error", "-xerror", "-i", str(film)),
                *("-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"),
            ],
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=1800,
        )
    if completed.returncode or log_path.stat().st_size:
        raise RuntimeError(f"Full decode failed: {log_path}")


def harvest_attempt(case: Mapping[str, Any], editorial_runs: Path) -> dict[str, Any]:
    """The plan this case's own run wrote, found by launch time and then by product."""
    started = case.get("started_epoch", 0.0)
    fresh = [
        path
        for path in editorial_runs.glob("*/attempts/*/status.private.json")
        if path.stat().st_mtime >= started
    ]
    if not fresh:
        return {}
    matching = [
        path
        for path in fresh
        if json.loads(path.read_text()).get("request", {}).get("product") == case["memory_type"]
    ]
    latest = max(matching or fresh, key=lambda p: p.stat().st_mtime)
    plan_path = latest.parent / "plan.private.json"
    if not plan_path.exists():
        return {"attempt_path": str(latest.parent)}
    plan = json.loads(plan_path.read_text())
    carriers = plan.get("carriers") or []
    return {
        "attempt_path": str(latest.parent),
        "plan_sha256": sha256(plan_path),
        "carrier_asset_ids": [str(c.get("asset_id")) for c in carriers],
        "favourite_asset_ids": [str(c["asset_id"]) for c in carriers if c.get("favourite")],
        "content_seconds": plan.get("content_seconds"),
        "duration_realization": plan.get("duration_realization"),
        "llm_metrics": plan.get("llm_metrics"),
    }


def harvest_delivery(film_path: str, database_path: Path) -> dict[str, Any]:
    """The tracked run that produced this film, for its Immich delivery identity."""
    from immich_memories.tracking.run_database import RunDatabase

    for run_row in RunDatabase(database_path).list_runs(limit=500):
        if run_row.output_path == film_path:
            return {
                "run_id": run_row.run_id,
                "immich_asset_id": run_row.immich_asset_id,
                "delivery_album": run_row.delivery_album,
            }
    return {}


def verify_film(case: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Everything FFmpeg has to say about the film before anyone is asked to watch it."""
    directory = Path(case["output_directory"])
    film = single_film(directory)
    facts = probe_film(film, manifest["ffprobe"])
    assert_music_applied(Path(case["log_path"]))
    decode_fully(film, manifest["ffmpeg"], directory / "decode.private.log")
    facts["full_decode"] = "passed"
    return facts


def collect_case(
    case: dict[str, Any],
    manifest: Mapping[str, Any],
    *,
    verify: Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any]] = verify_film,
    delivery: Callable[[str, Path], dict[str, Any]] = harvest_delivery,
) -> None:
    case["film"] = verify(case, manifest)
    case["plan"] = harvest_attempt(case, Path(manifest["editorial_runs"]))
    case["delivery"] = delivery(case["film"]["path"], Path(manifest["database_path"]))
    if manifest["upload"] and not case["delivery"].get("immich_asset_id"):
        raise RuntimeError("Upload was requested but no Immich asset id was recorded")
    case.update(status="ready", verified_at=now())


def collect(
    manifest: dict[str, Any], manifest_path: Path, *, only: str | None, **ports: Any
) -> None:
    for case in manifest["cases"]:
        if only and case["id"] != only:
            continue
        # Without --only, verify what `run` just produced. Naming a case re-verifies
        # it whatever state it is in, which is how a failed verification is retried.
        if not only and case.get("status") != "rendered":
            continue
        try:
            collect_case(case, manifest, **ports)
        except Exception as exc:
            case.update(status="failed", error=str(exc), verified_at=now())
        save_private(manifest_path, manifest)
        print(f"{case['id']}: {case['status']}", flush=True)


def report(manifest: Mapping[str, Any], manifest_path: Path) -> str:
    """Write the private report beside the manifest; return the console-safe table."""
    rows = report_rows(manifest)
    stem = manifest_path.name.split(".")[0] + "-report"
    save_private(manifest_path.with_name(f"{stem}.private.json"), {"rows": rows})
    markdown_path = manifest_path.with_name(f"{stem}.private.md")
    write_private(markdown_path, markdown_report(manifest, rows))
    return f"{console_table(rows)}\n\nPrivate report: {markdown_path}"


def locked(manifest_path: Path) -> tuple[Any, dict[str, Any]]:
    """Hold an exclusive lock on the manifest for as long as the process lives."""
    lock = manifest_path.with_suffix(".lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError(f"Another matrix_routes process holds {manifest_path}")
    return lock, json.loads(manifest_path.read_text())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    builder = sub.add_parser("build", help="Join the public routes with the private overlay")
    builder.add_argument("--routes", type=Path, required=True)
    builder.add_argument("--private", type=Path, required=True)
    builder.add_argument("--manifest", type=Path, required=True)
    builder.add_argument("--cli", default=str(Path(sys.executable).parent / "immich-memories"))

    runner = sub.add_parser("run", help="Render the outstanding cases, one at a time")
    runner.add_argument("--manifest", type=Path, required=True)
    runner.add_argument("--only")
    runner.add_argument("--retry-failed", action="store_true")

    collector = sub.add_parser("collect", help="Verify the films and harvest their plans")
    collector.add_argument("--manifest", type=Path, required=True)
    collector.add_argument("--only")

    reporter = sub.add_parser("report", help="Table the run, and compare it with the reference")
    reporter.add_argument("--manifest", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.command == "build":
        manifest = build(args.routes, args.private, args.manifest, args.cli)
        print(f"{len(manifest['cases'])} cases written to {args.manifest}")
        return 0

    lock, manifest = locked(args.manifest)
    try:
        if args.command == "run":
            run(manifest, args.manifest, only=args.only, retry_failed=args.retry_failed)
        elif args.command == "collect":
            collect(manifest, args.manifest, only=args.only)
        else:
            print(report(manifest, args.manifest))
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
