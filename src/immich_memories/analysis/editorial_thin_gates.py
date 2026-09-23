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
    moment: str = ""


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
            _repeat_refusal(shot, keeper_of[shot["asset_id"]])
            for shot in kept
            if shot["asset_id"] in keeper_of
        )
        return survivors, refused

    def admits(
        self,
        candidate: Mapping[str, Any],
        *,
        cut: Sequence[Mapping[str, Any]],
        tier_of: Mapping[str, str],
    ) -> GateRefusal | None:
        """One candidate, judged in the company of the cut it would join, or None when it passes.

        The cut is protected, exactly as the draft pass protects what the film already holds, so
        a newcomer that repeats a shot already in the film is the one that leaves.
        """
        self.standing.ensure([candidate["asset_id"]])
        refusal = self._refusal(candidate, list(cut), tier_of)
        if refusal is not None:
            return refusal
        company: list[dict[str, Any]] = [dict(row) for row in cut]
        survivors, record = review_cut_by_cached_hashes(
            [*company, dict(candidate)],
            thumbnail_hash=self.thumbnail_hash,
            protected_asset_ids=[row["asset_id"] for row in cut],
        )
        if candidate["asset_id"] in {row["asset_id"] for row in survivors}:
            return None
        keeper = next(
            row["keeper"] for row in record["removals"] if row["asset_id"] == candidate["asset_id"]
        )
        return _repeat_refusal(candidate, keeper)

    def settle(self, shots: Sequence[Mapping[str, Any]]) -> None:
        """Put these shots to the standing gate together, so their answers share blocks."""
        self.standing.ensure([shot["asset_id"] for shot in shots])

    def stands_alone(self, shot: Mapping[str, Any], tier_of: Mapping[str, str]) -> bool:
        """The standing gate's answer for this shot as its story's weight reads it."""
        story = str(shot.get("story_episode") or "")
        stands = self.standing.stands(shot["asset_id"], _weight(story, tier_of), story)
        return stands or _owner_and_record(shot)

    def _refusal(self, shot, kept, tier_of) -> GateRefusal | None:
        story = str(shot.get("story_episode") or "")
        moment = str(shot.get("moment") or "")
        if not self.stands_alone(shot, tier_of):
            weight = _weight(story, tier_of)
            return GateRefusal(
                shot["asset_id"], story, "standing", f"as a {weight} story's shot", moment
            )
        verdict = self.audience.verdict_of(shot)
        if not allowed(verdict, self.audience_name):
            return GateRefusal(shot["asset_id"], story, "audience", verdict, moment)
        if not capture_space_available(shot, kept):
            return GateRefusal(
                shot["asset_id"], story, "capture spacing", "inside five minutes", moment
            )
        return None


def _weight(story: str, tier_of: Mapping[str, str]) -> str:
    return WEIGHT_OF_TIER.get(tier_of.get(story, "background"), "glimpse")


def _repeat_refusal(shot: Mapping[str, Any], keeper: str) -> GateRefusal:
    return GateRefusal(
        asset_id=shot["asset_id"],
        story=str(shot.get("story_episode") or ""),
        rule="look-alike",
        detail=f"repeats {keeper[:8]} by cached preview hash",
        moment=str(shot.get("moment") or ""),
    )


def _owner_and_record(shot: Mapping[str, Any]) -> bool:
    """The favourite wins its moment: a picture the owner starred that the catalogue also
    records something about is not refused on a standing answer read off its text alone.
    The audience gate still decides."""
    return bool(shot.get("favourite") and shot.get("notable_record"))
