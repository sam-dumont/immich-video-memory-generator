"""Score, adjust, and rank memory candidates for generation priority."""

from __future__ import annotations

import math
from datetime import date

from immich_memories.automation.candidates import MemoryCandidate

# Maximum candidates per memory_type — prevents any single type from flooding the list
_MAX_PER_TYPE = 3
_TYPE_CAPS = {
    "on_this_day": 1,  # At most 1 per run — can't verify day-level content quality
    "multi_person": 2,  # At most 2 pair suggestions per run
}


def score_and_rank(
    candidates: list[MemoryCandidate],
    generated_keys: set[str],
    today: date,
    last_runs_by_type: dict[str, date],
) -> list[MemoryCandidate]:
    """Apply scoring adjustments, dedup, and return candidates sorted highest-score-first."""
    for candidate in candidates:
        candidate.score = _adjust_score(candidate, generated_keys, today, last_runs_by_type)

    candidates = _dedup_by_key(candidates)
    candidates.sort(key=lambda c: c.score, reverse=True)
    return _cap_per_type(candidates)


def _adjust_score(
    candidate: MemoryCandidate,
    generated_keys: set[str],
    today: date,
    last_runs_by_type: dict[str, date],
) -> float:
    """Apply all scoring adjustments to a single candidate."""
    score = candidate.score

    # Never-generated boost
    if candidate.memory_key not in generated_keys:
        score *= 1.2

    # Recency: linear decay from date_range_end, floor 0.5
    days_ago = (today - candidate.date_range_end).days
    score *= max(0.5, 1.0 - days_ago / 365.0)

    # Content richness: log-scaled, 30% weight
    richness = min(1.0, math.log(max(1, candidate.asset_count)) / math.log(1000))
    score = score * 0.7 + score * 0.3 * richness

    # Same-type cooldown
    last_run = last_runs_by_type.get(candidate.memory_type)
    if last_run is not None:
        days_since = (today - last_run).days
        if days_since <= 7:
            score *= 0.3
        elif days_since <= 30:
            score *= 0.7

    return max(0.0, min(1.0, score))


def _dedup_by_key(candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
    """Remove duplicate memory_keys, keeping the highest-scoring entry."""
    seen: dict[str, int] = {}
    for i, c in enumerate(candidates):
        if c.memory_key in seen:
            prev_idx = seen[c.memory_key]
            if c.score > candidates[prev_idx].score:
                candidates[prev_idx].score = -1
                seen[c.memory_key] = i
            else:
                c.score = -1
        else:
            seen[c.memory_key] = i
    return [c for c in candidates if c.score >= 0]


def _cap_per_type(candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
    """Keep at most _MAX_PER_TYPE candidates per memory_type, preserving score order."""
    type_counts: dict[str, int] = {}
    result: list[MemoryCandidate] = []
    for c in candidates:
        count = type_counts.get(c.memory_type, 0)
        cap = _TYPE_CAPS.get(c.memory_type, _MAX_PER_TYPE)
        if count < cap:
            result.append(c)
            type_counts[c.memory_type] = count + 1
    return result
