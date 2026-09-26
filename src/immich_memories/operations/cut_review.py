"""Recorded model proposals for the pictures being reviewed, without new inference."""

from __future__ import annotations

import json
from pathlib import Path


def read_cut_decisions(attempt_dir: Path) -> dict[str, dict[str, str | int]]:
    """Read saved model objections and protection reasons; an absent pass adds nothing."""
    path = attempt_dir / "derived-decisions" / "thin-polish.private.json"
    if not path.is_file():
        return {}
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(record, dict):
        return {}
    decisions: dict[str, dict[str, str | int]] = {
        asset_id: {
            "model_reason": row.get("why", ""),
            "kept_reason": row.get("rule", "") if row.get("protected") else "",
            "proposed_asset_id": "",
            "offered_count": 0,
            "replacement_outcome": "",
        }
        for asset_id, row in record.get("verdicts", {}).items()
    }
    for slot in record.get("slots", []):
        if not (asset_id := slot.get("replacing")):
            continue
        decisions.setdefault(asset_id, {"model_reason": "", "kept_reason": ""}).update(
            proposed_asset_id=slot.get("chosen", ""),
            offered_count=int(slot.get("offered") or 0),
            replacement_outcome=slot.get("outcome", ""),
        )
    return decisions
