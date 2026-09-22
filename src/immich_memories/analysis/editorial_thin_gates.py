"""Every shot of a rules draft, put to the gates a model install can actually ask.

The draft was built with no model at all: its standing answers come from the facts on a line and
its look-alike from a cached preview hash. A model install can ask better questions of the same
pictures, and a shot the gates refuse here leaves a slot the polish can refill, which is what a
draft silently short of its target cannot offer.

Nothing is reimplemented. The standing gate, the audience gate, the five-minute capture spacing
and the cached-hash duplicate review are the production ones, asked in that order.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from operator import itemgetter
from typing import Any, Protocol

from immich_memories.analysis.editorial_final_hash_review import review_cut_by_cached_hashes
from immich_memories.analysis.editorial_shareability import allowed
from immich_memories.analysis.editorial_story_shortlist import capture_space_available

# What a worthiness tier means to the standing gate, which asks in weight words.
WEIGHT_OF_TIER = {"remarkable": "major", "maybe": "minor", "background": "glimpse"}


class StandsAlone(Protocol):
    """The production standing gate, as this pass uses it."""

    def ensure(self, assets: Sequence[str]) -> None: ...

    def stands(self, asset: str, weight: str, story_key: str = "") -> bool: ...


class ShowsToTheAudience(Protocol):
    """The production audience gate, as this pass uses it."""

    def verdict_of(self, unit: Mapping[str, Any]) -> str: ...


@dataclass(frozen=True)
class GateRefusal:
    """One shot the gates took out of the draft, and which gate took it."""

    asset_id: str
    story: str
    rule: str
    detail: str


@dataclass(frozen=True)
class ThinGates:
    """The four gates over a draft, in the order that spends the fewest model calls."""

    standing: StandsAlone
    audience: ShowsToTheAudience
    thumbnail_hash: Callable[[str], str | None]
    audience_name: str = "family"

    def admit(
        self,
        carriers: Sequence[dict[str, Any]],
        *,
        tier_of: Mapping[str, str],
        protected: Sequence[str] = (),
    ) -> tuple[list[dict[str, Any]], list[GateRefusal]]:
        """The shots the gates keep, and the ones they refuse with the gate that refused them."""
        shots = sorted(carriers, key=itemgetter("taken", "asset_id"))
        self.standing.ensure([c["asset_id"] for c in shots])
        kept: list[dict[str, Any]] = []
        refused: list[GateRefusal] = []
        for shot in shots:
            refusal = self._refusal(shot, kept, tier_of)
            if refusal is None:
                kept.append(shot)
            else:
                refused.append(refusal)
        survivors, record = review_cut_by_cached_hashes(
            kept, thumbnail_hash=self.thumbnail_hash, protected_asset_ids=protected
        )
        keeper_of = {row["asset_id"]: row["keeper"] for row in record["removals"]}
        refused.extend(
            GateRefusal(
                asset_id=shot["asset_id"],
                story=str(shot.get("story_episode") or ""),
                rule="look-alike",
                detail=f"repeats {keeper_of[shot['asset_id']][:8]} by cached preview hash",
            )
            for shot in kept
            if shot["asset_id"] in keeper_of
        )
        return survivors, refused

    def _refusal(self, shot, kept, tier_of) -> GateRefusal | None:
        story = str(shot.get("story_episode") or "")
        weight = WEIGHT_OF_TIER.get(tier_of.get(story, "background"), "glimpse")
        stands = self.standing.stands(shot["asset_id"], weight, story)
        if not stands and not _owner_and_record(shot):
            return GateRefusal(shot["asset_id"], story, "standing", f"as a {weight} story's shot")
        verdict = self.audience.verdict_of(shot)
        if not allowed(verdict, self.audience_name):
            return GateRefusal(shot["asset_id"], story, "audience", verdict)
        if not capture_space_available(shot, kept):
            return GateRefusal(shot["asset_id"], story, "capture spacing", "inside five minutes")
        return None


def _owner_and_record(shot: Mapping[str, Any]) -> bool:
    """The favourite wins its moment: a picture the owner starred that the catalogue also
    records something about is not refused on a standing answer read off its text alone.
    The audience gate still decides."""
    return bool(shot.get("favourite") and shot.get("notable_record"))
