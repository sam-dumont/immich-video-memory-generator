"""Demand final Live samples without widening selection or granting audience clearance."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import chain
from typing import Any

from immich_memories.analysis import editorial_shareability as share
from immich_memories.analysis.editorial_attached_samples import AttachedVideoSamples
from immich_memories.analysis.editorial_picture_facts import PictureFactsProvider
from immich_memories.analysis.editorial_sampled_pair_confirmation import CachedSampledPairConfirmer
from immich_memories.processing.live_material import LiveRenderMaterial


@dataclass(frozen=True)
class AttachedMaterialEvidence:
    displayed_members: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    observed_members: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    records: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    gaps: Mapping[str, tuple[Mapping[str, Any], ...]] = field(default_factory=dict)


class FinalAttachedPictures:
    def __init__(
        self,
        samples: AttachedVideoSamples,
        pictures: PictureFactsProvider,
        pairs: CachedSampledPairConfirmer,
    ) -> None:
        self._samples, self._pictures, self._pairs = samples, pictures, pairs

    def __call__(self, carriers: Sequence[Mapping[str, Any]]) -> AttachedMaterialEvidence:
        displayed: dict[str, tuple[str, ...]] = {}
        observed: dict[str, tuple[str, ...]] = {}
        records: dict[str, Mapping[str, Any]] = {}
        gaps: dict[str, tuple[Mapping[str, Any], ...]] = {}
        material_scope, demands = self._demanded_material(carriers)
        # Bind all displayed material before the first download, decode or model request.
        self._samples.begin_material(material_scope, list(chain.from_iterable(demands.values())))
        for key, requests in demands.items():
            members, missing = self._observed_members(requests, records)
            observed[key] = tuple(members)
            # A missing interval never becomes a vacuous proof over a smaller film.
            displayed[key] = () if missing else tuple(members)
            if missing:
                gaps[key] = tuple(missing)
        self._samples.finish_material()
        return AttachedMaterialEvidence(displayed, observed, records, gaps)

    @staticmethod
    def _demanded_material(
        carriers: Sequence[Mapping[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        material_scope: list[dict[str, Any]] = []
        demands: dict[str, list[dict[str, Any]]] = {}
        for unit in carriers:
            if unit.get("kind") != "live-motion":
                continue
            key = unit["asset_id"]
            if key in demands:
                raise ValueError("final attached material repeats a primary identity")
            material = LiveRenderMaterial.from_dict(unit["live_material"])
            if (
                tuple(unit["members"]) != material.still_ids
                or tuple(unit["video_ids"]) != material.video_ids
                or tuple(tuple(pair) for pair in unit["trim_points"]) != material.trim_points
            ):
                raise ValueError("final Live carrier differs from its canonical material")
            start, end = material.selected_interval(
                unit["seconds"],
                start=unit.get("start_time", 0.0),
                end=unit.get("end_time"),
                raw_seconds=unit.get("raw_seconds"),
            )
            material_scope.append(
                {
                    "primary": key,
                    "canonical": material.as_dict(),
                    "start": start,
                    "end": end,
                }
            )
            demands[key] = [
                {
                    "video_id": segment.video_id,
                    "parent_ids": tuple(
                        row.still_id
                        for row in material.source_entries
                        if row.video_id == segment.video_id
                    ),
                    "start": segment.start,
                    "end": segment.end,
                }
                for segment in material.displayed_interval(start, end)
            ]
        return material_scope, demands

    def _observed_members(
        self, requests: list[dict[str, Any]], records: dict[str, Mapping[str, Any]]
    ) -> tuple[list[str], list[Mapping[str, Any]]]:
        members: list[str] = []
        missing: list[Mapping[str, Any]] = []
        for request in requests:
            acquired = self._samples.acquire(**request)
            if acquired is None:
                missing.append(
                    {
                        "source_id": request["video_id"],
                        "start": request["start"],
                        "end": request["end"],
                        "reason": "attached_sample_unavailable",
                    }
                )
                continue
            sample, frame, source = acquired
            record = self._pictures.observe_sample(sample, frame)
            self._pairs.bind_sample(sample, source)
            if sample.key in records and records[sample.key] != record:
                raise ValueError("one exact attached sample produced conflicting observations")
            records[sample.key] = record
            members.append(sample.key)
        return members, missing


def sample_audience_evidence(record: Mapping[str, Any]) -> dict[str, Any]:
    """Use the existing audience classifier on one frame; this cannot clear prior holds."""
    description = record.get("description") if record.get("status") == "available" else None
    return {
        "version": share.AUDIENCE_CHECK_POLICY_VERSION,
        "members": [
            {
                "member": "p1",
                "caption": description or "",
                "detectors": {},
                "flags": [],
                "body_observation": share._visual_body_observation(record),
            }
        ],
        "companion_detectors": [],
        "companion_flags": [],
    }
