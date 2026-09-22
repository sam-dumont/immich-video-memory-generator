"""The passes a settled cut goes through, and the run they mutate.

Once the selection has produced carriers the film is still edited: motion and timing are
resolved over it, later contributions are observed, the family-viewing gate judges it, the
duplicate review reads it and the timing budget trims it. Every one of them mutates the same
run, which is why they sit together, and why the planner beside them keeps only the decisions
that build one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from immich_memories.analysis import editorial_shareability as _share
from immich_memories.analysis.editorial_completion import RetainedMotion
from immich_memories.analysis.editorial_final_attached import AttachedMaterialEvidence
from immich_memories.analysis.editorial_final_hash_review import review_cut_by_cached_hashes
from immich_memories.analysis.editorial_final_sampled_duplicates import (
    displayed_sample_members,
    reduce_final_sampled_duplicates,
)
from immich_memories.analysis.editorial_picture_evidence import PictureEvidenceOverlay
from immich_memories.analysis.editorial_source_route import retire_unprojectable
from immich_memories.analysis.editorial_story_planner import alternatives_pool
from immich_memories.analysis.editorial_story_trim import trim_to_timing_budget
from immich_memories.analysis.editorial_structure_audience import (
    AudienceGate,
    close_share_log,
    open_share_log,
    tighten_with_attached_samples,
)
from immich_memories.analysis.editorial_structure_budget import MIN_CARRIER_SECONDS
from immich_memories.analysis.editorial_structure_contract import (
    StructurePlannerPorts,
    StructurePlanningInput,
)
from immich_memories.analysis.editorial_structure_material import Material, Wall
from immich_memories.analysis.editorial_structure_record import shave_content_duration
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
    attached_audience: dict = field(default_factory=dict)
    final_duplicates: dict = field(
        default_factory=lambda: {
            "status": "unavailable",
            "reason": "sampled comparison ports absent",
        }
    )


def resolve_motion_and_timing(
    run: PlanRun, source: StructurePlanningInput, ports: StructurePlannerPorts
) -> None:
    # Reviews must see the rendering that ordinary motion measurement resolved.
    # Completion reuses this instance for survivors and newly retained additions.
    retained_motion = RetainedMotion(ports.resolve_motion)
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


def observe_attached(
    run: PlanRun,
    ports: StructurePlannerPorts,
    gate: AudienceGate,
    picture_evidence: PictureEvidenceOverlay,
    relation_records: dict,
    share_log: dict,
) -> tuple[AttachedMaterialEvidence, bool]:
    attached = AttachedMaterialEvidence()
    if ports.observe_attached_material is None or not any(
        carrier["kind"] == "live-motion" for carrier in run.carriers
    ):
        return attached, False
    # Samples and the strict renderer must agree on the final selected interval.
    if run.render_timeline is None:
        run.shaved += shave_content_duration(run.carriers, run.final_content_cap)
    attached = ports.observe_attached_material(run.carriers)
    if set(attached.records) & picture_evidence.records.keys():
        raise ValueError("attached sample identity collides with primary picture evidence")
    relation_records.update({key: dict(record) for key, record in attached.records.items()})
    run.carriers = tighten_with_attached_samples(
        run.carriers,
        gate=gate,
        attached_evidence=attached,
        attached_audience=run.attached_audience,
        share_log=share_log,
        cut_carriers=run.cut_carriers,
    )
    return attached, True


def _sampled_duplicate_review(
    run: PlanRun,
    ports: StructurePlannerPorts,
    *,
    source_relation,
    episode_relation,
    picture_records,
    attached: AttachedMaterialEvidence,
    protected: Sequence[str],
    quality,
    pixel_facts,
):
    """The model review: nominate a pair from what its pictures were described as holding,
    confirm it against their conserved pixels. None when this run has no ports to ask with."""
    if source_relation is None or ports.sampled_preview_hashes is None:
        return None
    final_records = {**picture_records, **attached.records}
    carried = {carrier["asset_id"] for carrier in run.carriers}
    final_members = {
        key: members for key, members in attached.displayed_members.items() if key in carried
    }
    displayed_ids = tuple(
        sorted(
            {
                member
                for carrier in run.carriers
                for member in final_members.get(
                    carrier["asset_id"], displayed_sample_members(carrier)
                )
            }
        )
    )
    return reduce_final_sampled_duplicates(
        run.carriers,
        picture_records=final_records,
        preview_hashes=ports.sampled_preview_hashes(displayed_ids, final_records),
        confirm_relation=source_relation,
        confirm_episode_relation=episode_relation,
        bound_sample_members=final_members,
        protected_asset_ids=protected,
        objective_quality={
            c["asset_id"]: quality(c["asset_id"])
            for c in run.carriers
            if c["asset_id"] in pixel_facts
        },
    )


def final_duplicate_review(
    run: PlanRun,
    ports: StructurePlannerPorts,
    *,
    source_relation,
    episode_relation,
    picture_records,
    attached: AttachedMaterialEvidence,
    prior,
    prior_assets: set[str],
    quality,
    pixel_facts,
    owner_required: Sequence[str] = (),
) -> None:
    """Audit the completed film, including later contributions and the actual
    resolved render kinds. Nothing may refill a removed duplicate afterward."""
    protected = sorted(
        (prior_assets - set(prior.get("review_proposed_assets", [])) if prior else set())
        | set(owner_required)
    )
    before_duplicates = run.carriers.copy()
    if ports.rules is not None:
        # The sampled review needs a reader to confirm a nominated pair, so a no-model film
        # ended with no review at all while a model film ended with one. The preview hashes
        # the burst pass already cached ask the same question over the whole finished cut.
        reviewed = review_cut_by_cached_hashes(
            run.carriers, thumbnail_hash=ports.thumbnail_hash, protected_asset_ids=protected
        )
    else:
        reviewed = _sampled_duplicate_review(
            run,
            ports,
            source_relation=source_relation,
            episode_relation=episode_relation,
            picture_records=picture_records,
            attached=attached,
            protected=protected,
            quality=quality,
            pixel_facts=pixel_facts,
        )
    if reviewed is None:
        return
    run.carriers, run.final_duplicates = reviewed
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
        for carrier in before_duplicates
        if carrier["asset_id"] in removed
    )


def check_empty_attached(ports: StructurePlannerPorts, observed: bool) -> None:
    if ports.observe_attached_material is None or observed:
        return
    empty_attached = ports.observe_attached_material([])
    if any(
        (
            empty_attached.records,
            empty_attached.displayed_members,
            empty_attached.observed_members,
            empty_attached.gaps,
        )
    ):
        raise ValueError("empty attached material produced nonempty evidence")


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
