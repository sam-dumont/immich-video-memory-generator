"""Explain the caption origin saved with a run, without reopening its live bank."""

from __future__ import annotations

import json
from pathlib import Path


def caption_origin_note(attempt: Path, asset_id: str) -> str:
    try:
        preparation = json.loads((attempt / "preparation.private.json").read_text())
    except (OSError, ValueError):
        return "Caption origin: unknown (not recorded for this run)"
    origins = preparation.get("caption_provenance")
    if not isinstance(origins, dict):
        return "Caption origin: unknown (not recorded for this run)"
    origin = origins.get(asset_id)
    if origin is None:
        return ""
    if origin.get("status") == "unknown":
        return "Caption origin: unknown (not recorded with this caption)"
    build = ", ".join(
        f"{key}={value}" for key, value in sorted(origin.get("reported_build", {}).items())
    )
    parts = [
        str(origin.get("model_id", "unknown model")),
        str(origin.get("endpoint", "unknown endpoint")),
        str(origin.get("artifact_id") or "artifact not declared"),
        build or "build not reported",
    ]
    return "Caption origin: " + "; ".join(parts)
