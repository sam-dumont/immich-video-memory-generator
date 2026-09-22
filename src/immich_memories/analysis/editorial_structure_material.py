"""The captured production wall, read into the happenings and playable units of the period.

Nothing here decides what the film shows. It turns the conserved wall bytes and source assets
into anchors (time-and-place families of moments), the playable units of each anchor — live
bursts, videos and stills, deduplicated — and the anchor row the memory-worthy gate reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from immich_memories.analysis import editorial_shareability as _share
from immich_memories.analysis import editorial_wall_rows as wall_rows
from immich_memories.analysis.editorial_carrier_eligibility import excluded_carrier_sources
from immich_memories.analysis.editorial_episode_documents import (
    anchor_observations,
    episode_candidates_any_order,
    factual_moment_rows,
)
from immich_memories.analysis.editorial_event_families import merge_event_families
from immich_memories.analysis.editorial_person_period_facts import (
    person_period_facts,
    render_person_period_facts,
)
from immich_memories.analysis.editorial_picture_evidence import PictureEvidenceOverlay
from immich_memories.analysis.editorial_speech import banked_unit_regions, speech_buffer
from immich_memories.analysis.editorial_structure_budget import (
    MIN_MOTION_SECONDS,
    MOTION_CAP_SECONDS,
    NOMINAL_STILL_SECONDS,
    RESIDUAL_MIN,
)
from immich_memories.analysis.editorial_structure_contract import (
    StructurePlannerPorts,
    StructurePlanningInput,
)
from immich_memories.analysis.editorial_structure_lines import (
    AnchorRows,
    UnitLines,
    metadata_life,
)
from immich_memories.analysis.motion_rendering import motion_renderings
from immich_memories.api.models import AssetType
from immich_memories.photos.burst_dedup import PhotoCandidate, drop_burst_duplicates

STILL_SECONDS = NOMINAL_STILL_SECONDS


@dataclass
class Wall:
    """The captured production wall, read into the happenings the planner decides over."""

    aliases: tuple[str, ...]
    tables: dict
    moments: dict[str, dict]
    place_names: dict[str, str]
    moment_places: dict[str, list]
    family_of_moment: dict[str, str]
    merge_log: dict
    families: dict[str, list[str]]
    fam_ids: list[str]
    anchor_label: dict[str, str]
    observed: dict[str, str]
    period_people: dict[str, Any]
    person_context: dict[str, str]
    meaning_of: dict[str, str]
    event_assets: dict[str, list[str]]
    moment_of_asset: dict[str, str]


def read_wall(source: StructurePlanningInput) -> Wall:
    wall_bytes = source.wall_bytes
    aliases, _ = wall_rows._read_wall_index(wall_bytes)
    tables = wall_rows._table_rows(wall_bytes.decode("utf-8").splitlines())
    episode_meaning = {r[0]: r[1] for r in tables["episodes"][1]}
    m_fields = tables["moments"][0]
    moments = {r[0]: dict(zip(m_fields, r, strict=True)) for r in tables["moments"][1]}
    moment_places: dict[str, list] = {}
    for m, p in tables["moment_places"][1]:
        moment_places.setdefault(m, []).append(p)
    wall_candidates = episode_candidates_any_order(tables, aliases)
    factual_rows = {row["moment_id"]: row for row in factual_moment_rows(tables, aliases)}
    mapping = {m: c.episode_id for c in wall_candidates for m in c.moment_ids}
    family_of_moment, merge_log = merge_event_families(
        tables, aliases, mapping, assets_of=source.moment_asset_ids, gps=source.gps
    )
    families: dict[str, list[str]] = {}
    for m in aliases:
        families.setdefault(family_of_moment[m], []).append(m)
    fam_ids = list(families)  # chronological by construction
    period_people = {f: person_period_facts(tables, families[f]) for f in fam_ids}
    event_assets: dict[str, list[str]] = {}
    moment_of_asset: dict[str, str] = {}
    for moment_alias, asset_ids in source.moment_asset_ids.items():
        f = family_of_moment.get(moment_alias)
        if f is None:
            continue
        ids = list(asset_ids)
        event_assets.setdefault(f, []).extend(ids)
        for a in ids:
            moment_of_asset[a] = moment_alias
    return Wall(
        aliases=aliases,
        tables=tables,
        moments=moments,
        place_names={r[0]: r[1] for r in tables["places"][1]},
        moment_places=moment_places,
        family_of_moment=family_of_moment,
        merge_log=merge_log,
        families=families,
        fam_ids=fam_ids,
        anchor_label={f: f"F{i + 1:02d}" for i, f in enumerate(fam_ids)},
        observed={f: anchor_observations(families[f], factual_rows) for f in fam_ids},
        period_people=period_people,
        person_context={f: render_person_period_facts(period_people[f]) for f in fam_ids},
        meaning_of={f: _fam_meaning(families[f], mapping, episode_meaning) for f in fam_ids},
        event_assets=event_assets,
        moment_of_asset=moment_of_asset,
    )


def _fam_meaning(moment_aliases, mapping, episode_meaning) -> str:
    eps = list(dict.fromkeys(mapping[m] for m in moment_aliases))
    return " / ".join(dict.fromkeys(episode_meaning[e] for e in eps))


class UnitBuilder:
    """The playable units of one happening: live bursts, videos and stills, deduplicated.

    D26: a never_auto picture (or a burst holding one) is evidence of the happening but never a
    carrier; it leaves before the favourite rule so a flagged star cannot silence its shareable
    siblings.
    """

    def __init__(
        self,
        source: StructurePlanningInput,
        ports: StructurePlannerPorts,
        wall: Wall,
        *,
        renderings: dict,
        never_auto,
        document_sources,
    ) -> None:
        self._assets = source.assets
        self._residuals = source.motion_residuals
        self._speech = source.speech_regions
        self._speech_buffer = speech_buffer(source.config)
        self._pixel_facts = source.pixel_facts
        self._event_assets = wall.event_assets
        self._moment_of_asset = wall.moment_of_asset
        self._renderings = renderings
        self._resolve_motion = ports.resolve_motion
        self._thumbnail_hash = ports.thumbnail_hash
        self._window = source.config.photos.burst_window_seconds
        self._threshold = source.config.photos.burst_hash_threshold
        self._never_auto = never_auto
        self._document_sources = document_sources
        self._keep_moment_alternatives = ports.rules is not None
        self._hashes: dict[str, str | None] = {}
        self.evidence_pictures: dict[str, int] = {}
        self.never_auto_excluded: dict[str, list] = {}
        self.document_excluded: dict[str, list] = {}

    def quality(self, asset_id: str) -> float:
        sharp, bright = self._pixel_facts.get(asset_id, (0.0, 118.0))
        return sharp * max(0.0, 1.0 - abs(bright - 118.0) / 92.0)

    def _thumb_hash(self, asset_id: str):
        if asset_id not in self._hashes:
            try:
                self._hashes[asset_id] = self._thumbnail_hash(asset_id) or None
            except (OSError, ValueError, TypeError):
                self._hashes[asset_id] = None
        return self._hashes[asset_id]

    def _family_residual(self, still_ids):
        vals = [
            self._residuals[s]["residual"]
            for s in still_ids
            if s in self._residuals and "residual" in self._residuals[s]
        ]
        return max(vals) if vals else None

    def _live_unit(self, asset_id: str, base: dict, ids: list[str]) -> dict:
        r = self._renderings[asset_id]
        members = [s for s in r.still_ids if s in ids] or [asset_id]
        residual = self._family_residual(members)
        motion = r.may_play and (
            (residual is not None and residual >= RESIDUAL_MIN)
            or (residual is None and self._resolve_motion is not None)
        )
        stars = [s for s in members if self._assets[s].is_favorite]
        return base | {
            "favourite": bool(stars),
            "kind": "live-motion" if motion else "live-still",
            "motion_candidate": r.may_play,
            "motion_assessed": residual is not None,
            "asset_id": max(stars or members, key=self.quality),
            "members": members,
            "video_ids": list(r.video_ids),
            "trim_points": [list(p) for p in r.trim_points],
            "live_material": r.material.as_dict(),
            "seconds": round(min(r.duration_seconds, MOTION_CAP_SECONDS), 2)
            if motion
            else STILL_SECONDS,
            "raw_seconds": round(r.duration_seconds, 2),
            "residual": residual,
        }

    def _video_unit(self, asset_id: str, base: dict) -> dict | None:
        """None for a clip too short to read as a shot rather than a stub."""
        dur = float(self._assets[asset_id].duration_seconds or 0.0)
        if dur < MIN_MOTION_SECONDS:
            return None
        return base | {
            "kind": "video",
            "asset_id": asset_id,
            "members": [asset_id],
            "video_ids": [asset_id],
            "trim_points": [],
            "seconds": round(min(dur, MOTION_CAP_SECONDS), 2),
            "raw_seconds": round(dur, 2),
            "residual": None,
        }

    def _still_unit(self, asset_id: str, base: dict) -> dict:
        return base | {
            "kind": "still",
            "asset_id": asset_id,
            "members": [asset_id],
            "video_ids": [],
            "trim_points": [],
            "seconds": STILL_SECONDS,
            "raw_seconds": None,
            "residual": None,
        }

    def _raw_units(self, ids: list[str]) -> list[dict]:
        seen_families: set = set()
        units: list[dict] = []
        for a in ids:
            asset = self._assets[a]
            base = {
                "favourite": asset.is_favorite,
                "moment": self._moment_of_asset.get(a),
                "taken": asset.file_created_at.isoformat(),
            }
            # A rendering belongs to a photograph. A video-typed id here falls through to
            # the ordinary video branch, so borrowed rendering state can never make a
            # video pretend to be Live material.
            if a in self._renderings and asset.type is AssetType.IMAGE:
                r = self._renderings[a]
                if r.material is None:
                    raise ValueError("Live rendering lacks canonical source material")
                if r.still_ids in seen_families:
                    continue
                seen_families.add(r.still_ids)
                units.append(self._live_unit(a, base, ids))
                continue
            unit = (
                self._video_unit(a, base)
                if asset.type.value.lower() == "video"
                else self._still_unit(a, base)
            )
            if unit is not None:
                units.append(unit)
        return [self._with_banked_speech(unit) for unit in units]

    def _with_banked_speech(self, unit: dict) -> dict:
        """A unit whose speech a cut has already measured knows where its sentences end."""
        regions = banked_unit_regions(unit, self._speech, buffer=self._speech_buffer)
        return unit if regions is None else unit | {"speech_regions": regions}

    def _distinct(self, units: list[dict]) -> list[dict]:
        # The favourite wins its moment. A reader with no model behind it cannot come back
        # to a moment whose favourite is refused as a carrier, so it keeps the moment's
        # other frames: the capture-group order still puts the favourite in front of them.
        if not self._keep_moment_alternatives:
            starred_moments = {u["moment"] for u in units if u["favourite"]}
            units = [u for u in units if u["moment"] not in starred_moments or u["favourite"]]
        cands = [
            PhotoCandidate(
                key=str(i),
                taken_at=datetime.fromisoformat(u["taken"]),
                thumbnail_hash=None if u["kind"] == "video" else self._thumb_hash(u["asset_id"]),
                score=self.quality(u["asset_id"]),
                is_favorite=bool(u["favourite"]),
            )
            for i, u in enumerate(units)
        ]
        kept = set(
            drop_burst_duplicates(
                cands, window_seconds=self._window, hash_threshold=self._threshold
            )
        )
        # A real duplicate has already been removed. Time alone cannot distinguish
        # an echo from the next action or relationship within the same occasion.
        return [u for i, u in enumerate(units) if str(i) in kept]

    def units_of(self, f: str) -> list[dict]:
        ids = [a for a in self._event_assets.get(f, []) if a in self._assets]
        ids.sort(key=lambda a: self._assets[a].file_created_at)
        units = self._raw_units(ids)
        shareable, excluded = _share.partition_units(units, self._never_auto)
        self.evidence_pictures[f] = len(self._distinct(units))
        if excluded:
            self.never_auto_excluded[f] = [
                {"asset_id": u["asset_id"], "members": u["members"], "kind": u["kind"]}
                for u in excluded
            ]
        scene_units, documents = _share.partition_units(shareable, set(self._document_sources))
        if documents:
            self.document_excluded[f] = [u["asset_id"] for u in documents]
        return self._distinct(scene_units)


@dataclass
class Material:
    """Everything the selection reads about the period's pictures."""

    units: dict[str, list[dict]]
    text: UnitLines
    rows: AnchorRows
    builder: UnitBuilder
    picture_evidence: PictureEvidenceOverlay
    document_sources: dict[str, str]
    ineligible: dict[str, str]
    moment_assets: dict[str, list[str]]
    story_lines: dict[str, str]


