"""A no-model film goes short rather than keep filler that shows nothing.

The rules draft fills its slots from the material, not from what vouches for each picture, so
in a quiet month it reaches past its indicators. A picture with no indicator of its own (no
star, no video or playing motion, no person Immich knows) is only
there as filler, and when the frame head reads it as carrying nothing (a lone object, an empty
room, a body part, a screen or a document) it leaves the cut. Nothing takes its place: short
beats a guess.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from immich_memories.analysis.editorial_carrier_eligibility import NOTHING_KINDS

# A screen or document is evidence a film can carry when something vouches for it; as filler
# it shows nothing a viewer came for.
SHOWS_NOTHING_AS_FILLER = NOTHING_KINDS | {"screen_or_document"}
MOVING_KINDS = frozenset({"video", "live-motion"})


@dataclass(frozen=True)
class FillerEvidence:
    frame_kind_of: Callable[[str], str | None]
    known_person: Callable[[str], bool]
    protected: frozenset[str] = frozenset()


def owner_vouches_for(carrier: Mapping[str, Any], evidence: FillerEvidence) -> bool:
    """Whether the library itself says this picture matters: a star, a recorded video, a
    person Immich knows, or an owner requirement.

    A Live Photo's motion is not one: the phone records it with every still, so it says
    nothing the photographer chose.
    """
    asset = carrier["asset_id"]
    return (
        bool(carrier.get("favourite"))
        or carrier.get("kind") == "video"
        or asset in evidence.protected
        or evidence.known_person(asset)
    )


def _has_indicator(carrier: dict, evidence: FillerEvidence) -> bool:
    return owner_vouches_for(carrier, evidence) or carrier.get("kind") in MOVING_KINDS


def drop_unvouched_filler(
    carriers: Sequence[dict], evidence: FillerEvidence
) -> tuple[list[dict], list[dict]]:
    """Split a settled cut into the shots it keeps and the filler it drops.

    Only removes: a shot with any indicator, or with no frame reading, is kept as it is.
    """
    kept: list[dict] = []
    dropped: list[dict] = []
    for carrier in carriers:
        empty = evidence.frame_kind_of(carrier["asset_id"]) in SHOWS_NOTHING_AS_FILLER
        if empty and not _has_indicator(carrier, evidence):
            dropped.append(carrier)
        else:
            kept.append(carrier)
    return kept, dropped


def filler_evidence(source) -> FillerEvidence:
    """What vouches for a picture of a planning input, and what its frame head read."""

    def frame_kind_of(asset_id: str) -> str | None:
        record = source.audience_annotations.get(asset_id)
        return dict(record.heads).get("frame_kind") if record is not None else None

    def known_person(asset_id: str) -> bool:
        asset = source.assets.get(asset_id)
        return bool(asset is not None and asset.people)

    return FillerEvidence(
        frame_kind_of=frame_kind_of,
        known_person=known_person,
        protected=frozenset(source.owner_required_asset_ids),
    )
