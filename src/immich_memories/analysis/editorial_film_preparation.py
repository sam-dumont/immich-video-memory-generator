"""Acquire the NAS facts first, then enrich the selected film without expanding its scope."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from immich_memories.analysis.editorial_clip_frames import load_clip_frames
from immich_memories.analysis.editorial_preparation_motion import read_motion_residuals
from immich_memories.analysis.editorial_runtime_evidence import (
    EditorialInputsRequired,
    EvidencePreparation,
)
from immich_memories.analysis.editorial_shareability import load_detector_heads, load_flags
from immich_memories.analysis.editorial_structure_contract import StructurePlanningInput
from immich_memories.config_tiers import nas_draft_config


class FilmPreparation:
    """One film's acquisition scopes; bulk preparation keeps its independent entry point."""

    def __init__(self, evidence: EvidencePreparation) -> None:
        self._evidence = evidence
        self._prepared: Any = None
        self._on_stage = None
        self._round = 0

    def __call__(self, prepared, on_stage, reach):
        self._prepared, self._on_stage = prepared, on_stage
        self._round = 0
        readings = self._evidence.readings
        nas = replace(readings, config=nas_draft_config(readings.config))
        return replace(self._evidence, readings=nas, inspect_clips=False)(prepared, on_stage, reach)

    def refine(self, source: StructurePlanningInput, carriers) -> StructurePlanningInput:
        """Read only selected material and publish its fresh facts to refinement's readers."""
        if not carriers:
            return source
        ids = frozenset(
            asset_id
            for carrier in carriers
            for asset_id in (carrier["asset_id"], *carrier.get("members", ()))
            if asset_id in source.assets
        )
        self._round += 1
        evidence = replace(self._evidence, report_name=f"refinement/{self._round:04}/preparation")
        evidence(self._prepared, self._on_stage, ids)
        readings = evidence.readings
        batch = readings.reader(self._prepared).lines_for(tuple(sorted(ids)))
        if batch.missing_asset_ids:
            raise EditorialInputsRequired(readings.store_path, detail="unreadable refinement facts")
        store = readings.store_path
        assets = [source.assets[asset_id] for asset_id in ids]
        companions = {
            str(asset.live_photo_video_id) for asset in assets if asset.live_photo_video_id
        }
        return replace(
            source,
            annotations={**source.annotations, **batch.as_mapping()},
            audience_annotations={**source.audience_annotations, **batch.records_by_id()},
            shareability_flags=dict(source.shareability_flags)
            | load_flags(store, ids | companions),
            companion_detectors=dict(source.companion_detectors)
            | load_detector_heads(store, companions, source.config.editorial.head_versions),
            clip_frames=dict(source.clip_frames) | load_clip_frames(store, companions),
            motion_residuals=dict(source.motion_residuals) | read_motion_residuals(store, assets),
        )


class CandidateEvidence:
    """Own the live fact views shared by one refinement's readers and candidate gates."""

    def __init__(
        self,
        source: StructurePlanningInput,
        prepare: Callable[
            [StructurePlanningInput, Sequence[Mapping[str, Any]]], StructurePlanningInput
        ],
    ) -> None:
        self._prepare = prepare
        self._seen: set[str] = set()
        self._facts = {
            name: dict(getattr(source, name))
            for name in (
                "annotations",
                "audience_annotations",
                "shareability_flags",
                "companion_detectors",
                "clip_frames",
                "motion_residuals",
            )
        }
        self.source = replace(
            source,
            annotations=self._facts["annotations"],
            audience_annotations=self._facts["audience_annotations"],
            shareability_flags=self._facts["shareability_flags"],
            companion_detectors=self._facts["companion_detectors"],
            clip_frames=self._facts["clip_frames"],
            motion_residuals=self._facts["motion_residuals"],
        )

    def ensure(self, carriers: Sequence[Mapping[str, Any]]) -> bool:
        """Acquire a candidate once; existing reader views see its facts before admission."""
        pending = [
            carrier
            for carrier in carriers
            if {carrier["asset_id"], *carrier.get("members", ())} - self._seen
        ]
        if not pending:
            return False
        refreshed = self._prepare(self.source, pending)
        for name, facts in self._facts.items():
            facts.clear()
            facts.update(getattr(refreshed, name))
        self._seen.update(
            asset_id
            for carrier in pending
            for asset_id in (carrier["asset_id"], *carrier.get("members", ()))
        )
        return True
