"""The facts a cut measures: a Live Photo's motion residual, a clip's speech, and how two
Live companions' clocks relate.

A row belongs to one picture (or one pair of companions), one producer and the exact source
metadata it was measured from, like a caption or a motion line. A changed source is a different digest, so its old
row stops being an answer. No row means nobody measured, never "measured nothing": a clip
with no speech in it is an empty region list under a row that exists.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any, TypeVar

from immich_memories.store.editorial_preparation import now, private_database_path

MOTION_RESIDUALS = "motion_residuals"
SPEECH_REGIONS = "speech_regions"
LIVE_CLOCK_OFFSETS = "live_clock_offsets"

T = TypeVar("T")
K = TypeVar("K")

SCHEMA = """
CREATE TABLE IF NOT EXISTS motion_residuals (
 asset_id TEXT NOT NULL, producer TEXT NOT NULL, source_digest TEXT NOT NULL,
 measured TEXT NOT NULL, written_at TEXT NOT NULL, PRIMARY KEY(asset_id, producer));
CREATE TABLE IF NOT EXISTS speech_regions (
 asset_id TEXT NOT NULL, producer TEXT NOT NULL, source_digest TEXT NOT NULL,
 measured TEXT NOT NULL, written_at TEXT NOT NULL, PRIMARY KEY(asset_id, producer));
CREATE TABLE IF NOT EXISTS live_clock_offsets (
 asset_id TEXT NOT NULL, producer TEXT NOT NULL, source_digest TEXT NOT NULL,
 measured TEXT NOT NULL, written_at TEXT NOT NULL, PRIMARY KEY(asset_id, producer));
"""


def initialize_cut_measurements(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)


def open_cut_measurements(path: Path) -> sqlite3.Connection:
    """A writable bank at this path, created private to the owner if it is not there yet."""
    connection = sqlite3.connect(private_database_path(path))
    connection.execute("PRAGMA journal_mode=WAL")
    initialize_cut_measurements(connection)
    return connection


def _remember(
    connection: sqlite3.Connection,
    table: str,
    *,
    asset_id: str,
    producer: str,
    source_digest: str,
    measured: Any,
) -> None:
    with connection:
        connection.execute(
            f"INSERT OR REPLACE INTO {table} VALUES (?,?,?,?,?)",  # noqa: S608
            (asset_id, producer, source_digest, json.dumps(measured, sort_keys=True), now()),
        )


def _banked(
    connection: sqlite3.Connection, table: str, digests: Mapping[str, str], producer: str
) -> dict[str, Any]:
    banked: dict[str, Any] = {}
    ids = list(digests)
    for start in range(0, len(ids), 500):
        batch = ids[start : start + 500]
        try:
            rows = connection.execute(
                f"SELECT asset_id, source_digest, measured FROM {table} "  # noqa: S608
                f"WHERE producer=? AND asset_id IN ({','.join('?' * len(batch))})",
                (producer, *batch),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).casefold():
                raise
            return {}
        for asset_id, digest, measured in rows:
            if digest == digests[str(asset_id)]:
                banked[str(asset_id)] = json.loads(measured)
    return banked


def remember_motion_residual(
    connection: sqlite3.Connection,
    *,
    asset_id: str,
    producer: str,
    source_digest: str,
    measured: Mapping[str, Any],
) -> None:
    """Replace whatever this producer measured on an older version of the same picture."""
    _remember(
        connection,
        MOTION_RESIDUALS,
        asset_id=asset_id,
        producer=producer,
        source_digest=source_digest,
        measured=dict(measured),
    )


def banked_motion_residuals(
    connection: sqlite3.Connection, digests: Mapping[str, str], producer: str
) -> dict[str, dict[str, Any]]:
    """The motion measurements that still answer for these pictures as they are now."""
    return {
        asset_id: measured
        for asset_id, measured in _banked(connection, MOTION_RESIDUALS, digests, producer).items()
        if isinstance(measured, dict)
    }


def remember_speech_regions(
    connection: sqlite3.Connection,
    *,
    asset_id: str,
    producer: str,
    source_digest: str,
    regions: Sequence[tuple[float, float]],
) -> None:
    """An empty list is an answer: this detector heard no speech in this exact source."""
    _remember(
        connection,
        SPEECH_REGIONS,
        asset_id=asset_id,
        producer=producer,
        source_digest=source_digest,
        measured=[[start, end] for start, end in regions],
    )


def banked_speech_regions(
    connection: sqlite3.Connection, digests: Mapping[str, str], producer: str
) -> dict[str, tuple[tuple[float, float], ...]]:
    """The speech regions that still answer for these clips as they are now."""
    return {
        asset_id: tuple((float(pair[0]), float(pair[1])) for pair in measured)
        for asset_id, measured in _banked(connection, SPEECH_REGIONS, digests, producer).items()
        if isinstance(measured, list)
    }


def _pair_key(pair: tuple[str, str]) -> str:
    # A join belongs to both companions, in shutter order; ids never hold a newline.
    return f"{pair[0]}\n{pair[1]}"


def remember_clock_offset(
    connection: sqlite3.Connection,
    *,
    pair: tuple[str, str],
    producer: str,
    source_digest: str,
    seconds: float | None,
) -> None:
    """None is an answer: the files share no content this measurement can trust."""
    _remember(
        connection,
        LIVE_CLOCK_OFFSETS,
        asset_id=_pair_key(pair),
        producer=producer,
        source_digest=source_digest,
        measured={"seconds": seconds},
    )


def banked_clock_offsets(
    connection: sqlite3.Connection, digests: Mapping[tuple[str, str], str], producer: str
) -> dict[tuple[str, str], float | None]:
    """The joins already measured between these companions as they are now."""
    pairs = {_pair_key(pair): pair for pair in digests}
    rows = _banked(
        connection,
        LIVE_CLOCK_OFFSETS,
        {key: digests[pair] for key, pair in pairs.items()},
        producer,
    )
    return {
        pairs[key]: (float(seconds) if seconds is not None else None)
        for key, measured in rows.items()
        if isinstance(measured, dict) and "seconds" in measured
        for seconds in (measured["seconds"],)
    }


def reading_cut_measurements(
    store_path: Path, read: Callable[[sqlite3.Connection], dict[K, T]]
) -> dict[K, T]:
    """Apply a bank reader to the store without writing it; an unreachable store answers nobody."""
    if not Path(store_path).exists():
        return {}
    try:
        with closing(sqlite3.connect(f"file:{store_path}?mode=ro", uri=True)) as connection:
            return read(connection)
    except sqlite3.Error:
        return {}
