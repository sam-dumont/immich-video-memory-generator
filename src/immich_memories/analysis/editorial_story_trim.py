"""The allocation in reverse: which carriers leave when the production budget is tighter.

The production title budget depends on what was selected (a divider per month shown), so the
story planner's slots can overshoot it; this trim fits the minimum content back inside it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


def trim_to_timing_budget(
    carriers: list[dict],
    content_budget_of: Callable[[list[dict]], float],
    min_seconds: float,
    protected: frozenset[str] = frozenset(),
) -> tuple[list[dict], list[dict]]:
    """Drop carriers until their minimum content fits the production content budget of what remains.

    The budget depends on the selection (a month divider per month shown), so it is re-resolved
    after every drop. Drop order: the least weighed story first, and inside a story its latest
    picture; a story's only picture goes only when no lighter story still has one. A protected
    carrier (one the owner required) is never a victim; when only those remain the trim stops.
    A dropped carrier carries the reason it was cut, which the selection sheet prints.
    """
    from immich_memories.speech.cuts import minimum_duration

    kept = carriers.copy()
    dropped: list[dict] = []
    while kept:
        budget = content_budget_of(kept)
        if sum(minimum_duration(c, min_seconds) for c in kept) <= budget + 1e-6:
            break
        counts: dict[str, int] = {}
        for c in kept:
            counts[c.get("story_episode") or ""] = counts.get(c.get("story_episode") or "", 0) + 1
        ranked = [(_drop_rank(c, counts), c) for c in kept if c["asset_id"] not in protected]
        if not ranked:
            break
        best = min(rank for rank, _c in ranked)
        if best >= 14 and len(kept) == 1:
            break  # the dominant story's only picture stays whatever the budget says
        victim = max(
            (c for rank, c in ranked if rank == best), key=lambda c: c.get("taken") or ""
        )  # latest first
        kept.remove(victim)
        dropped.append(
            victim
            | {
                "reason": f"Cut to fit the film's {budget:.1f} s of content",
                "review_stage": "timing-trim",
            }
        )
    return kept, dropped


_DROP_ORDER = {"none": 0, "glimpse": 1, "minor": 2, "major": 3, "dominant": 4}


def _drop_rank(c: Mapping[str, Any], counts: Mapping[str, int]) -> int:
    """The allocation in reverse: glimpses first, then extra pictures lightest story first,
    then only pictures lightest story first; the dominant story's only picture last of all."""
    weight = _DROP_ORDER.get(str(c.get("story_weight")), 0)
    if weight <= 1:
        return 0
    return weight if counts[c.get("story_episode") or ""] > 1 else 10 + weight
