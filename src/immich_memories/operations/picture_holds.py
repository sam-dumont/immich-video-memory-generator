"""A picture's hold and the owner's decision on it, as the pool, the storyboard and the CLI say it.

A hold is what keeps a picture from a film shared beyond the household or out of every film:
a detector's flag on the picture or its Live clip, and whatever an earlier cut banked in the
library's audience bank. The owner answers per picture: clear the hold, or never use it
(`store/owner_decisions.py`). Nothing here clears anything by itself.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from immich_memories.analysis.editorial_shareability import (
    NEVER_AUTO,
    OWNER_SOURCE,
    load_detector_heads,
    load_flags,
)
from immich_memories.analysis.editorial_shareability_audience import exposure_flagged
from immich_memories.analysis.editorial_structure_audience import (
    CARRIER_RULE_SOURCE,
    AudienceBank,
    library_bank_path,
)
from immich_memories.store import owner_decisions
from immich_memories.store.owner_decisions import CLEAR_HOLD, NEVER_USE

_DETECTOR = "a nudity detector flagged it"
_CLIP = "a nudity detector flagged its motion clip"
_FINDING_WORDS = {
    "exposure_evidence": _DETECTOR,
    "clip_exposure": _CLIP,
    "exposure_chain": "most of the pictures taken around it are flagged for nudity",
    "private_activity": "its caption names a private moment",
    "nudity_shirtless_or_underwear": "an older reading saw someone uncovered",
}


@dataclass(frozen=True)
class PictureHold:
    """One picture: what holds it, and what the owner decided."""

    asset_id: str
    decision: str | None
    reasons: tuple[str, ...]
    # A detector flagged the picture or its clip: the hold only the owner may clear.
    detector: bool

    @property
    def can_clear(self) -> bool:
        """A hold stands and the owner hasn't answered it yet (Undo comes first after one)."""
        return bool(self.reasons) and self.decision is None

    def describe(self) -> str:
        """One line for under the picture; empty when there is nothing to say."""
        held = "; ".join(self.reasons)
        if self.decision == NEVER_USE:
            return "You'll never use this picture."
        if self.decision == CLEAR_HOLD:
            return f"You cleared its hold ({held})." if held else "You cleared its hold."
        return f"Held: {held}." if held else ""


def store_of(config: Any) -> Path:
    return config.editorial.resolve_annotation_database(config.cache.cache_path)


def audience_bank_of(config: Any) -> Path:
    return library_bank_path(store_of(config))


def read(
    config: Any, asset_ids: Iterable[str], *, clips: Mapping[str, str | None] | None = None
) -> dict[str, PictureHold]:
    """Every picture's hold and decision, from the library's banks as they stand now.

    `clips` maps a Live Photo's still to its motion clip, whose own flags hold the picture too;
    the store's own record of a Live Photo is read either way.
    """
    ids = list(dict.fromkeys(asset_ids))
    store = store_of(config)
    clips = owner_decisions.live_clips(store, ids) | {a: c for a, c in (clips or {}).items() if c}
    decided = owner_decisions.decisions(store, ids)
    heads = (
        load_detector_heads(store, [*ids, *clips.values()], config.editorial.head_versions)
        if store.is_file()
        else {}
    )
    flags = _producer_never_auto(store, ids)
    bank = AudienceBank(audience_bank_of(config), answerer="")
    out = {}
    for asset_id in ids:
        reasons, detector = _reasons(asset_id, clips.get(asset_id), heads, bank)
        if asset_id in flags:
            reasons.append(f"marked never to be used by {flags[asset_id]}")
        out[asset_id] = PictureHold(asset_id, decided.get(asset_id), tuple(reasons), detector)
    return out


def _reasons(
    asset_id: str,
    clip_id: str | None,
    heads: Mapping[str, Mapping[str, str]],
    bank: AudienceBank,
) -> tuple[list[str], bool]:
    reasons = []
    if exposure_flagged(heads.get(asset_id, {})):
        reasons.append(_DETECTOR)
    if clip_id and exposure_flagged(heads.get(clip_id, {})):
        reasons.append(_CLIP)
    detector = bool(reasons)
    banked = bank.held(asset_id)
    # A carrier rule (a screen, a document) says what a picture is, not who may see it.
    if banked is not None and banked.get("policy") != CARRIER_RULE_SOURCE:
        finding = str(banked.get("finding") or "")
        words = _FINDING_WORDS.get(finding, f"the family-viewing check held it ({finding})")
        detector = detector or words in (_DETECTOR, _CLIP)
        if words not in reasons:
            reasons.append(words)
    return reasons, detector


def _producer_never_auto(store: Path, ids: list[str]) -> dict[str, str]:
    if not store.is_file():
        return {}
    return {
        asset_id: row.source
        for asset_id, rows in load_flags(store, ids).items()
        for row in rows
        if row.flag == NEVER_AUTO and row.source != OWNER_SOURCE
    }


def clear_hold(config: Any, asset_id: str, *, via: str, clip_id: str | None = None) -> None:
    """The owner cleared this one picture's hold: every film may use it."""
    owner_decisions.decide(store_of(config), asset_id, CLEAR_HOLD, via=via, clip_id=clip_id)


def never_use(config: Any, asset_id: str, *, via: str, clip_id: str | None = None) -> None:
    """The owner never wants this picture in a film."""
    owner_decisions.decide(store_of(config), asset_id, NEVER_USE, via=via, clip_id=clip_id)


def forget(config: Any, asset_id: str, *, clip_id: str | None = None) -> None:
    """Drop the owner's decision: the app's own holds apply again."""
    owner_decisions.forget(store_of(config), asset_id, clip_id=clip_id)
