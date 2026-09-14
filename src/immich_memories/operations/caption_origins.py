"""Read the caption origins a run saved, and say how many distinct ones it found."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

# The two sentinel groups `store.caption_provenance` folds assets into.
_UNKNOWN = "unknown"
_NONE = "none"


def _describe(origin: Mapping[str, object]) -> str:
    served = ", ".join(
        f"{key}={value}" for key, value in sorted(_mapping(origin.get("served")).items())
    )
    digest = str(origin.get("control_digest") or "")
    return "; ".join(
        [
            str(origin.get("model_id", "unknown model")),
            str(origin.get("endpoint", "unknown endpoint")),
            str(origin.get("artifact_id") or "artifact not declared"),
            served or "build not reported",
            f"controls {digest}" if digest else "controls not measured",
        ]
    )


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _origins(provenance: object) -> list[dict]:
    rows = _mapping(provenance).get("origins")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def caption_origin_summary(provenance: object) -> str:
    """One line naming every distinct captioner behind a run's captions, or nothing.

    The count is the point: a bank filled by two servers that word `setting`
    differently is the failure #933 names, and until something counts the origins
    nobody sees the seam. The line is printed, so `setup_matrix_capture` can read
    it off stdout with the rest of the preparation table.
    """
    origins = [row for row in _origins(provenance) if row.get("status") != _NONE]
    if not origins:
        return ""
    captions = sum(int(row.get("assets", 0)) for row in origins)
    labels = "; ".join(
        f"{_UNKNOWN if row.get('status') == _UNKNOWN else _describe(row)} x{row.get('assets', 0)}"
        for row in origins
    )
    mixed = " MIXED" if len(origins) > 1 else ""
    return f"caption origins: {len(origins)} distinct over {captions} captions{mixed} [{labels}]"


def caption_origin_note(attempt: Path, asset_id: str) -> str:
    """What produced one picture's caption, out of the snapshot rather than the live bank.

    Reconfiguring the caption server after the fact must not change this answer,
    which is why it is read from the run and never from the current config.
    """
    try:
        preparation = json.loads((attempt / "preparation.private.json").read_text())
    except (OSError, ValueError):
        return "Caption origin: unknown (not recorded for this run)"
    provenance = preparation.get("caption_provenance")
    if not isinstance(provenance, Mapping):
        return "Caption origin: unknown (not recorded for this run)"
    origins = _origins(provenance)
    if not origins:
        # A tier that asked for no captions records no origins and owes no sentence.
        return ""
    index = _mapping(provenance.get("by_asset")).get(asset_id, 0)
    origin = origins[index] if isinstance(index, int) and index < len(origins) else origins[0]
    if origin.get("status") == _NONE:
        return ""
    if origin.get("status") == _UNKNOWN:
        return "Caption origin: unknown (not recorded with this caption)"
    return "Caption origin: " + _describe(origin)
