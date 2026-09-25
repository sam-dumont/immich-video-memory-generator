"""The owner's own word on a picture: clear its hold, or never use it.

Kept as `source='owner'` rows in the annotation store's `flags` table, the rows the audience
gate and the material builder already read (`editorial_shareability.load_flags`). This module
is their only writer. A picture carries at most one owner decision: a new one replaces the old
in the same transaction, so two writers (the web page and the CLI) can't leave a picture both
cleared and never used. Each write is a single SQLite transaction, which is why no file lock is
needed here, unlike the JSON banks (#1266).

A Live Photo is one picture to its owner, so a decision on its still covers its motion clip
too, when the store knows the clip.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path

from immich_memories.analysis.editorial_shareability import (
    NEVER_AUTO,
    OWNER_CLEARED,
    OWNER_SOURCE,
)
from immich_memories.store.editorial_preparation import initialize, now, private_database_path

CLEAR_HOLD = OWNER_CLEARED
NEVER_USE = NEVER_AUTO
DECISIONS = (CLEAR_HOLD, NEVER_USE)


def _open(store_path: Path | str) -> sqlite3.Connection:
    connection = sqlite3.connect(private_database_path(Path(store_path)), timeout=60)
    initialize(connection)
    return connection


def _picture(connection: sqlite3.Connection, asset_id: str, clip_id: str | None) -> list[str]:
    ids = [asset_id]
    if clip_id is None:
        row = connection.execute(
            "SELECT live_photo_video_id FROM assets WHERE asset_id=?", (asset_id,)
        ).fetchone()
        clip_id = row[0] if row else None
    if clip_id and clip_id != asset_id:
        ids.append(str(clip_id))
    return ids


def decide(
    store_path: Path | str,
    asset_id: str,
    decision: str,
    *,
    via: str,
    clip_id: str | None = None,
) -> tuple[str, ...]:
    """Record the owner's decision on one picture, replacing any earlier one.

    Returns the ids written: the picture, and its Live Photo clip when there is one. `via`
    names the surface (`web`, `cli`) for the record.
    """
    if decision not in DECISIONS:
        raise ValueError(f"unknown owner decision {decision!r}")
    evidence = json.dumps({"via": via, "at": now()}, sort_keys=True)
    with closing(_open(store_path)) as connection, connection:
        ids = _picture(connection, asset_id, clip_id)
        connection.executemany(
            "DELETE FROM flags WHERE asset_id=? AND source=?", ((i, OWNER_SOURCE) for i in ids)
        )
        connection.executemany(
            "INSERT INTO flags (asset_id,flag,evidence,source,written_at) VALUES (?,?,?,?,?)",
            ((i, decision, evidence, OWNER_SOURCE, now()) for i in ids),
        )
    return tuple(ids)


def forget(store_path: Path | str, asset_id: str, *, clip_id: str | None = None) -> tuple[str, ...]:
    """Drop the owner's decision on one picture: the app's own holds and rules apply again."""
    with closing(_open(store_path)) as connection, connection:
        ids = _picture(connection, asset_id, clip_id)
        connection.executemany(
            "DELETE FROM flags WHERE asset_id=? AND source=?", ((i, OWNER_SOURCE) for i in ids)
        )
    return tuple(ids)


def live_clips(store_path: Path | str, asset_ids: Iterable[str]) -> dict[str, str]:
    """The motion clip of each Live Photo among these pictures, as the store banked it."""
    path = Path(store_path)
    ids = list(dict.fromkeys(asset_ids))
    if not path.is_file() or not ids:
        return {}
    out: dict[str, str] = {}
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ",".join("?" * len(chunk))
            try:
                rows = connection.execute(
                    "SELECT asset_id, live_photo_video_id FROM assets "  # noqa: S608
                    f"WHERE live_photo_video_id IS NOT NULL AND asset_id IN ({marks})",
                    chunk,
                ).fetchall()
            except sqlite3.OperationalError:
                return {}
            out.update({str(a): str(c) for a, c in rows if c})
    return out


def decisions(store_path: Path | str, asset_ids: Iterable[str] | None = None) -> dict[str, str]:
    """The owner's decision per picture, for these ids or for every picture that has one."""
    path = Path(store_path)
    if not path.is_file():
        return {}
    wanted = None if asset_ids is None else set(asset_ids)
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
        try:
            rows = connection.execute(
                "SELECT asset_id, flag FROM flags WHERE source=? ORDER BY asset_id",
                (OWNER_SOURCE,),
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
    return {
        str(asset_id): str(flag)
        for asset_id, flag in rows
        if flag in DECISIONS and (wanted is None or str(asset_id) in wanted)
    }
