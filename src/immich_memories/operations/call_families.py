"""Group the model calls of a run by the pipeline stage they belong to."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# A stage label carries its run inside it: an index, a moment id, and the words
# for the order, the slice of the table and the shape of the re-ask. A measurement
# wants the work, not the run, so all of them are dropped.
_RUN_WORDS = frozenset(
    {
        "source",
        "reversed",
        "hashed",
        "repair",
        "retry",
        "activity",
        "exposure",
        "page",
        "candidate",
        "context",
        "again",
    }
)


def family_of(stage: str) -> str:
    """Name the stage family of one call label, e.g. `story-pick-K01-repair` -> `story-pick`."""
    words: list[str] = []
    for token in stage.split("-"):
        if not (token.isascii() and token.isalpha() and token.islower()):
            break
        words.append(token)
    while len(words) > 1 and words[-1] in _RUN_WORDS:
        words.pop()
    return "-".join(words) or stage


def calls_by_family(calls: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int | float]]:
    """Count asked calls, reused answers and wall time per stage family, in the order run."""
    families: dict[str, dict[str, int | float]] = {}
    for call in calls:
        family = families.setdefault(
            family_of(str(call.get("stage", ""))),
            {"asked": 0, "cache_hits": 0, "wall_seconds": 0.0},
        )
        family["asked"] += 1
        family["cache_hits"] += int(bool(call.get("cache_hit")))
        family["wall_seconds"] = round(
            family["wall_seconds"] + float(call.get("wall_seconds") or 0.0), 3
        )
    return families
