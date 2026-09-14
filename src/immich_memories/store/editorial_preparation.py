"""Persist source metadata and exact-producer annotation preparation facts."""

from __future__ import annotations

import math
import os
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from immich_memories.analysis.editorial_description_contract import (
    DESCRIPTION_MODEL,
    DESCRIPTION_SOURCE,
    validate_envelope,
)
from immich_memories.analysis.editorial_description_outcomes import unavailable_for
from immich_memories.api.models import Asset

ASSET_COLUMNS = {
    "taken_at": "TEXT",
    "media_kind": "TEXT",
    "favourite": "INTEGER",
    "original_file": "TEXT",
    "width": "INTEGER",
    "height": "INTEGER",
    "city": "TEXT",
    "state": "TEXT",
    "country": "TEXT",
    "latitude": "REAL",
    "longitude": "REAL",
    "live_photo_video_id": "TEXT",
    "duration_seconds": "REAL",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (asset_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS descriptions (
 asset_id TEXT, model TEXT, text TEXT, source TEXT, written_at TEXT, PRIMARY KEY(asset_id,model));
CREATE TABLE IF NOT EXISTS caption_provenance (
 asset_id TEXT, model TEXT, origin TEXT NOT NULL, PRIMARY KEY(asset_id,model));
CREATE TABLE IF NOT EXISTS description_fields (
 asset_id TEXT, model TEXT, field TEXT, value TEXT, written_at TEXT, PRIMARY KEY(asset_id,model,field));
CREATE TABLE IF NOT EXISTS asset_people (
 asset_id TEXT, person_name TEXT, person_id TEXT, birth_date TEXT, written_at TEXT,
 PRIMARY KEY(asset_id,person_name));
CREATE TABLE IF NOT EXISTS flags (
 asset_id TEXT, flag TEXT, evidence TEXT, source TEXT, written_at TEXT,
 PRIMARY KEY(asset_id,flag,source));
CREATE TABLE IF NOT EXISTS head_facts (
 asset_id TEXT, head TEXT, version TEXT, label TEXT, confidence REAL, encoder_key TEXT, decided_at TEXT,
 PRIMARY KEY(asset_id,head,version));
CREATE TABLE IF NOT EXISTS pixel_facts (
 asset_id TEXT PRIMARY KEY, producer_key TEXT, sharpness REAL, brightness REAL, contrast REAL,
 dark_fraction REAL, bright_fraction REAL, width INTEGER, height INTEGER, orientation TEXT,
 needs_rotation INTEGER, computed_at TEXT);
CREATE TABLE IF NOT EXISTS pixel_facts_thresholds (
 name TEXT PRIMARY KEY, value REAL, producer_key TEXT, n INTEGER, computed_at TEXT);
CREATE TABLE IF NOT EXISTS motion_bursts (
 asset_id TEXT PRIMARY KEY, burst_id TEXT, still_ids TEXT, video_ids TEXT, duration_seconds REAL,
 beats_a_still INTEGER, minimum_seconds REAL, computed_at TEXT);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def private_database_path(path: Path) -> Path:
    """Create at 0600 before SQLite opens it; keep existing sidecars private too."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(descriptor)
    path.chmod(0o600)
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        with suppress(FileNotFoundError):
            sidecar.chmod(0o600)
    return path


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    existing = {row[1] for row in connection.execute("PRAGMA table_info(assets)")}
    for name, sql_type in ASSET_COLUMNS.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE assets ADD COLUMN {name} {sql_type}")  # noqa: S608
    connection.commit()


def remember_assets(connection: sqlite3.Connection, assets: Sequence[Asset]) -> None:
    """Use already fetched Immich metadata; never derive flags from captions."""
    columns = ",".join(ASSET_COLUMNS)
    updates = ",".join(f"{name}=excluded.{name}" for name in ASSET_COLUMNS)
    rows = []
    people = []
    timestamp = now()
    for asset in assets:
        exif = asset.exif_info
        taken = (
            (exif.date_time_original if exif else None)
            or asset.local_date_time
            or asset.file_created_at
        )
        rows.append(
            (
                asset.id,
                taken.isoformat(),
                "video" if asset.is_video else "photo",
                int(asset.is_favorite),
                asset.original_file_name,
                asset.width,
                asset.height,
                *(
                    getattr(exif, key, None)
                    for key in ("city", "state", "country", "latitude", "longitude")
                ),
                asset.live_photo_video_id,
                asset.duration_seconds,
            )
        )
        for person in asset.people:
            if person.name.strip():
                birth = person.birth_date.date().isoformat() if person.birth_date else None
                people.append((asset.id, person.name, person.id, birth, timestamp))
    connection.executemany(
        f"INSERT INTO assets (asset_id,{columns}) VALUES ({','.join('?' for _ in range(14))}) "  # noqa: S608
        f"ON CONFLICT(asset_id) DO UPDATE SET {updates}",
        rows,
    )
    # Current source metadata is authoritative for people; owner flags remain untouched.
    connection.executemany("DELETE FROM asset_people WHERE asset_id=?", ((a.id,) for a in assets))
    connection.executemany(
        "INSERT OR REPLACE INTO asset_people (asset_id,person_name,person_id,birth_date,written_at) VALUES (?,?,?,?,?)",
        people,
    )
    connection.commit()


def missing_facts(
    connection: sqlite3.Connection,
    asset_ids: Sequence[str],
    *,
    description_model: str,
    head_versions: Mapping[str, str],
    pixel_producer_key: str,
    preview_for: Callable[[str], bytes],
) -> tuple[dict[str, tuple[str, ...]], tuple[str, ...]]:
    """Report all missing or malformed producers, plus proven terminal caption failures."""
    wanted = tuple(dict.fromkeys(asset_ids))
    _stage_wanted(connection, wanted)
    complete = _complete_captions(connection, description_model)
    unavailable: set[str] = set()
    if description_model == DESCRIPTION_MODEL:
        unavailable, damaged = _terminal_caption_failures(connection, wanted, preview_for)
        complete -= damaged
    missing: dict[str, tuple[str, ...]] = {
        f"description:{description_model}": tuple(
            a for a in wanted if a not in complete | unavailable
        )
    }
    for head, version in head_versions.items():
        found = _decided_heads(connection, head, version)
        missing[f"head:{head}@{version}"] = tuple(a for a in wanted if a not in found)
    pixels = _measured_pixels(connection, pixel_producer_key)
    missing[f"pixel:{pixel_producer_key}"] = tuple(a for a in wanted if a not in pixels)
    return {key: ids for key, ids in missing.items() if ids}, tuple(
        a for a in wanted if a in unavailable
    )


def _stage_wanted(connection: sqlite3.Connection, wanted: Sequence[str]) -> None:
    connection.execute(
        "CREATE TEMP TABLE IF NOT EXISTS preparation_wanted (asset_id TEXT PRIMARY KEY)"
    )
    connection.execute("DELETE FROM preparation_wanted")
    connection.executemany("INSERT INTO preparation_wanted VALUES (?)", ((a,) for a in wanted))


def _complete_caption(
    description_model: str, text: str, source: str, fields: Mapping[str, str]
) -> bool:
    if description_model != DESCRIPTION_MODEL:
        return bool(text) and bool(fields.get("setting"))
    if source != DESCRIPTION_SOURCE or set(fields) != {"setting"}:
        return False
    try:
        validate_envelope({"description": text, "setting": fields["setting"]})
    except (TypeError, ValueError):
        return False
    return True


def _complete_captions(connection: sqlite3.Connection, description_model: str) -> set[str]:
    descriptions = {
        r[0]: r[1:]
        for r in connection.execute(
            "SELECT asset_id,text,source FROM descriptions JOIN preparation_wanted USING(asset_id) WHERE model=?",
            (description_model,),
        )
    }
    fields: dict[str, dict[str, str]] = {}
    for asset_id, field, value in connection.execute(
        "SELECT asset_id,field,value FROM description_fields JOIN preparation_wanted USING(asset_id) WHERE model=?",
        (description_model,),
    ):
        fields.setdefault(asset_id, {})[field] = value
    return {
        asset_id
        for asset_id, (text, source) in descriptions.items()
        if _complete_caption(description_model, text, source, fields.get(asset_id, {}))
    }


def _terminal_caption_failures(
    connection: sqlite3.Connection,
    wanted: Sequence[str],
    preview_for: Callable[[str], bytes],
) -> tuple[set[str], set[str]]:
    """Only a damaged batch needs isolated reads. Conflicting evidence cannot be complete."""
    with suppress(OSError, ValueError, sqlite3.Error):
        return set(unavailable_for(connection, wanted, preview_for=preview_for)), set()
    unavailable: set[str] = set()
    damaged: set[str] = set()
    for asset_id in wanted:
        try:
            unavailable.update(unavailable_for(connection, (asset_id,), preview_for=preview_for))
        except (OSError, ValueError, sqlite3.Error):
            damaged.add(asset_id)
    return unavailable, damaged


def _decided_heads(connection: sqlite3.Connection, head: str, version: str) -> set[str]:
    return {
        asset_id
        for asset_id, label, confidence in connection.execute(
            "SELECT asset_id,label,confidence FROM head_facts JOIN preparation_wanted USING(asset_id) WHERE head=? AND version=?",
            (head, version),
        )
        if isinstance(label, str)
        and label.strip()
        and isinstance(confidence, (int, float))
        and math.isfinite(confidence)
        and 0 <= confidence <= 1
    }


def _measured_pixels(connection: sqlite3.Connection, pixel_producer_key: str) -> set[str]:
    pixels = {
        asset_id
        for asset_id, *values in connection.execute(
            "SELECT asset_id,sharpness,brightness,contrast,dark_fraction,bright_fraction FROM pixel_facts "
            "JOIN preparation_wanted USING(asset_id) WHERE producer_key=?",
            (pixel_producer_key,),
        )
        if all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)
    }
    threshold = connection.execute(
        "SELECT value FROM pixel_facts_thresholds WHERE name='sharpness_p10' AND producer_key=?",
        (pixel_producer_key,),
    ).fetchone()
    if (
        threshold is None
        or not isinstance(threshold[0], (float, int))
        or not math.isfinite(threshold[0])
    ):
        return set()
    return pixels
