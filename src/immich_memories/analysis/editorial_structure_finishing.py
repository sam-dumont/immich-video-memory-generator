"""The passes a settled cut goes through, and the run they mutate.

Once the selection has produced carriers the film is still edited: motion and timing are
resolved over it, later contributions are observed, the family-viewing gate judges it, the
duplicate review reads it and the timing budget trims it. Every one of them mutates the same
run, which is why they sit together, and why the planner beside them keeps only the decisions
that build one.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from immich_memories.analysis import editorial_shareability as _share
from immich_memories.analysis.editorial_completion import (
    ACCEPTED_SHORTFALL_FRACTION,
    RetainedMotion,
)
from immich_memories.analysis.editorial_final_hash_review import review_cut_by_cached_hashes
from immich_memories.analysis.editorial_source_route import retire_unprojectable
from immich_memories.analysis.editorial_story_planner import alternatives_pool
from immich_memories.analysis.editorial_story_trim import trim_to_timing_budget
from immich_memories.analysis.editorial_structure_audience import (
    AudienceGate,
    close_share_log,
    open_share_log,
)
from immich_memories.analysis.editorial_structure_budget import MIN_CARRIER_SECONDS
from immich_memories.analysis.editorial_structure_contract import (
    StructurePlannerPorts,
    StructurePlanningInput,
)
from immich_memories.analysis.editorial_structure_material import Material, Wall
from immich_memories.analysis.editorial_structure_record import shave_content_duration
from immich_memories.analysis.editorial_unvouched_filler import (
    FillerEvidence,
    drop_unvouched_filler,
)
from immich_memories.operations.cut_progress import StageUpdate, announce_stage


def announce_count(pictures: int, point: str) -> None:
    """The edit's own counts, said out loud instead of only written to a record."""
    announce_stage(StageUpdate(f"Editing the memory: {pictures} pictures {point}"))


@dataclass
class PlanRun:
    """The mutable result of one planning run, before it is written down."""

    carriers: list[dict] = field(default_factory=list)
    cut_carriers: list[dict] = field(default_factory=list)
    selection_stages: dict = field(default_factory=dict)
    shaved: int = 0
    render_timeline: Any = None
    final_content_cap: float = 0.0
    motion_metrics: dict = field(default_factory=dict)
    final_duplicates: dict = field(
        default_factory=lambda: {
            "status": "unavailable",
            "reason": "the duplicate review has not run",
        }
    )
    # Binds a kept carrier's unmeasured Live stitch to its measurement (`measured_stitch`).
    bind_stitch: Callable[[dict], dict] | None = None


def resolve_motion_and_timing(
    run: PlanRun, source: StructurePlanningInput, ports: StructurePlannerPorts
) -> None:
    # Reviews must see the rendering that ordinary motion measurement resolved.
    # Completion reuses this instance for survivors and newly retained additions.
    retained_motion = RetainedMotion(ports.resolve_motion, run.bind_stitch)
    run.carriers = retained_motion(run.carriers)
    run.selection_stages["before_picture_review"] = len(run.carriers)
    announce_count(len(run.carriers), "going into the picture review")
    # Audience-eligible funded pictures and completion additions reuse prior results.
    run.carriers = retained_motion(run.carriers)
    run.motion_metrics = retained_motion.metrics
    run.carriers = retire_unprojectable(run.carriers, source, run.cut_carriers)
    if ports.resolve_speech is not None:
        run.carriers = ports.resolve_speech(run.carriers)
    timing = source.render_timing
    if timing is None:
        return
    if (
        timing.target_seconds != source.case.target_seconds
        or timing.memory_type != source.case.product
    ):
        raise ValueError("Editorial render timing disagrees with the requested memory")
    run.carriers, dropped = trim_to_timing_budget(
        run.carriers,
        lambda cs: timing.resolve(cs, source.assets).content_budget,
        MIN_CARRIER_SECONDS,
        protected=frozenset(source.owner_required_asset_ids),
    )
    run.cut_carriers.extend(dropped)
    run.render_timeline = timing.resolve(run.carriers, source.assets)
    run.final_content_cap = run.render_timeline.content_budget
    run.shaved += shave_content_duration(run.carriers, run.final_content_cap)
    if sum(c["seconds"] for c in run.carriers) > run.final_content_cap:
        raise ValueError("Editorial minimum content cannot fit the production title budget")


