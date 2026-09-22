"""Small deterministic digests shared by local triage-head stages."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable


def label_inventory_sha256(rows: Iterable[tuple[str, str, str]]) -> str:
    """Bind asset id, capture group, and source version for one label inventory."""
    normalized = sorted((str(asset), str(group), str(source)) for asset, group, source in rows)
    if len({asset for asset, _, _ in normalized}) != len(normalized):
        raise ValueError("label inventory asset ids must be unique")
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(b"triage-label-inventory-v1\0" + encoded).hexdigest()
