"""The shots worth a second look before the film leaves the house.

The exposure head is read at 0.5 and stays there: dropping the cut to 0.1 holds 51 % of a
baby month, which is not a film. But between 0.2 and 0.5 the head is neither sure nor
wrong -- underwear in particular sits there -- and those shots are in the cut with nothing
said about them.

So a run writes them down. The list changes no verdict and removes no shot: it names the
carriers in that band that no other hold already keeps to the family, with the head's own
probability, and the run's summary says how many there are. The owner decides.

A `family_only` verdict is the rules tier's answer for every carrier, so this matters most
to the model tier and to a `sendable` export, where a shot in the band is otherwise on its
way out of the house with no one having looked.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing, suppress
from pathlib import Path
from typing import Any, Protocol

from immich_memories.analysis.editorial_preparation_detectors import MARQO_HEAD
from immich_memories.security import write_secret_file

FILENAME = "review-before-sharing.private.json"
POLICY = "review-before-sharing-v1"
# The head's grey zone: certain enough to mention, not certain enough to hold.
GREY_LOW = 0.2
GREY_HIGH = 0.5
# Findings that already keep a carrier to the family for this reason. A shot one of these
# holds needs no second look: it has had one.
_ALREADY_HELD = frozenset(
    {
        "exposure_evidence",
        "exposure_chain",
        "clip_exposure",
        "nudity_shirtless_or_underwear",
        "private_activity",
        "owner_review_flag",
        "unresolved_exposure",
        "undecided_exposure",
        "invalid_body_observation",
        "undecided_body_observation",
        "unresolved_companion_exposure",
        # The owner looked at the picture itself and cleared it.
        "owner_cleared",
    }
)


def exposure_probabilities(
    store_path: Path | str | None, asset_ids: Sequence[str], version: str
) -> dict[str, float]:
    """The exposure head's own probability per source, from the bank it already wrote.

    The row carries the probability beside the label, so nothing new is computed and
    nothing new is stored. A source the head never decided is absent.
    """
    ids = list(dict.fromkeys(asset_ids))
    if not store_path or not ids or not Path(store_path).is_file():
        return {}
    found: dict[str, float] = {}
    with (
        suppress(sqlite3.Error),
        closing(sqlite3.connect(f"file:{store_path}?mode=ro", uri=True)) as connection,
    ):
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            marks = ",".join("?" * len(chunk))
            rows = connection.execute(
                "SELECT asset_id, confidence FROM head_facts "  # noqa: S608
                f"WHERE head=? AND version=? AND asset_id IN ({marks})",
                (MARQO_HEAD, version, *chunk),
            )
            for asset_id, confidence in rows:
                if isinstance(confidence, (int, float)) and math.isfinite(confidence):
                    found[str(asset_id)] = float(confidence)
    return found


def to_check(
    carriers: Sequence[Mapping[str, Any]],
    verdicts: Mapping[str, Mapping[str, Any]],
    probabilities: Mapping[str, float],
) -> list[dict[str, Any]]:
    """The cut's shots in the head's grey zone that nothing else already holds."""
    rows = []
    for carrier in carriers:
        asset_id = str(carrier.get("asset_id") or "")
        probability = probabilities.get(asset_id)
        if probability is None or not GREY_LOW <= probability < GREY_HIGH:
            continue
        record = verdicts.get(asset_id, {})
        if record.get("finding") in _ALREADY_HELD:
            continue
        rows.append(
            {
                "asset_id": asset_id,
                "exposure_probability": round(probability, 5),
                "taken": carrier.get("taken"),
                "verdict": record.get("verdict", ""),
            }
        )
    return sorted(rows, key=lambda row: (-row["exposure_probability"], row["asset_id"]))


def write_review_list(directory: Path, rows: Sequence[Mapping[str, Any]]) -> int:
    """Write the list beside the cut and answer how long it is; an empty run writes an empty one."""
    write_secret_file(
        Path(directory) / FILENAME,
        json.dumps(
            {
                "policy": POLICY,
                "band": [GREY_LOW, GREY_HIGH],
                "head": MARQO_HEAD,
                "pictures": list(rows),
            },
            ensure_ascii=False,
            indent=1,
        ),
    )
    return len(rows)


class CutSource(Protocol):
    """Where a finished cut's list is written and which bank it reads."""

    @property
    def store_path(self) -> Path | None: ...

    @property
    def artifact_dir(self) -> Path: ...

    @property
    def config(self) -> Any: ...


def write_for_cut(
    source: CutSource,
    carriers: Sequence[Mapping[str, Any]],
    verdicts: Mapping[str, Mapping[str, Any]],
) -> int:
    """Name the finished cut's shots in the exposure head's grey zone. It changes no shot."""
    probabilities = exposure_probabilities(
        source.store_path,
        [str(carrier.get("asset_id")) for carrier in carriers],
        source.config.editorial.head_versions.get(MARQO_HEAD, ""),
    )
    return write_review_list(source.artifact_dir, to_check(carriers, verdicts, probabilities))


def _pictures(attempt_dir: Path | None) -> list[dict[str, Any]]:
    if attempt_dir is None:
        return []
    path = Path(attempt_dir) / FILENAME
    if not path.is_file():
        return []
    with suppress(OSError, ValueError):
        listed = json.loads(path.read_text()).get("pictures")
        if isinstance(listed, list):
            return listed
    return []


def review_count(attempt_dir: Path | None) -> int:
    """How many shots this run wants a look at; a run that wrote no list wants none."""
    return len(_pictures(attempt_dir))


def review_note(attempt_dir: Path | None, asset_id: str) -> str:
    """What ``runs why`` adds about one picture, or nothing when it is not on the list."""
    for row in _pictures(attempt_dir):
        if row.get("asset_id") == asset_id:
            return (
                f"  worth a look before sharing: the exposure head read "
                f"{row.get('exposure_probability')}, under the {GREY_HIGH} hold"
            )
    return ""