def _live_renderings(
    source: StructurePlanningInput, wall: Wall, ports: StructurePlannerPorts
) -> dict:
    renderings: dict = {}
    if not source.allow_live_motion:
        return renderings
    for ids in wall.event_assets.values():
        # The motion manifest must use the same material the event can select
        # and review, rather than borrow nearby footage from source context.
        material = [source.assets[a] for a in dict.fromkeys(ids) if a in source.assets]
        renderings.update(
            motion_renderings(
                material,
                source.config,
                companion_assets=source.companion_assets,
                clock_offsets=ports.clock_offsets,
            )
        )
    return renderings


def build_material(
    source: StructurePlanningInput, ports: StructurePlannerPorts, wall: Wall
) -> Material:
    lines = source.annotations
    picture_evidence = PictureEvidenceOverlay(
        source.audience_annotations, lines, ports.observe_picture
    )
    document_sources = excluded_carrier_sources(lines)
    builder = UnitBuilder(
        source,
        ports,
        wall,
        renderings=_live_renderings(source, wall, ports),
        never_auto=_share.never_auto_ids(source.shareability_flags),
        document_sources=document_sources,
    )
    units = {f: builder.units_of(f) for f in wall.fam_ids}
    text = UnitLines(
        lines,
        # The no-model reader has no sentence to read, so it is handed the people facts
        # the line strips. A model reader keeps judging the caption it was given.
        life_without_prose=metadata_life(source.assets, source.audience_annotations)
        if ports.rules is not None
        else None,
    )
    # The story reader reads what the pictures show, not the flag/people tail of the line.
    story_lines = dict(lines)
    for family_units in units.values():
        for u in family_units:
            story_lines[u["asset_id"]] = text.description(u) or lines.get(u["asset_id"], "")
    moment_assets: dict[str, list[str]] = {}
    for asset_id, moment_alias in wall.moment_of_asset.items():
        if asset_id in source.assets:
            moment_assets.setdefault(moment_alias, []).append(asset_id)
    return Material(
        units=units,
        text=text,
        rows=AnchorRows(
            anchor_label=wall.anchor_label,
            families=wall.families,
            moments=wall.moments,
            place_names=wall.place_names,
            moment_places=wall.moment_places,
            event_assets=wall.event_assets,
            assets=source.assets,
            observed=wall.observed,
            person_context=wall.person_context,
            meaning_of=wall.meaning_of,
        ),
        builder=builder,
        picture_evidence=picture_evidence,
        document_sources=document_sources,
        ineligible=_ineligible_anchors(wall.fam_ids, units, builder),
        moment_assets=moment_assets,
        story_lines=story_lines,
    )


def _ineligible_anchors(fam_ids, units, builder: UnitBuilder) -> dict[str, str]:
    """An anchor with no distinct shareable scene picture cannot be selected at all."""
    return {
        f: (
            "no shareable picture: every distinct picture carries never_auto"
            if builder.never_auto_excluded.get(f)
            else "no scene picture: screen or document evidence only"
            if builder.document_excluded.get(f)
            else "no distinct picture"
        )
        for f in fam_ids
        if not units[f]
    }


def anchor_line(wall: Wall, material: Material, f: str) -> str:
    units = material.units[f]
    return material.rows.line(f, units, material.builder.evidence_pictures.get(f, len(units)))
