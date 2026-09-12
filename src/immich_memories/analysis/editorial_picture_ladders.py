"""How much picture depth a playable duration can carry."""

from __future__ import annotations

from immich_memories.analysis.editorial_structure_budget import (
    CONTENT_RESERVE_SECONDS,
    MIN_CARRIER_SECONDS,
)


def depth_cap(target_seconds: float) -> int:
    """Bound depth by playable time; editorial funding and source capacity decide its use."""
    return int(max(0, target_seconds - CONTENT_RESERVE_SECONDS) // MIN_CARRIER_SECONDS)
