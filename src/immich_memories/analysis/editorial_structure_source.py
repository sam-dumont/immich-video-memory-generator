"""Adapt the conserved product workprint into structure-planning evidence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.analysis.editorial_attached_outcomes import AttachedOutcomeReplay
from immich_memories.analysis.editorial_case import Case, _adapt_production_cards
from immich_memories.analysis.editorial_intent import build_editorial_intent
from immich_memories.analysis.editorial_moment_wall import ProductionMomentWallRenderer
from immich_memories.analysis.editorial_motion_outcomes import MotionOutcomeReplay
from immich_memories.analysis.editorial_preparation_motion import read_motion_residuals
from immich_memories.analysis.editorial_shareability import load_detector_heads, load_flags
from immich_memories.analysis.editorial_structure_contract import (
    EpisodeReadingCard,
    StructurePlanningInput,
)
from immich_memories.api.models import Asset, VideoClipInfo
from immich_memories.speech.facts import read_speech_regions, speech_producer

if TYPE_CHECKING:
    from immich_memories.analysis.editorial_orchestration import TextEditorialWorkprint
    from immich_memories.analysis.editorial_people import EditorialPeople
    from immich_memories.analysis.moment_cards import MomentCard
    from immich_memories.analysis.text_episode_reader import TextEpisodeReadResult
    from immich_memories.config_loader import Config


def read_pixel_facts(store_path: Path, producer: str) -> dict[str, tuple[float, float]]:
    with sqlite3.connect(f"file:{store_path}?mode=ro", uri=True) as connection:
        return {
            row[0]: (float(row[1] or 0.0), float(row[2] or 0.0))
            for row in connection.execute(
                "select asset_id, sharpness, brightness from pixel_facts where producer_key=?",
                (producer,),
            )
        }


def capture_companion_assets(
    primaries: Mapping[str, Asset], sources: Sequence[Asset | VideoClipInfo]
) -> dict[str, Asset]:
    """Retain real attached-video metadata without making it selectable primary material."""
    linked = {asset.live_photo_video_id for asset in primaries.values() if asset.is_live_photo}
    companions: dict[str, Asset] = {}
    for source in sources:
        asset = source.asset if isinstance(source, VideoClipInfo) else source
        if asset.id not in linked:
            continue
        if not asset.is_video:
            raise ValueError("declared companion metadata is not a video")
        if asset.id in companions and companions[asset.id] != asset:
            raise ValueError("captured companion metadata disagrees for one source ID")
        companions[asset.id] = asset
    return companions


def episode_reading_cards(
    episodes: TextEpisodeReadResult,
    cards: Sequence[MomentCard],
    aliases: Sequence[str],
) -> dict[str, EpisodeReadingCard]:
    """Carry each moment's banked episode meaning and representatives under its wall alias.

    An episode the reader could not read keeps the card's own representatives and an empty
    meaning; the story read builds its factual line locally rather than losing the moment.
    """
    read = {evidence.projection.group.group_id: evidence for evidence in episodes.episodes}
    carried: dict[str, EpisodeReadingCard] = {}
    for alias, card in zip(aliases, cards, strict=True):
        evidence = read.get(card.episode_id)
        reading = evidence.reading if evidence is not None else None
        carried[alias] = EpisodeReadingCard(
            episode_id=card.episode_id,
            evidence_key=reading.identity.evidence_key if reading is not None else "",
            what_happened=reading.what_happened if reading is not None else "",
            representative_asset_ids=(
                tuple(row.asset_id for row in reading.representatives)
                if reading is not None
                else card.representative_asset_ids
            ),
            cache_hit=bool(evidence is not None and evidence.cache_hit),
        )
    return carried


def capture_structure_input(
    workprint: TextEditorialWorkprint,
    *,
    case: Case,
    config: Config,
    people: EditorialPeople,
    store_path: Path,
    artifact_dir: Path,
    motion_outcome_replay: MotionOutcomeReplay | None = None,
    attached_sources: Sequence[Asset | VideoClipInfo] = (),
    attached_outcome_replay: AttachedOutcomeReplay | None = None,
) -> StructurePlanningInput:
    cards, _selectable = _adapt_production_cards(workprint.prepared, workprint.cards)
    renderer = ProductionMomentWallRenderer(workprint.prepared, workprint.cards, people)
    wall = renderer.render(cards)
    assets = {candidate.asset_id: candidate.source for candidate in workprint.prepared.candidates}
    companions = capture_companion_assets(assets, attached_sources)
    gps = {
        key: (asset.exif_info.latitude, asset.exif_info.longitude)
        for key, asset in assets.items()
        if asset.exif_info
        and asset.exif_info.latitude is not None
        and asset.exif_info.longitude is not None
    }
    eligible_hash = hashlib.sha256(
        json.dumps(
            workprint.prepared.candidate_ids, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()
    return StructurePlanningInput(
        case=case,
        intent=build_editorial_intent(
            case.product,
            case.ranges,
            brief=case.brief,
            people=case.people,
            event_admission=case.event_admission,
            material={
                assets[asset_id].file_created_at.date()
                for card in workprint.cards
                for asset_id in card.selectable_asset_ids
                if asset_id in assets
            },
        ),
        config=config,
        wall_bytes=wall.text.encode(),
        moment_asset_ids={
            alias: card.selectable_asset_ids
            for alias, card in zip(wall.aliases, workprint.cards, strict=True)
        },
        assets=assets,
        companion_assets=companions,
        annotations=workprint.episodes.annotation_batch.as_mapping(),
        audience_annotations={
            line.asset_id: line for line in workprint.episodes.annotation_batch.lines
        },
        gps=gps,
        pixel_facts=read_pixel_facts(store_path, config.editorial.pixel_producer_key),
        shareability_flags=load_flags(store_path, {*assets, *companions}),
        companion_detectors=load_detector_heads(
            store_path, companions, config.editorial.head_versions
        ),
        motion_residuals=read_motion_residuals(store_path, assets.values()),
        speech_regions=read_speech_regions(
            store_path,
            [*assets.values(), *companions.values()],
            speech_producer(config.speech),
        ),
        store_path=store_path,
        episode_readings=episode_reading_cards(workprint.episodes, workprint.cards, wall.aliases),
        lineage={
            "episode_readings": [
                {
                    "group_id": episode.identity.group_id,
                    "producer_key": episode.identity.producer_key,
                    "evidence_key": episode.identity.evidence_key,
                }
                for episode in workprint.episodes.episodes
                if episode.identity is not None
            ],
            "eligible_ids_sha256": eligible_hash,
            "sources": "conserved production workprint, no refetch",
        },
        bank_dir=store_path.parent / "structure-banks" / case.key,
        artifact_dir=artifact_dir,
        motion_outcome_replay=motion_outcome_replay,
        attached_outcome_replay=attached_outcome_replay,
        owner_required_asset_ids=tuple(
            asset_id
            for asset_id in workprint.prepared.owner_required_asset_ids
            if asset_id in assets
        ),
    )
