"""Score, adjust, and rank memory candidates for generation priority."""

from __future__ import annotations

import math
from datetime import date

from immich_memories.automation.candidates import MemoryCandidate


def score_and_rank(
    candidates: list[MemoryCandidate],
    generated_keys: set[str],
    today: date,
    last_runs_by_type: dict[str, date],
) -> list[MemoryCandidate]:
    """Apply scoring adjustments and return candidates sorted highest-score-first.

    Adjustments applied in order:
    1. Never-generated boost (x1.2)
    2. Recency decay (linear over 365 days, floor 0.5)
    3. Content richness (log-scaled asset count, 30% weight)
    4. Same-type cooldown (x0.3 within 7 days, x0.7 within 30 days)
    5. Final clamp to [0.0, 1.0]
    """
    for candidate in candidates:
        score = candidate.score

        # 1. Never-generated boost
        if candidate.memory_key not in generated_keys:
            score *= 1.2

        # 2. Recency: linear decay from date_range_end to today, floor 0.5
        days_ago = (today - candidate.date_range_end).days
        recency = max(0.5, 1.0 - days_ago / 365.0)
        score *= recency

        # 3. Content richness: log(asset_count)/log(1000), capped at 1.0, 30% weight
        if candidate.asset_count > 0:
            richness = min(1.0, math.log(candidate.asset_count) / math.log(1000))
        else:
            richness = 0.0
        score = score * 0.7 + score * 0.3 * richness

        # 4. Same-type cooldown
        last_run = last_runs_by_type.get(candidate.memory_type)
        if last_run is not None:
            days_since = (today - last_run).days
            if days_since <= 7:
                score *= 0.3
            elif days_since <= 30:
                score *= 0.7

        # 5. Clamp
        candidate.score = max(0.0, min(1.0, score))

    # Deduplicate by memory_key — keep highest-scoring entry
    seen_keys: dict[str, int] = {}
    for i, c in enumerate(candidates):
        if c.memory_key in seen_keys:
            prev_idx = seen_keys[c.memory_key]
            if c.score > candidates[prev_idx].score:
                candidates[prev_idx].score = -1  # mark for removal
                seen_keys[c.memory_key] = i
            else:
                c.score = -1
        else:
            seen_keys[c.memory_key] = i
    candidates = [c for c in candidates if c.score >= 0]

    candidates.sort(key=lambda c: c.score, reverse=True)
    return _cap_per_type(candidates)


# Maximum candidates per memory_type — prevents any single type from flooding the list
_MAX_PER_TYPE = 3
_TYPE_CAPS = {
    "on_this_day": 1,  # At most 1 per run — can't verify day-level content quality
    "multi_person": 2,  # At most 2 pair suggestions per run
}


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
