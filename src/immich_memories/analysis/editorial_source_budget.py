"""Bound automatic analysis before previews, captions or model reading are requested."""

from __future__ import annotations

import json
import logging
import math
from collections import defaultdict
from collections.abc import Callable, Collection, Mapping
from dataclasses import replace
from time import monotonic
from typing import TYPE_CHECKING

from immich_memories.analysis.editorial_contracts import EditorialCandidate
from immich_memories.analysis.editorial_episode_context import capture_episode_context
from immich_memories.analysis.editorial_people import EditorialPeople
from immich_memories.analysis.editorial_story_shortlist import _spread
from immich_memories.analysis.selection_source import PreparedEditorialSource
from immich_memories.analysis.selection_trace import Trace
from immich_memories.operations.cut_progress import StageUpdate
from immich_memories.security import write_secret_file

if TYPE_CHECKING:
    from immich_memories.analysis.editorial_orchestration import TextEditorialPlanner
    from immich_memories.analysis.editorial_runtime import EditorialRunContext
    from immich_memories.config_loader import Config

logger = logging.getLogger(__name__)
SOURCE_BUDGET_VERSION = "source-budget-v1"


def source_candidate_limit(target_seconds: float, photo_seconds: float) -> int:
    """Offer four candidates per approximate film slot, with room for short-film variety."""
    return max(384, 4 * math.ceil(target_seconds / photo_seconds))


def shortlist_sources(
    prepared: PreparedEditorialSource, *, requested_ids: Collection[str], limit: int
) -> tuple[str, ...]:
    """Share work across calendar periods, then capture groups and alternatives."""
    if len(prepared.candidates) <= limit:
        return prepared.candidate_ids
    required = set(prepared.owner_required_asset_ids)
    requested = set(requested_ids) | required
    offered = [candidate for candidate in prepared.candidates if candidate.asset_id in requested]
    selected = {candidate.asset_id for candidate in offered if candidate.asset_id in required}
    groups = [
        [
            candidate
            for candidate in group.candidates
            if candidate.asset_id in requested and candidate.asset_id not in selected
        ]
        for group in prepared.moment_groups
    ]
    remaining = max(0, limit - len(selected))
    buckets = _calendar_buckets([group for group in groups if group], remaining)
    grants = _share_budget([sum(map(len, bucket)) for bucket in buckets], remaining)
    for bucket, grant in zip(buckets, grants, strict=True):
        selected.update(_sample_bucket(bucket, grant))
    return tuple(
        candidate.asset_id for candidate in prepared.candidates if candidate.asset_id in selected
    )


def _sample_bucket(groups: list[list[EditorialCandidate]], budget: int) -> list[str]:
    groups = _funded_groups(groups, budget)
    counts = _share_budget([len(group) for group in groups], budget)
    selected: list[str] = []
    for group, count in zip(groups, counts, strict=True):
        stars = [candidate for candidate in group if candidate.favourite]
        others = [candidate for candidate in group if not candidate.favourite]
        chosen = _spread(stars, min(count, len(stars)))
        chosen += _spread(others, count - len(chosen))
        selected.extend(candidate.asset_id for candidate in chosen)
    return selected


def _calendar_buckets(
    groups: list[list[EditorialCandidate]], budget: int
) -> list[list[list[EditorialCandidate]]]:
    # Use the finest calendar unit that leaves a few alternatives per period.
    # Capture volume within a day must not crowd quieter days out of a month.
    buckets: dict[tuple[int, ...], list[list[EditorialCandidate]]] = defaultdict(list)
    for scale in ("day", "week", "month", "quarter", "year"):
        buckets.clear()
        for group in groups:
            taken = group[0].taken_at
            key = {
                "day": (taken.year, taken.month, taken.day),
                "week": tuple(taken.isocalendar()[:2]),
                "month": (taken.year, taken.month),
                "quarter": (taken.year, (taken.month - 1) // 3),
                "year": (taken.year,),
            }[scale]
            buckets[key].append(group)
        if len(buckets) <= max(1, budget // 3):
            break
    return _spread(list(buckets.values()), budget)


def _share_budget(capacities: list[int], budget: int) -> list[int]:
    grants = [0] * len(capacities)
    remaining = min(budget, sum(capacities))
    while remaining:
        for index, capacity in enumerate(capacities):
            if remaining and grants[index] < capacity:
                grants[index] += 1
                remaining -= 1
    return grants


def _funded_groups(
    groups: list[list[EditorialCandidate]], budget: int
) -> list[list[EditorialCandidate]]:
    if not budget:
        return []
    # A few alternatives let the editor reject an awkward frame and keep a
    # short Live sequence. Singletons release that capacity to other events.
    count = math.ceil(budget / 3)
    starred = [group for group in groups if any(candidate.favourite for candidate in group)]
    ordinary = [group for group in groups if not any(candidate.favourite for candidate in group)]
    picked = _spread(starred, count)
    picked += _spread(ordinary, count - len(picked))
    shortfall = budget - sum(len(group) for group in picked)
    if shortfall > 0:
        chosen = {group[0].asset_id for group in picked}
        picked += _spread([group for group in groups if group[0].asset_id not in chosen], shortfall)
    return sorted(picked, key=lambda group: (group[0].taken_at, group[0].asset_id))


def prepare_budgeted_source(
    planner: TextEditorialPlanner,
    prepare_annotations: Callable[
        [PreparedEditorialSource, Callable[[StageUpdate], None] | None], Mapping[str, str]
    ],
    context: EditorialRunContext,
    config: Config,
    requested_ids: Collection[str],
    *,
    people: EditorialPeople,
    trace: Trace,
    on_stage: Callable[[StageUpdate], None] | None,
) -> PreparedEditorialSource:
    """Apply one shared work budget before production preparation, retaining a full refusal trace."""
    started = monotonic()
    preliminary = planner.prepare_source(trace=Trace(), include_previews=False)
    limit = source_candidate_limit(context.target_seconds, config.photos.duration)
    selected = shortlist_sources(
        preliminary,
        requested_ids=requested_ids,
        limit=limit,
    )
    kept = set(selected)
    metadata_seconds = monotonic() - started
    episode_context = capture_episode_context(preliminary, kept, people=people, config=config)
    context_seconds = monotonic() - started - metadata_seconds
    exclusions = {
        candidate.asset_id: "outside analysis budget"
        for candidate in preliminary.candidates
        if candidate.asset_id not in kept
    }
    write_secret_file(
        context.artifact_dir / "source-budget.private.json",
        json.dumps(
            {
                "version": SOURCE_BUDGET_VERSION,
                "eligible": len(preliminary.candidates),
                "candidate_limit": limit,
                "retained": len(selected),
                "omitted": len(exclusions),
                "owner_required": len(preliminary.owner_required_asset_ids),
                "retained_asset_ids": selected,
                "metadata_seconds": metadata_seconds,
                "context_seconds": context_seconds,
            },
            indent=2,
        ),
    )
    if exclusions:
        label = f"Choosing {len(selected)} candidates from {len(preliminary.candidates)} sources"
        logger.info(label)
        if on_stage is not None:
            on_stage(StageUpdate(label))
        preliminary = planner.prepare_source(
            trace=Trace(),
            evidence_exclusions=exclusions,
            include_previews=False,
        )
    exclusions.update(prepare_annotations(preliminary, on_stage))
    prepared = planner.prepare_source(trace=trace, evidence_exclusions=exclusions)
    return replace(
        prepared,
        episode_context={key: episode_context[key] for key in prepared.candidate_ids},
    )
