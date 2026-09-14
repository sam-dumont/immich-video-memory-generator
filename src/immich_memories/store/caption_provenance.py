"""Caption origins kept beside immutable description rows, outside reader identities."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True)
class CaptionOrigin:
    model_id: str
    endpoint: str
    artifact_id: str = ""
    reported_build: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        parts = urlsplit(self.endpoint)
        clean = parts._replace(netloc=parts.netloc.rsplit("@", 1)[-1], query="", fragment="")
        object.__setattr__(self, "endpoint", clean.geturl())


def remember_origin(
    connection: sqlite3.Connection, asset_id: str, model: str, origin: CaptionOrigin
) -> None:
    """Join the caller's caption transaction; never commit one half independently."""
    connection.execute(
        "INSERT INTO caption_provenance (asset_id,model,origin) VALUES (?,?,?)",
        (asset_id, model, json.dumps(asdict(origin), sort_keys=True)),
    )


def origins_for(
    connection: sqlite3.Connection, asset_ids: Sequence[str], model: str
) -> dict[str, dict]:
    """Return each existing caption's origin; old rows stay explicitly unknown."""
    recorded = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='caption_provenance'"
    ).fetchone()
    connection.execute("CREATE TEMP TABLE IF NOT EXISTS caption_wanted (asset_id TEXT PRIMARY KEY)")
    connection.execute("DELETE FROM caption_wanted")
    connection.executemany(
        "INSERT INTO caption_wanted VALUES (?)",
        ((asset_id,) for asset_id in dict.fromkeys(asset_ids)),
    )
    if recorded:
        sql = (
            "SELECT d.asset_id,p.origin FROM descriptions d JOIN caption_wanted w ON d.asset_id=w.asset_id "
            "LEFT JOIN caption_provenance p ON d.asset_id=p.asset_id AND d.model=p.model WHERE d.model=?"
        )
    else:
        sql = (
            "SELECT d.asset_id,NULL FROM descriptions d JOIN caption_wanted w ON d.asset_id=w.asset_id "
            "WHERE d.model=?"
        )
    return {
        asset_id: json.loads(raw) if raw else {"status": "unknown"}
        for asset_id, raw in connection.execute(sql, (model,))
    }
