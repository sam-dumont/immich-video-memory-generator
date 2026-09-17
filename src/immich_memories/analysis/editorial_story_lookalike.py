"""A story's second or third picture is kept only if it does not look like one already kept.

The question is the final duplicate review's visual repetition question, asked while the cut can
still be refilled: two pictures of the same capture family within the 90-minute window are asked
exactly as the final review asks them (so the answer is shared), and any other pair of one story
is asked the story-scoped version, whose premise does not claim the two were close in time.

A refused picture frees its slot for the story's next distinct moment, or for the next story in
funding order. When nothing else can take the slot, the refused picture comes back: the check
alone never makes a film short.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from immich_memories.analysis.editorial_final_sampled_duplicates import nearby_episode

Relation = Callable[[str, str, int | None], Mapping[str, Any]]
PairLooksAlike = Callable[[Mapping[str, Any], Mapping[str, Any]], bool | None]


def picture_pair_relation(
    *,
    observe: Callable[[str], None] | None,
    episode_relation: Relation | None,
    story_relation: Relation | None,
) -> PairLooksAlike | None:
    """Whether two carriers look alike, from their own observed pictures; None without the ports."""
    if observe is None or episode_relation is None or story_relation is None:
        return None

    def looks_alike(candidate: Mapping[str, Any], keeper: Mapping[str, Any]) -> bool | None:
        observe(candidate["asset_id"])
        observe(keeper["asset_id"])
        relation = episode_relation if nearby_episode(candidate, keeper) else story_relation
        return relation(candidate["asset_id"], keeper["asset_id"], None).get("same")

    return looks_alike


class LookAlikeCheck:
    """The refusals of one selection, inside a fixed bound of pair questions (twice the slots)."""

    def __init__(self, looks_alike: PairLooksAlike | None, *, slots: int) -> None:
        self._looks_alike = looks_alike
        self.limit = 2 * slots
        self.checks = 0
        self._answers: dict[tuple[str, str], bool | None] = {}
        self.refused: list[dict[str, Any]] = []
        self.readmitted: list[dict[str, Any]] = []
        self.unchecked = 0

    def repeats(
        self, candidate: Mapping[str, Any], kept: Sequence[Mapping[str, Any]]
    ) -> str | None:
        """The kept carrier this candidate repeats, if any. A favourite is never refused for
        looking like a picture the owner did not star."""
        if self._looks_alike is None:
            return None
        for keeper in kept:
            if candidate.get("favourite") and not keeper.get("favourite"):
                continue
            pair = (candidate["asset_id"], keeper["asset_id"])
            if pair not in self._answers:
                if self.checks >= self.limit:
                    self.unchecked += 1
                    return None
                self.checks += 1
                self._answers[pair] = self._looks_alike(candidate, keeper)
            if self._answers[pair] is True:
                return keeper["asset_id"]
        return None

    def refuse(self, story: str, asset: str, repeats: str, readmit: Callable[[], bool]) -> None:
        self.refused.append(
            {"story": story, "asset_id": asset, "repeats": repeats, "_readmit": readmit}
        )

    def readmit(self, room: Callable[[], bool]) -> None:
        """Fill what the check alone left empty, earliest refusal first."""
        for row in self.refused:
            if not room():
                return
            if row["_readmit"]():
                self.readmitted.append({k: v for k, v in row.items() if k != "_readmit"})

    def record(self) -> dict[str, Any]:
        return {
            "status": "unavailable" if self._looks_alike is None else "asked",
            "limit": self.limit,
            "checks": self.checks,
            "unchecked_for_the_bound": self.unchecked,
            "refused": [{k: v for k, v in row.items() if k != "_readmit"} for row in self.refused],
            "readmitted": self.readmitted,
        }
