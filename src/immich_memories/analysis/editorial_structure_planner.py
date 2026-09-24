"""Shared production structure planning over conserved factual inputs.

The algorithm is extracted intact from the validated matrix planner. File acquisition,
model transports, and lineage belong to adapters; this module owns editorial decisions.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import ChainMap
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from operator import itemgetter
from typing import Any

from immich_memories.analysis import llm_metrics
from immich_memories.analysis.editorial_block_votes import (
    judge_worthiness,
    load_vote_bank,
    save_vote_bank,
    worth_criterion_v44,
)
from immich_memories.analysis.editorial_cut_invariants import check_finished_cut
from immich_memories.analysis.editorial_episode_documents import factual_moment_rows
from immich_memories.analysis.editorial_exposure_chains import chain_holds_for
from immich_memories.analysis.editorial_family_seat import FilmSeatSource, seat_in_film
from immich_memories.analysis.editorial_home_radius import home_of, near_home_of
from immich_memories.analysis.editorial_owner_required import admit_owner_required
from immich_memories.analysis.editorial_picture_ladders import depth_cap
from immich_memories.analysis.editorial_review_list import write_for_cut
from immich_memories.analysis.editorial_rule_banked_facts import (
    NO_BANKED_FACTS,
    BankedAnswers,
    banked_leaders,
    configured_text_identity,
    open_banked_facts,
)
from immich_memories.analysis.editorial_rule_quality import rule_representative_rank
from immich_memories.analysis.editorial_rule_reader import RuleStructureReader
from immich_memories.analysis.editorial_sampled_reference import sampled_source_relation
from immich_memories.analysis.editorial_shareability_tiers import audience_check_for
from immich_memories.analysis.editorial_story_candidates import story_candidates
from immich_memories.analysis.editorial_story_lookalike import (
    hash_pair_relation,
    hash_then_model,
    picture_pair_relation,
)
from immich_memories.analysis.editorial_story_planner import alternatives_pool, select_story_first
from immich_memories.analysis.editorial_story_replies import film_close_family
from immich_memories.analysis.editorial_story_trips import detect_film_trips
from immich_memories.analysis.editorial_structure_audience import (
    AUDIENCE_BANK_NAME,
    AudienceBank,
    AudienceGate,
)
from immich_memories.analysis.editorial_structure_budget import (
    CONTENT_RESERVE_SECONDS,
    MIN_CARRIER_SECONDS,
    NOMINAL_STILL_SECONDS,
)
from immich_memories.analysis.editorial_structure_contract import (
    StructurePlannerPorts,
    StructurePlanningInput,
    StructurePlanningResult,
)
from immich_memories.analysis.editorial_structure_finishing import (
    PlanRun,
    announce_count,
    apply_audience_gate,
    check_empty_attached,
    drop_filler_nothing_vouches_for,
    final_duplicate_review,
    held_by_gate,
    observe_attached,
    replacement_offers,
    resolve_motion_and_timing,
    seat_again_after_review,
    trim_to_timing,
)
from immich_memories.analysis.editorial_structure_material import (
    Material,
    Wall,
    anchor_line,
    build_material,
    hold_the_ends,
    read_wall,
)
from immich_memories.analysis.editorial_structure_record import (
    PlanFacts,
    PlanOutcome,
    build_result,
    contract_texts,
    merged_threads_log,
    provider_metrics,
    shave_content_duration,
)
from immich_memories.analysis.editorial_thin_step import polish_the_draft, shows_life
from immich_memories.analysis.editorial_unvouched_filler import filler_evidence
from immich_memories.analysis.subject_framing import framing_visibility
from immich_memories.processing.editorial_timing import bind_editorial_timeline
from immich_memories.security import write_secret_file

SECONDS_PER_SLOT = NOMINAL_STILL_SECONDS
STORY_RANK = {"central": 0, "supporting": 1}
FLAGGED_LINE = re.compile(r"nsfw=yes|exposure=(partial|nude)")


def _looks_alike_relation(ports, material, episode_relation, relation_records):
    """The repetition question: the preview hashes first, then whatever else this reader has."""
    hashes = hash_pair_relation(ports.thumbnail_hash)
    if ports.rules is not None:
        return hashes
    return hash_then_model(
        hashes,
        picture_pair_relation(
            observe=material.picture_evidence.observe if ports.observe_picture else None,
            episode_relation=episode_relation,
            story_relation=sampled_source_relation(
                ports.confirm_story_pairs, picture_records=relation_records
            ),
        ),
    )


def _near_home_test(source: StructurePlanningInput, wall: Wall):
    home = home_of(source.config.trips)

    def near_home(f):
        pts: list[tuple[float, Any]] = []
        for a in wall.event_assets.get(f, []):
            asset = source.assets.get(a)
            exif = asset.exif_info if asset is not None else None
            if not exif or exif.latitude is None:
                continue
            pts.append((exif.latitude, exif.longitude))
        return near_home_of(home, pts)

    return near_home


@dataclass
class _SubjectPool:
    """A subject memory reads only the happenings the gate read as concerning the subject."""

    units: dict[str, list[dict]]
    moment_assets: dict[str, list[str]]
    rows_fn: Any
    record: dict | None = None


def _subject_pool(marker, gate_tier, wall, material) -> _SubjectPool:
    """The candidate pool comes first; the story is built on it alone. Every other product
    reads the whole period."""
    if not marker:
        return _SubjectPool(material.units, material.moment_assets, factual_moment_rows)
    pool = {f for f, t in gate_tier.items() if t <= 1}
    pool_moments = {m for m, f in wall.family_of_moment.items() if f in pool}

    def pool_rows(tables_, aliases_):
        return [
            row
            for row in factual_moment_rows(tables_, aliases_)
            if row.get("moment_id") in pool_moments
        ]

    return _SubjectPool(
        {f: units for f, units in material.units.items() if f in pool},
        {m: ids for m, ids in material.moment_assets.items() if m in pool_moments},
        pool_rows,
        {
            "criterion_marker": marker,
            "families_in_pool": len(pool),
            "families_total": len(gate_tier),
            "moments_in_pool": len(pool_moments),
        },
    )


def _chapters_of(selection, carriers, anchor_label) -> list[dict]:
    """Chapters are the chosen episodes in the module's own order, so a carrier's
    1-based `chapter` indexes this list exactly as the default path's beats do."""
    episode_of = {e.key: e for e in selection.story.episodes}
    chapter_families: dict[str, list[str]] = {}
    for carrier in carriers:
        known = chapter_families.setdefault(carrier["story_episode"], [])
        if carrier["event"] not in known:
            known.append(carrier["event"])
    return [
        {
            "chapter": f"S{number:02d}",
            "beat": row["title"],
            "anchors": [anchor_label[f] for f in chapter_families.get(row["episode"], [])],
            "share": 0.0,
            "show": (episode_of[row["episode"]].significance or row["title"])
            if row["episode"] in episode_of
            else row["title"],
            "budget": row["granted"],
            "capacity": row["depicted_moments"],
            "families": list(chapter_families.get(row["episode"], [])),
        }
        for number, row in enumerate(selection.episodes, 1)
    ]


