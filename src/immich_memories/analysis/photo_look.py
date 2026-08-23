"""What a look at a photograph does to a finished selection.

The VLM shortlist is a budget — on a real year, thirty photographs out of
1918 — and selection picks from all of them. So most stills in a finished cut
have never been looked at, and the holistic review, told never to drop a clip
for missing information, cannot judge any of them.

These two passes close that, bounded to what shipped rather than the library:
look at the selected stills, and let a day reconsider its own frame once its
candidates have been seen.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from immich_memories.analysis.source_filter import is_a_still

if TYPE_CHECKING:
    from immich_memories.analysis.smart_pipeline import ClipWithSegment

logger = logging.getLogger(__name__)

# How many of a day's stills a re-pick may look at. The day is the bound, not
# the library: a library of looks is what the shortlist budget exists to
# avoid, and a day rarely needs more than a handful to find its best frame.
_CANDIDATES_PER_DAY = 4


def _looks_for(
    members: list[ClipWithSegment],
    *,
    config: Any,
    client: Any,
    provider_circuit: Any,
) -> dict[str, tuple[float, dict]]:
    """Ask the photo scorer about these stills, or nothing if it cannot answer."""
    from immich_memories.photos import photo_pipeline

    if not members:
        return {}
    try:
        return photo_pipeline.look_at_selected_photos(
            [m.clip.asset for m in members],
            config=config,
            client=client,
            provider_circuit=provider_circuit,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        logger.debug("Photo look failed: %s", type(exc).__name__)
        return {}


def _apply(member: ClipWithSegment, look: tuple[float, dict] | None) -> None:
    """Take both halves of a look: what it saw, and what it made of it.

    The caption and the rank come out of the same call. Keeping only the words
    let a look change what the review reads and never what selection ranked
    on, so a frame the model could not identify outranked one it understood.
    """
    if look is None:
        return
    from immich_memories.analysis.cache_projection import apply_semantic_payload

    member.score, payload = look
    apply_semantic_payload(member.clip, payload)


def look_at_stills(
    stills: list[ClipWithSegment],
    *,
    config: Any,
    client: Any,
    provider_circuit: Any = None,
) -> None:
    """Give the review eyes on the photographs that reached the cut.

    A still's real look is the photo scorer: the video analyzer fails on a
    photograph and writes back a zero, so a photo it could not read was not
    merely unseen but ranked last.
    """
    if not stills:
        return
    logger.info("Verify pass: looking at %d selected photo(s)", len(stills))
    looks = _looks_for(stills, config=config, client=client, provider_circuit=provider_circuit)
    for member in stills:
        _apply(member, looks.get(member.clip.asset.id))


def repick_days(
    *,
    selected: list[ClipWithSegment],
    pool: list[ClipWithSegment],
    config: Any,
    client: Any,
    provider_circuit: Any = None,
) -> list[ClipWithSegment]:
    """Let a day reconsider its own frame once its candidates are looked at.

    A still is chosen on metadata, long before anything sees it. Measured on a
    real day: the frame that shipped was a close-up the model read as "dried,
    flaky green leaves, possibly herbs or tea" at interest 0.4, while the same
    day's other frame was understood outright — "a white plastic fermentation
    bucket, equipped with an airlock and spigot" — at 0.6. Both were looked
    at, the better one scored higher, and the worse one still shipped.

    Bounded to the day rather than the library: only days that already have a
    selected still are reconsidered, and only against that day's own
    candidates. The look is cached, so a candidate seen before costs nothing.
    """
    chosen = {m.clip.asset.id for m in selected}
    days = {
        m.clip.asset.file_created_at.date()
        for m in selected
        if is_a_still(m.clip.asset) and m.clip.asset.file_created_at
    }
    if not days:
        return selected

    by_day: dict[Any, list[ClipWithSegment]] = {}
    for member in pool:
        when = member.clip.asset.file_created_at
        if is_a_still(member.clip.asset) and when and when.date() in days:
            by_day.setdefault(when.date(), []).append(member)

    looking_at = [
        member
        for day_members in by_day.values()
        for member in sorted(day_members, key=lambda c: c.score, reverse=True)[:_CANDIDATES_PER_DAY]
    ]
    looks = _looks_for(looking_at, config=config, client=client, provider_circuit=provider_circuit)
    for member in looking_at:
        _apply(member, looks.get(member.clip.asset.id))

    swaps = {}
    for day, day_members in by_day.items():
        swap = _better_frame_for(day, day_members, chosen)
        if swap:
            swaps.update(swap)

    return [swaps.get(m.clip.asset.id, m) for m in selected] if swaps else selected


def _better_frame_for(
    day: Any,
    day_members: list[ClipWithSegment],
    chosen: set[str],
) -> dict[str, ClipWithSegment]:
    """The swap this day wants, if looking at it changed the answer."""
    here = [m for m in day_members if m.clip.asset.id in chosen]
    if not here:
        return {}
    best = max(day_members, key=lambda c: c.score)
    weakest_here = min(here, key=lambda c: c.score)
    if best.clip.asset.id in chosen or best.score <= weakest_here.score:
        return {}
    logger.info(
        "Day re-pick %s: %s reads better than %s once both were looked at",
        day,
        best.clip.asset.id,
        weakest_here.clip.asset.id,
    )
    return {weakest_here.clip.asset.id: best}
