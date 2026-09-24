"""Does a picture stand by itself, and may it serve as context inside its story?

One question over one picture at a time, answered from the facts the heads wrote
(`editorial_standing_facts`), on every tier: no model is asked. The answer is what the carrier
admission and the depth pass both spend, so it lives beside neither of them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from immich_memories.analysis.editorial_clip_frames import subject_often_missing
from immich_memories.analysis.editorial_story_pick_contract import carries_motion

WEIGHED_STORY_WEIGHTS = ("dominant", "major", "minor")


class StandingGate:
    """Does a picture stand by itself, and may it serve as context inside its story?"""

    def __init__(
        self,
        score_of: Callable[[str], int],
        *,
        line_of: Callable[[str], str],
        life: Callable[[str], bool],
        unit_by_asset: Mapping[str, Any],
        pictures_of: Mapping[str, int],
    ) -> None:
        self._score_of = score_of
        self._line_of = line_of
        self._life = life
        self._unit_by_asset = unit_by_asset
        self._pictures_of = pictures_of
        self.scores: dict[str, int] = {}
        self.context_rejected: set[tuple[str, str]] = set()

    def ensure(self, assets: Sequence[str], needs: Mapping[str, int] | None = None) -> None:
        """Score every picture not yet scored; with `needs`, skip one no answer can move."""
        self.scores.update(
            {
                a: self._score_of(a)
                for a in dict.fromkeys(assets)
                if a not in self.scores and self._line_of(a) and (needs is None or needs.get(a, 2))
            }
        )

    def needs(self, asset: str, weight: str, story_key: str = "") -> int:
        """Whether this picture's standing can change whether it stands, as `stands` reads it.

        0 when no answer changes it (it has no context to serve, or it is a still with life the
        gate only orders); otherwise 1.
        """
        if asset not in self._unit_by_asset:
            return 1
        if not self._context_allowed(asset, weight, story_key) or self._frames_miss(asset):
            return 0
        moving = carries_motion(self._unit_by_asset[asset][1])
        return 0 if self._ordered_only(asset, weight, story_key) and not moving else 1

    def thin(self, story_key: str) -> bool:
        """A story of one or two pictures has no context for a weak picture to serve."""
        return self._pictures_of.get(story_key, 0) <= 2

    def rejected_motion(self, asset: str) -> bool:
        """Playing motion cannot override missing or unanimously weak standing evidence, nor
        frames that often miss the subject: a clip is judged on what it shows across its
        length, not on the one frame its preview and its row were read from."""
        moving = carries_motion(self._unit_by_asset[asset][1])
        return moving and (self.scores.get(asset, 0) == 0 or self._frames_miss(asset))

    def _frames_miss(self, asset: str) -> bool:
        # Only a real video is refused on its frames, and never a favourite: a favourite
        # showing a wall means something happened there. A Live Photo is its still; its clip's
        # reading decides only whether it plays. The fact stays on the line for the reader.
        unit = self._unit_by_asset[asset][1]
        if unit.get("favourite") or unit.get("kind") != "video":
            return False
        return subject_often_missing(self._line_of(asset))

    def _context_allowed(self, asset: str, weight: str, story_key: str) -> bool:
        starred = bool(self._unit_by_asset[asset][1].get("favourite"))
        return (
            self._life(asset)
            or starred
            or (weight in WEIGHED_STORY_WEIGHTS and self._pictures_of.get(story_key, 0) > 2)
        )

    def has_required_context(self, asset: str, weight: str, story_key: str) -> bool:
        """The existing context requirement is eligibility, not a recoverable weak score."""
        allowed = self._context_allowed(asset, weight, story_key)
        if not allowed:
            self.context_rejected.add((story_key, asset))
        return allowed

    def stands(self, asset: str, weight: str, story_key: str = "") -> bool:
        """A picture is refused on standing only when its score is 0.

        Inside a dominant or major story a still with people or animals in it serves its purpose
        with context and is only ORDERED by the gate, never removed, unless the story is a
        glimpse or holds one or two pictures, which leaves it no context to serve. A moving clip
        needs a score like any other picture, and a picture with no context to serve is refused
        whatever its score."""
        score = self.scores.get(asset, 0)
        if self.rejected_motion(asset):
            return False
        if not self.has_required_context(asset, weight, story_key):
            return False
        if self._ordered_only(asset, weight, story_key):
            return True
        return score >= 1

    def _ordered_only(self, asset: str, weight: str, story_key: str) -> bool:
        """A picture with life in a dominant or major story of more than two pictures: the gate
        orders it and never removes it. A glimpse or a thin story has no context to lend it."""
        return weight in ("dominant", "major") and not self.thin(story_key) and self._life(asset)