def _story_worthiness(selection, wall: Wall, tier: dict, worth_reason: dict) -> None:
    """Worthiness comes from the story's own hierarchy, not a separate ballot."""
    for episode in selection.story.episodes:
        rank = STORY_RANK.get(episode.role, 2)
        for moment_alias in episode.moments:
            f = wall.family_of_moment.get(moment_alias)
            if f is not None and rank < tier.get(f, 3):
                tier[f] = rank
                worth_reason[f] = episode.title
    for f in wall.fam_ids:
        tier.setdefault(f, 2)


def _evidence_partitions(intent, wall: Wall, tier: dict) -> set[str]:
    parts = set()
    for f in wall.fam_ids:
        if tier[f] > 1:
            continue
        day = datetime.fromisoformat(wall.moments[wall.families[f][0]]["taken"]).date()
        part = intent.partition_for(day)
        if part is not None:
            parts.add(part.key)
    return parts


def _partition_cap(
    intent, target_seconds: float, prior, content_budget=None
) -> tuple[int, int, int | None]:
    """Slots, the per-anchor depth cap and the product's partition carrier limit."""
    slots_total = int(
        (target_seconds if content_budget is None else content_budget) // SECONDS_PER_SLOT
    )
    cap = (
        depth_cap(target_seconds)
        if content_budget is None
        else int(content_budget // MIN_CARRIER_SECONDS)
    )
    limit = intent.max_carriers_per_partition
    if limit is not None:
        cap = min(cap, limit)
    if limit is not None and prior:
        prior_counts: dict[str, int] = {}
        for c in prior["carriers"]:
            part = intent.partition_for(datetime.fromisoformat(c["taken"]).date())
            if part is not None:
                prior_counts[part.key] = prior_counts.get(part.key, 0) + 1
        if any(n > limit for n in prior_counts.values()):
            raise ValueError(
                "prior plan exceeds the product's partition carrier limit; replan without the incompatible prior"
            )
    return slots_total, cap, limit


def plan_structure(
    source: StructurePlanningInput, ports: StructurePlannerPorts
) -> StructurePlanningResult:
    """Run the shared editorial algorithm on an already captured production wall."""
    source.bank_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    source.artifact_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    audit_dir = source.artifact_dir / "derived-decisions"
    audit_dir.mkdir(mode=0o700, exist_ok=True)
    # Only the exact text gateway can reuse semantic answers across runs. These parsed
    # dictionaries memoize within this run and are written solely for audit/restart evidence.
    contract, contract_key, admission, admission_key = contract_texts(source.case, source.intent)
    wall = read_wall(source)
    material = build_material(source, ports, wall)
    selection_budget = (
        source.render_timing.selection_budget(source.assets) if source.render_timing else None
    )
    slots_total, cap, partition_limit = _partition_cap(
        source.intent, source.case.target_seconds, source.prior_plan, selection_budget
    )
    prior_assets = (
        {c["asset_id"] for c in source.prior_plan["carriers"]} if source.prior_plan else set()
    )
    run = PlanRun(
        final_content_cap=source.case.target_seconds - CONTENT_RESERVE_SECONDS,
        bind_stitch=material.builder.measured_stitch,
    )
    with llm_metrics.collecting() as counters:
        outcome = _select(
            source,
            ports,
            wall,
            material,
            run,
            audit_dir=audit_dir,
            contract=contract,
            admission=admission,
            admission_key=admission_key,
            partition_limit=partition_limit,
            prior_assets=prior_assets,
        )
    metrics = provider_metrics(counters)
    if run.render_timeline is None:
        run.shaved += shave_content_duration(run.carriers, run.final_content_cap)
    elif sum(c["seconds"] for c in run.carriers) > run.final_content_cap:
        raise ValueError("Certified editorial content grew after its timing was fixed")
    outcome.metrics = metrics
    outcome.shaved = run.shaved
    outcome.content_cap = run.final_content_cap
    outcome.timing_binding = _timing_binding(source, run)
    write_for_cut(source, run.carriers, outcome.share_log.get("verdicts", {}))
    facts = PlanFacts(
        label=source.case.label,
        target_seconds=source.case.target_seconds,
        contract_key=contract_key,
        wall_sha256=hashlib.sha256(source.wall_bytes).hexdigest(),
        slots_total=slots_total,
        cap=cap,
        source_assets=len(source.assets),
        fam_ids=wall.fam_ids,
        anchor_label=wall.anchor_label,
        period_people=wall.period_people,
        merge_log=wall.merge_log,
        document_sources=material.document_sources,
        document_excluded=material.builder.document_excluded,
        ineligible=material.ineligible,
        prior=source.prior_plan,
        prior_assets=prior_assets,
        prior_plan_ref=source.prior_plan_ref,
    )
    return build_result(source, ports, facts, outcome)


def _timing_binding(source: StructurePlanningInput, run: PlanRun) -> dict:
    if run.render_timeline is None:
        return {}
    if source.render_timing is None:
        raise ValueError("Certified timeline lacks its source timing policy")
    return {
        "render_timing": bind_editorial_timeline(
            source.render_timing,
            run.render_timeline,
            [c["asset_id"] for c in run.carriers],
        )
    }


def _select(
    source: StructurePlanningInput,
    ports: StructurePlannerPorts,
    wall: Wall,
    material: Material,
    run: PlanRun,
    *,
    audit_dir,
    contract: str,
    admission: str,
    admission_key: str,
    partition_limit: int | None,
    prior_assets: set[str],
) -> PlanOutcome:
    """The whole selection under one metrics collector: gate, story, audience, duplicates."""

    def record_story(name: str, payload) -> None:
        write_secret_file(
            audit_dir / f"{name}.private.json",
            json.dumps(payload, ensure_ascii=False, indent=1, default=str),
        )

    # A run that only polishes a rules draft still has the captions its tier produces;
    # the reduced check belongs to a run with no model at all.
    audience_tier = (
        "no_captions"
        if ports.rules is not None
        and ports.thin is None
        and source.config.editorial.preparation.demands_models
        else source.config.editorial.preparation.tier
    )
    gate = AudienceGate(
        ports.judge,
        audience=source.audience,
        picture_evidence=material.picture_evidence,
        flag_rows=source.shareability_flags,
        lines=source.annotations,
        bank_path=audit_dir / "shareability.private.json",
        library=AudienceBank(
            source.bank_dir.parent / AUDIENCE_BANK_NAME,
            answerer=f"{audience_tier}|{configured_text_identity(source.config.llm)}",
        ),
        check_audience=audience_check_for(audience_tier),
        chains=chain_holds_for(
            source.assets, source.audience_annotations, source.companion_detectors
        ),
        companion_heads=source.companion_detectors,
    )
    attached_relation_records: dict[str, dict[str, Any]] = {}
    relation_records = ChainMap(attached_relation_records, material.picture_evidence.records)
    tier, worth_reason, marker = _worthiness_gate(
        source,
        ports,
        wall,
        material,
        admission=admission,
        admission_key=admission_key,
        record=record_story,
    )
    pool = _subject_pool(marker, tier, wall, material)
    if pool.record is not None:
        record_story("subject-pool", pool.record)
    # One memo for the repetition question, shared by the story check and the final review.
    episode_relation = sampled_source_relation(
        ports.confirm_episode_pairs, picture_records=relation_records
    )
    # A no-model draft asks nothing, so it reads the model's answers through `banked` alone.
    unit_of = {u["asset_id"]: u for units in material.units.values() for u in units}
    banked = _banked_facts(source, ports)
    if ports.rules is not None:
        record_story("banked-facts", banked.record())
    selection = _story_selection(
        source,
        ports,
        wall,
        material,
        pool,
        gate,
        contract=contract,
        tier=tier,
        marker=marker,
        record=record_story,
        partition_limit=partition_limit,
        banked=banked,
        looks_alike=_looks_alike_relation(ports, material, episode_relation, relation_records),
    )
    run.carriers = list(selection.carriers)
    if ports.thin is not None:
        run.carriers = polish_the_draft(
            source,
            ports,
            material,
            wall,
            selection,
            pool,
            gate,
            run.carriers,
            run,
            contract=contract,
            record=record_story,
        )
    seat = partial(
        seat_in_film,
        film=FilmSeatSource(source, ports.rules, selection, material.units, banked),
        candidates_of=story_candidates(selection, wall, pool, material.units),
        life=lambda asset_id: shows_life(material, unit_of, asset_id),
        excluded=material.document_sources,
    )
    run.carriers = seat(run.carriers, record=record_story)
    required = frozenset(source.owner_required_asset_ids)
    if required:
        # After the read, never before it: the owner's ticks change no prompt.
        run.carriers, owner_record = admit_owner_required(
            run.carriers,
            required=source.owner_required_asset_ids,
            units=material.units,
            stories=selection.story.stories,
            episodes=selection.story.episodes,
            anchor_label=wall.anchor_label,
            line_of=lambda asset_id: material.story_lines.get(asset_id, ""),
        )
        record_story("owner-required", owner_record)
    trim_to_timing(run, source, record_story, protected=required)
    # The film is settled here, so its ends are known. The shave that follows takes the half
    # second back when the target leaves no room for it. An opening and a closing frame are
    # read rather than glanced at whoever cut them, so this is not the no-model reader's.
    hold_the_ends(run.carriers)
    chapters = _chapters_of(selection, run.carriers, wall.anchor_label)
    beats = [row["beat"] for row in chapters]
    _story_worthiness(selection, wall, tier, worth_reason)
    evidence_partitions = _evidence_partitions(source.intent, wall, tier)
    carriers_at_selection = len(run.carriers)
    # The final sampled-duplicate review still runs, so it needs its relation.
    source_relation = sampled_source_relation(
        ports.confirm_sampled_pairs, picture_records=relation_records
    )
    run.carriers.sort(key=itemgetter("taken"))
    run.selection_stages = {
        "funded_picture_requests": 0,
        "after_funded_acquisition": len(run.carriers),
        "before_shareability": len(run.carriers),
    }
    announce_count(len(run.carriers), "going into the family-viewing check")
    share_log = apply_audience_gate(run, gate, selection, material, wall)
    if required - {c["asset_id"] for c in run.carriers}:
        # The safety gate keeps its authority over an owner tick; say so where the owner can read it.
        record_story(
            "owner-required-after-audience",
            {"removed": sorted(required - {c["asset_id"] for c in run.carriers})},
        )
    resolve_motion_and_timing(run, source, ports)
    attached, observed = observe_attached(
        run, ports, gate, material.picture_evidence, attached_relation_records, share_log
    )
    # The review protects the same people the seat counts: in a person film, the subject's own.
    close_of = film_close_family(source)
    final_duplicate_review(
        run,
        ports,
        replacements_for=replacement_offers(
            alternatives_pool(selection, material.units, wall.anchor_label)
        ),
        source_relation=source_relation,
        episode_relation=episode_relation,
        picture_records=material.picture_evidence.records,
        attached=attached,
        prior=source.prior_plan,
        prior_assets=prior_assets,
        quality=material.builder.quality,
        pixel_facts=source.pixel_facts,
        owner_required=source.owner_required_asset_ids,
        close_family_of=lambda asset_id: close_of(selection.lines.get(asset_id, "")),
        gate=gate,
    )
    run.selection_stages["after_final_duplicate_review"] = len(run.carriers)
    announce_count(len(run.carriers), "after the duplicate review")
    if ports.rules is not None and not run.polished:
        # The last removal pass, so no replacement pass can bring a removed filler's like back in.
        drop_filler_nothing_vouches_for(run, filler_evidence(source), record_story)
    # After every pass that removes a shot, so none of them can undo a family seat. It seats a
    # close family member's frame, never filler the pass above removed.
    seat_again_after_review(
        run,
        ports,
        seat=lambda cut: seat(
            cut,
            record=lambda _name, audit: record_story("family-seat-after-review", audit),
            held=held_by_gate(gate, unit_of),
        ),
    )
    check_empty_attached(ports, observed)
    check_finished_cut(source, selection, material, run, gate, banked, share_log, record_story)
    return PlanOutcome(
        contract=contract,
        carriers=run.carriers,
        cut_carriers=run.cut_carriers,
        selection=selection,
        chapters=chapters,
        beats=beats,
        threads=merged_threads_log(beats, selection.story.thesis),
        tier=tier,
        worth_reason=worth_reason,
        share_log=share_log,
        final_duplicates=run.final_duplicates,
        motion_metrics=run.motion_metrics,
        selection_stages=run.selection_stages,
        evidence_partitions=evidence_partitions,
        attached_evidence=attached,
        attached_audience=run.attached_audience,
        picture_facts=material.picture_evidence.records,
        calls=ports.judge.calls,
        ladder_reads=0,
        carriers_at_selection=carriers_at_selection,
    )


def _worthiness_gate(
    source, ports, wall: Wall, material: Material, *, admission, admission_key, record
):
    """Keep scoped admission; ordinary story importance comes from the story reading."""
    if ports.rules is not None:
        tiers, reasons = ports.rules.worthiness(wall, _near_home_test(source, wall))
        record("memory-worthy-gate", {"version": "rules-v1", "tiers": tiers, "reasons": reasons})
        return tiers, reasons, ""
    criterion, marker = worth_criterion_v44(source.case.product, source.intent.subject)
    if not marker:
        record("memory-worthy-gate", {"version": "story-importance-v1", "rounds": []})
        return {}, {}, ""
    bank_path = source.bank_dir / "memory-worthy.private.json"
    bank = load_vote_bank(bank_path)
    gate_tier, gate_reason, gate_rounds = judge_worthiness(
        ports.judge,
        happenings=wall.fam_ids,
        label_of=wall.anchor_label,
        text_of=lambda f: anchor_line(wall, material, f).split(": ", 1)[-1],
        near_home=_near_home_test(source, wall),
        contract=admission,
        contract_key=admission_key,
        criterion=criterion,
        marker=marker,
        period_label=source.case.label,
        bank=bank,
        save=lambda: save_vote_bank(bank_path, bank),
    )
    record(
        "memory-worthy-gate",
        {
            "version": "memory-worthy-v2-contract",
            "counts": {
                "remarkable": sum(1 for t in gate_tier.values() if t == 0),
                "maybe": sum(1 for t in gate_tier.values() if t == 1),
                "background": sum(1 for t in gate_tier.values() if t == 2),
            },
            "tiers": {wall.anchor_label[f]: t for f, t in gate_tier.items()},
            "reasons": {wall.anchor_label[f]: r for f, r in gate_reason.items()},
            "rounds": gate_rounds,
        },
    )
    return gate_tier.copy(), gate_reason.copy(), marker


def _story_selection(
    source,
    ports,
    wall: Wall,
    material: Material,
    pool: _SubjectPool,
    gate: AudienceGate,
    *,
    contract: str,
    tier: dict,
    marker: str,
    record,
    partition_limit: int | None,
    banked: BankedAnswers,
    looks_alike=None,
):
    unit_of = {u["asset_id"]: u for units in material.units.values() for u in units}
    durations = [u["seconds"] for units in pool.units.values() for u in units if u["seconds"] > 0]
    seconds_per_slot = sum(durations) / len(durations) if durations else SECONDS_PER_SLOT
    pool_assets = {a for ids in pool.moment_assets.values() for a in ids if a in source.assets}
    trips = detect_film_trips(
        (source.assets[a] for a in sorted(pool_assets)),
        source.config.trips,
        journey=source.case.product == "trip",
    )
    return select_story_first(
        judge=ports.judge,
        rules=ports.rules,
        tables=wall.tables,
        aliases=wall.aliases,
        factual_rows_fn=pool.rows_fn,
        moment_assets=pool.moment_assets,
        lines=material.story_lines,
        flagged=lambda asset_id: bool(FLAGGED_LINE.search(source.annotations.get(asset_id, ""))),
        subject=lambda asset_id: framing_visibility(source.annotations.get(asset_id, "")),
        # With no model to compare pictures, a capture group's frame is won on capture facts,
        # and on whatever a model already said about them on an earlier run.
        representative_rank=rule_representative_rank(
            source.assets,
            source.annotations,
            source.motion_residuals,
            leads=banked_leaders(banked, tuple(source.episode_readings)),
        )
        if ports.rules is not None
        else None,
        life=lambda asset_id: shows_life(material, unit_of, asset_id),
        full_lines=source.annotations,
        contract=contract + "\n\n" + source.intent.story_prompt_block(),
        event_units=pool.units,
        family_of_moment=wall.family_of_moment,
        anchor_label=wall.anchor_label,
        description_of=material.text.description,
        quality=material.builder.quality,
        motion_line=ports.observe_story_motion,
        episode_readings=source.episode_readings,
        target_seconds=(
            source.render_timing.selection_budget(
                source.assets, expected_clip_duration=seconds_per_slot
            )
            if source.render_timing
            else source.case.target_seconds
        ),
        seconds_per_slot=seconds_per_slot,
        record=record,
        family_tier=tier,
        standing=(ports.rules or RuleStructureReader(source)).standing,
        excluded=material.document_sources,
        allow_story_gaps=bool(marker),  # a subject memory's stages span weeks with gaps
        journey=source.case.product == "trip",
        partition_limit=partition_limit,
        voice_per_partition=source.intent.voice_per_partition,
        partition_of=lambda taken: (
            part.key
            if (part := source.intent.partition_for(datetime.fromisoformat(taken).date()))
            else None
        ),
        trips=trips,
        looks_alike=looks_alike,
        film_span=(source.case.ranges[0].start.date(), source.case.ranges[-1].end.date()),
        near_home=_near_home_test(source, wall),
        banked=banked,
    )


def _banked_facts(source, ports) -> BankedAnswers:
    """What earlier model answers about this library say, for the draft that asks nothing.

    Only the no-model draft reads them. A model run asks its own questions about every
    candidate and banks the replies; handing it the same answers twice would change nothing
    and hide which run paid for what.
    """
    if ports.rules is None:
        return NO_BANKED_FACTS
    return open_banked_facts(
        bank_dir=source.bank_dir,
        attempts_dir=source.artifact_dir.parent,
        store_path=source.store_path,
        audience=source.audience,
        episode_cards=source.episode_readings,
        own_producers=frozenset(
            str(row.get("producer_key", ""))
            for row in (source.lineage.get("episode_readings") or ())
        ),
    )
