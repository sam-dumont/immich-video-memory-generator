"""Account for bounded invalid caption completions without inventing a description."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from immich_memories.analysis.editorial_description_contract import (
    DESCRIPTION_MODEL,
    DESCRIPTION_SOURCE,
    MAX_OUTPUT_TOKENS,
    TILE_VERSION,
    validate_envelope,
)
from immich_memories.analysis.editorial_description_wire import request_bytes, tile_preview

VERSION = "caption-unavailable-v1"
TABLE = "description_unavailable"
COLUMNS = (
    "asset_id",
    "model",
    "source",
    "version",
    "producer_key",
    "preview_sha256",
    "image_sha256",
    "request_sha256",
    "request_json",
    "attempts_json",
    "written_at",
)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def producer_key() -> str:
    return digest(
        json.dumps(
            {
                "wire": request_bytes(b"").decode(),
                "description_model": DESCRIPTION_MODEL,
                "description_source": DESCRIPTION_SOURCE,
                "tile_version": TILE_VERSION,
            },
            sort_keys=True,
        ).encode()
    )


def cached_preview(cache_path: Path, asset_id: str) -> bytes:
    """Read the native cache layout without its LRU timestamp mutation."""
    subdir = asset_id[:2] if len(asset_id) >= 2 else "00"
    return (cache_path / subdir / f"{asset_id}_preview.jpg").read_bytes()


_ATTEMPT_FIELDS = frozenset(
    {
        "envelope",
        "error",
        "elapsed_seconds",
        "finish_reason",
        "completion_tokens",
        "prompt_tokens",
        "raw_sha256",
        "raw_content",
        "request_sha256",
        "image_sha256",
    }
)


def _bounded_token_counts(row: Mapping[str, Any]) -> bool:
    return (
        all(
            type(row[key]) is int and row[key] >= 0
            for key in ("completion_tokens", "prompt_tokens")
        )
        and 0 < row["completion_tokens"] <= MAX_OUTPUT_TOKENS
    )


def _measured_elapsed(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (float, int))
        and math.isfinite(value)
        and value >= 0
    )


def _valid_envelope_text(content: str) -> bool:
    try:
        validate_envelope(json.loads(content))
    except (ValueError, TypeError):
        return False
    return True


def _invalid_model_completion(row: Mapping[str, Any]) -> bool:
    if set(row) != _ATTEMPT_FIELDS:
        return False
    content = row["raw_content"]
    if (
        row["envelope"] is not None
        or row["finish_reason"] not in {"stop", "length"}
        or not isinstance(content, str)
        or not content
        or digest(content.encode()) != row["raw_sha256"]
        or not isinstance(row["error"], str)
        or not (row["error"].startswith("invalid:") or row["error"] == "unparsed")
        or not _bounded_token_counts(row)
        or not _measured_elapsed(row["elapsed_seconds"])
    ):
        return False
    # A completion that stopped on its own and does parse is a success, not a failure.
    return row["finish_reason"] != "stop" or not _valid_envelope_text(content)


def bounded_invalid_attempts(attempts: Sequence[Mapping[str, Any]]) -> bool:
    """Only two actual invalid model completions are terminal; never transport errors."""
    return len(attempts) == 2 and all(_invalid_model_completion(row) for row in attempts)


def make_unavailable(
    asset_id: str, preview: bytes, attempts: Sequence[Mapping[str, Any]], *, written_at: str
) -> dict[str, Any]:
    if not asset_id or not written_at or not bounded_invalid_attempts(attempts):
        raise ValueError("unavailable caption requires two bounded invalid model completions")
    image = tile_preview(preview)
    wire = request_bytes(image)
    if any(
        a["request_sha256"] != digest(wire) or a["image_sha256"] != digest(image) for a in attempts
    ):
        raise ValueError("actual model request differs from failed caption input")
    return dict(
        zip(
            COLUMNS,
            (
                asset_id,
                DESCRIPTION_MODEL,
                DESCRIPTION_SOURCE,
                VERSION,
                producer_key(),
                digest(preview),
                digest(image),
                digest(wire),
                wire.decode(),
                json.dumps(list(attempts), sort_keys=True),
                written_at,
            ),
            strict=True,
        )
    )


def validate_unavailable(row: Mapping[str, Any], preview: bytes) -> None:
    if (
        set(row) != set(COLUMNS)
        or not row["asset_id"]
        or not isinstance(row["written_at"], str)
        or not row["written_at"].strip()
    ):
        raise ValueError("unavailable caption evidence shape is invalid")
    if (row["model"], row["source"], row["version"], row["producer_key"]) != (
        DESCRIPTION_MODEL,
        DESCRIPTION_SOURCE,
        VERSION,
        producer_key(),
    ):
        raise ValueError("unavailable caption producer identity differs")
    image = tile_preview(preview)
    wire = request_bytes(image)
    if (row["preview_sha256"], row["image_sha256"], row["request_sha256"], row["request_json"]) != (
        digest(preview),
        digest(image),
        digest(wire),
        wire.decode(),
    ):
        raise ValueError("unavailable caption preview/image/request binding differs")
    try:
        attempts = json.loads(row["attempts_json"])
    except (TypeError, ValueError) as exc:
        raise ValueError("unavailable caption attempts are malformed") from exc
    if (
        not isinstance(attempts, list)
        or not all(isinstance(a, dict) for a in attempts)
        or not bounded_invalid_attempts(attempts)
    ):
        raise ValueError("unavailable caption is not a bounded model failure")
    if any(
        a["request_sha256"] != digest(wire) or a["image_sha256"] != digest(image) for a in attempts
    ):
        raise ValueError("actual failed model requests differ from recorded input")


def remember_unavailable(
    connection: sqlite3.Connection, row: Mapping[str, Any], preview: bytes
) -> None:
    validate_unavailable(row, preview)
    connection.execute(
        f"CREATE TABLE IF NOT EXISTS {TABLE} ("
        + ", ".join(f"{key} TEXT NOT NULL" for key in COLUMNS)
        + ", PRIMARY KEY (asset_id, model))"
    )
    # This is an outcome insertion, not replacement of older attempted evidence.
    connection.execute(
        "INSERT INTO description_unavailable (asset_id,model,source,version,producer_key,preview_sha256,image_sha256,request_sha256,request_json,attempts_json,written_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        tuple(row[key] for key in COLUMNS),
    )
    connection.commit()


def unavailable_for(
    connection: sqlite3.Connection,
    asset_ids: Sequence[str],
    *,
    preview_for: Callable[[str], bytes] | None = None,
) -> dict[str, dict[str, Any]]:
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
    ).fetchone():
        return {}
    if {r[1] for r in connection.execute(f"PRAGMA table_info({TABLE})")} != set(COLUMNS):
        raise ValueError("unavailable caption table schema differs")
    wanted = sorted(set(asset_ids))
    found = {}
    # Bound parameters and payload reads to this scope, including on query_only connections.
    for offset in range(0, len(wanted), 500):
        chunk = wanted[offset : offset + 500]
        placeholders = ",".join("?" for _ in chunk)
        for values in connection.execute(
            "SELECT asset_id,model,source,version,producer_key,preview_sha256,image_sha256,request_sha256,request_json,attempts_json,written_at "  # noqa: S608 — only parameter placeholder marks are interpolated
            f"FROM description_unavailable WHERE model=? AND asset_id IN ({placeholders})",
            (DESCRIPTION_MODEL, *chunk),
        ):
            row = dict(zip(COLUMNS, values, strict=True))
            asset_id = row["asset_id"]
            if asset_id in found or preview_for is None:
                raise ValueError("unavailable caption needs unique evidence and current preview")
            validate_unavailable(row, preview_for(asset_id))
            conflicts = connection.execute(
                "SELECT 1 FROM descriptions WHERE asset_id=? AND model=? "
                "UNION ALL SELECT 1 FROM description_fields WHERE asset_id=? AND model=?",
                (asset_id, DESCRIPTION_MODEL, asset_id, DESCRIPTION_MODEL),
            ).fetchone()
            if conflicts:
                raise ValueError("caption success/partial rows conflict with unavailable outcome")
            found[asset_id] = row
    return found
