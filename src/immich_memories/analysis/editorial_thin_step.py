"""The planner's thin step: the model's one read of the rules draft, or the reason it did not run.

A draft the polish did not touch is the no-model film, so the step tells the run whether it
polished: the passes only a no-model film takes (the unvouched-filler drop) run on that draft
exactly as they would with no model configured, instead of being skipped for a polish that
never happened.
"""

from __future__ import annotations

from immich_memories.analysis.editorial_audience_batch import AUDIENCE_BATCH_SIZE
from immich_memories.analysis.editorial_story_candidates import story_candidates
from immich_memories.analysis.editorial_story_replies import film_close_family
from immich_memories.analysis.editorial_story_standing import StandingGate
from immich_memories.analysis.editorial_structure_material import Material, Wall
from immich_memories.analysis.editorial_thin_gates import ThinGates
from immich_memories.analysis.editorial_thin_layer import PeriodUnread


def shows_life(material: Material, unit_of, asset_id: str) -> bool:
    """Whether this picture's unit reads as people or life, not a lone object."""
    u = unit_of.get(asset_id)
    return bool(u) and material.text.shows_life(u) and not material.text.lone_object(u)


def polish_the_draft(
    source,
    ports,
    material: Material,
    wall: Wall,
    selection,
    pool,
    gate,
    carriers,
    run,
    *,
    contract,
    record,
):
    """The model's one read of the rules cut this run built; sets `run.polished`.

    The draft was built blind, from rules. Standing here is the same answer from the heads the
    draft used; the model is asked only what the heads cannot answer.
    """
    if ports.thin is None:
        return carriers
    unit_by_asset = {u["asset_id"]: (f, u) for f, units in material.units.items() for u in units}
    unit_of = {asset: unit for asset, (_family, unit) in unit_by_asset.items()}
    standing = StandingGate(
        ports.rules.standing,
        line_of=lambda asset_id: selection.lines.get(asset_id, ""),
        life=lambda asset_id: shows_life(material, unit_of, asset_id),
        unit_by_asset=unit_by_asset,
        pictures_of={s["key"]: s["seen"]["pictures"] for s in selection.story.stories},
    )
    try:
        catalogue = ports.thin.catalogue_of(selection.story, pool.moment_assets, drafted=carriers)
        unread = ""
    except PeriodUnread as exc:
        catalogue, unread = None, str(exc)
    run.polished = catalogue is not None and bool(carriers)
    return ports.thin.polish(
        carriers,
        judge=ports.judge,
        gates=ThinGates(
            standing=standing,
            audience=gate,
            thumbnail_hash=ports.thumbnail_hash,
            audience_name=source.audience,
            audience_batch=AUDIENCE_BATCH_SIZE
            if source.config.editorial.thin_batched_audience
            else 0,
        ),
        catalogue=catalogue,
        unread=unread,
        contract=contract,
        line_of=lambda asset_id: selection.lines.get(asset_id, ""),
        record=record,
        candidates_of=story_candidates(selection, wall, pool, material.units),
        content_cap=run.final_content_cap,
        protected=source.owner_required_asset_ids,
        subject=source.intent.subject or "",
        close_family=film_close_family(source),
    )