def replacement_offers(pool_for: Callable[[Mapping[str, Any]], Sequence[Mapping[str, Any]]]):
    """Label the audience gate's own pool by where each offer comes from.

    The pool a held carrier draws on is already the moment's other pictures first, in the
    order the quality key ranked them, and then the story's unshown moments. Naming the two
    rungs is all the duplicate review needs to say which one refilled a slot.
    """

    def offers(carrier: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
        moment = set(carrier.get("moment_alternatives") or ())
        return [
            ("moment" if unit.get("asset_id") in moment else "story", unit)
            for unit in pool_for(carrier)
        ]

    return offers


def _settle_replacements(run: PlanRun, ports: StructurePlannerPorts, added: Sequence[str]) -> None:
    """A refilled slot arrives after motion was resolved and the budget settled.

    Its picture is resolved like any other retained unit, and the film is fitted to the cap
    it was already fitted to, so a replacement cannot buy the film length the trim refused.
    """
    if not added:
        return
    filled = set(added)
    retained = RetainedMotion(ports.resolve_motion, run.bind_stitch)
    resolved = {
        c["asset_id"]: c for c in retained([c for c in run.carriers if c["asset_id"] in filled])
    }
    run.carriers = [resolved.get(c["asset_id"], c) for c in run.carriers]
    if run.final_content_cap > 0:
        run.shaved += shave_content_duration(run.carriers, run.final_content_cap)


def final_duplicate_review(
    run: PlanRun,
    ports: StructurePlannerPorts,
    *,
    prior,
    prior_assets: set[str],
    owner_required: Sequence[str] = (),
    replacements_for: Callable[[Mapping[str, Any]], Sequence[tuple[str, Mapping[str, Any]]]]
    | None = None,
    close_family_of: Callable[[str], Collection[str]] = lambda _asset: (),
    gate: AudienceGate | None = None,
) -> None:
    """Audit the completed film, including later contributions and the actual
    resolved render kinds. Nothing may refill a removed duplicate afterward.

    A close family member's only shot never leaves: the review keeps it ahead of its
    look-alike. A refill arrives after the audience gate ran, so `gate` judges it like any
    other carrier: its banked verdict when there is one, a new question otherwise, and a
    refused refill leaves the slot to the next offer or empty."""
    protected = sorted(
        (prior_assets - set(prior.get("review_proposed_assets", [])) if prior else set())
        | set(owner_required)
    )
    before_duplicates = run.carriers.copy()
    # The preview hashes the burst pass already cached and the scene prints ingest banked ask
    # the repetition question over the whole finished cut, whatever the reader is. No tier
    # sends the pair's pixels to a model: pictures are read once, at ingest.
    run.carriers, run.final_duplicates = review_cut_by_cached_hashes(
        run.carriers,
        thumbnail_hash=ports.thumbnail_hash,
        protected_asset_ids=protected,
        replacements_for=replacements_for,
        scene_print=ports.scene_print,
        close_family_of=close_family_of,
        admits=_admitted_by(gate),
        # A scene repeat nothing replaces leaves only while the film still reaches its target
        # within the shortfall the owner accepts: a film short of material keeps it.
        content_floor=run.final_content_cap * (1 - ACCEPTED_SHORTFALL_FRACTION)
        if run.final_content_cap > 0
        else math.inf,
    )
    known = {carrier["asset_id"] for carrier in before_duplicates}
    refilled = [c for c in run.carriers if c["asset_id"] not in known]
    run.final_duplicates["status"] = (
        "incomplete" if run.final_duplicates["incomplete"] else "complete"
    )
    removed = {row["asset_id"]: row for row in run.final_duplicates["removals"]}
    run.cut_carriers.extend(
        carrier
        | {
            "reason": "Final visual duplicate of retained source "
            + removed[carrier["asset_id"]]["keeper"],
            "review_stage": "final-duplicates",
        }
        for carrier in (*before_duplicates, *refilled)
        if carrier["asset_id"] in removed
    )
    _settle_replacements(
        run, ports, [row["replacement"] for row in removed.values() if "replacement" in row]
    )


def _admitted_by(gate: AudienceGate | None) -> Callable[[Mapping[str, Any]], bool]:
    if gate is None:
        return lambda _row: True
    return lambda row: _share.allowed(gate.verdict_of(row), gate.audience)


def held_by_gate(gate: AudienceGate, unit_of: Mapping[str, Mapping[str, Any]]):
    """Whether the audience gate refuses a picture for this film, asked one picture at a time."""

    def held(asset_id: str) -> bool:
        verdict = gate.verdict_of(unit_of[asset_id])
        return verdict is not None and not _share.allowed(verdict, gate.audience)

    return held


def seat_again_after_review(
    run: PlanRun, ports: StructurePlannerPorts, seat: Callable[[list[dict]], list[dict]]
) -> None:
    """Seat a close family member again when a pass after the seat took their only shot.

    The seat runs on the draft; the audience gate, the timing trim and the duplicate reviews
    all come after it and can each remove a frame. The finished film is checked once more, a
    frame that gives up its place is on the cut record, and a new seat is resolved and fitted
    like any refilled slot.
    """
    before = run.carriers
    seated = seat(before)
    known = {c["asset_id"] for c in before}
    kept = {c["asset_id"] for c in seated}
    run.cut_carriers.extend(
        carrier
        | {
            "reason": "Gave its place to a close family member's only shot",
            "review_stage": "family-seat",
        }
        for carrier in before
        if carrier["asset_id"] not in kept
    )
    run.carriers = sorted(seated, key=lambda c: str(c.get("taken", "")))
    _settle_replacements(run, ports, [c["asset_id"] for c in seated if c["asset_id"] not in known])


def trim_to_timing(
    run: PlanRun, source, record, *, protected: frozenset[str] = frozenset()
) -> None:
    """The production title budget depends on what was selected (a divider per month shown):
    a memory across ten years holds ten dividers. Fit the minimum content to that budget now,
    dropping from the least weighed stories, rather than dying in the tail's guard."""
    if source.render_timing is None:
        return
    run.carriers, dropped = trim_to_timing_budget(
        run.carriers,
        lambda cs: source.render_timing.resolve(cs, source.assets).content_budget,
        MIN_CARRIER_SECONDS,
        protected=protected,
    )
    record("timing-trim", {"dropped": [c["asset_id"] for c in dropped], "kept": len(run.carriers)})


def apply_audience_gate(
    run: PlanRun, gate: AudienceGate, selection, material: Material, wall: Wall
) -> dict:
    before_privacy = run.carriers.copy()
    run.carriers, share_log = _share.apply_gate(
        # Same temporal family does not establish editorial equivalence. Let the
        # existing bounded assembly decision judge a new contribution after privacy.
        run.carriers,
        verdict_of=gate.verdict_of,
        # Story-first has no ladder to fall back on: the moment's own other pictures are
        # the only replacements a held carrier can have.
        pool_for=alternatives_pool(selection, material.units, wall.anchor_label),
        audience=gate.audience,
    )
    open_share_log(share_log, funded_acquisition={})
    run.selection_stages["after_shareability"] = len(run.carriers)
    close_share_log(
        share_log,
        gate,
        never_auto_excluded=material.builder.never_auto_excluded,
        anchor_label=wall.anchor_label,
        ineligible=material.ineligible,
    )
    gate.exclude_refused_members(before_privacy)
    return share_log


def drop_filler_nothing_vouches_for(run: PlanRun, evidence: FillerEvidence, record) -> None:
    """The no-model film's last pass: filler that shows nothing leaves, and no pass refills it."""
    run.carriers, dropped = drop_unvouched_filler(run.carriers, evidence)
    run.cut_carriers.extend(
        carrier
        | {
            "reason": "Filler with no indicator that the frame head reads as showing nothing",
            "review_stage": "unvouched-filler",
        }
        for carrier in dropped
    )
    run.selection_stages["after_unvouched_filler"] = len(run.carriers)
    record(
        "unvouched-filler",
        {
            "dropped": [
                {"asset_id": c["asset_id"], "frame_kind": evidence.frame_kind_of(c["asset_id"])}
                for c in dropped
            ],
            "kept": len(run.carriers),
        },
    )
