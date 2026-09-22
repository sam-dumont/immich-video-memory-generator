"""A story's second or third picture is kept only if it adds to the ones already kept.

The question is the final duplicate review's visual repetition question, asked while the cut can
still be refilled: two pictures of the same capture family within the 90-minute window are asked
exactly as the final review asks them (so the answer is shared), and any other pair of one story
is asked the story-scoped version, whose premise does not claim the two were close in time.

A picture is compared with the frames its story already holds around it: those of its own moment,
and the kept frame just before and just after it in capture time. That is where a repetition
lives, and it keeps the fixed bound (twice the film's slots) for the pairs worth asking about
rather than spending it on every pair of a long story.

A picture can also be refused for where it was taken rather than for how it looks, when its place
already holds its share of the film (`editorial_story_places`); both refusals share this ledger.

A refused picture frees its slot for the story's next distinct moment, or for the next story in
funding order. When nothing else can take the slot, the refused picture comes back: the check
alone never makes a film short.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from immich_memories.analysis.duplicate_hashing import hamming_distance
from immich_memories.analysis.editorial_final_sampled_duplicates import nearby_episode

Relation = Callable[[str, str, int | None], Mapping[str, Any]]
PairLooksAlike = Callable[[Mapping[str, Any], Mapping[str, Any]], bool | None]
MOTION_KINDS = frozenset({"video", "live-motion"})
# The distance at which two cached previews corroborate each other as one view. The
# burst pass already reads the same 64-bit hash at 8 bits inside a five-minute window;
# this is the looser threshold the final duplicate review works to.
LOOK_ALIKE_HASH_DISTANCE = 10


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


def hash_pair_relation(
    thumbnail_hash: Callable[[str], str | None], *, distance: int = LOOK_ALIKE_HASH_DISTANCE
) -> PairLooksAlike:
    """Whether two carriers repeat each other, from the previews the pipeline already hashed.

    The comparison is bounded to one story or one calendar day, which is where a
    repetition lives: two frames of the same subject a season apart are the memory, not
    an echo. A frame that plays is never a repeat of a still, because a video product
    prefers motion, and a preview with no cached hash answers unknown rather than
    distinct.
    """

    def looks_alike(candidate: Mapping[str, Any], keeper: Mapping[str, Any]) -> bool | None:
        if (
            candidate["story_episode"] != keeper["story_episode"]
            and candidate["taken"][:10] != keeper["taken"][:10]
        ):
            return False
        if candidate.get("kind") in MOTION_KINDS and keeper.get("kind") not in MOTION_KINDS:
            return False
        left, right = thumbnail_hash(candidate["asset_id"]), thumbnail_hash(keeper["asset_id"])
        if not left or not right:
            return None
        return hamming_distance(left, right) <= distance

    return looks_alike


class LookAlikeCheck:
    """The refusals of one selection, inside a fixed bound of pair questions (twice the slots)."""

    def __init__(self, looks_alike: PairLooksAlike | None, *, slots: int) -> None:
        self._looks_alike = looks_alike
        # The same work bound the final duplicate review uses over the finished film: twice the
        # pictures it may hold. Nothing about a particular film sets it.
        self.limit = 2 * slots
        self.checks = 0
        self._answers: dict[tuple[str, str], bool | None] = {}
        self.refused: list[dict[str, Any]] = []
        self.readmitted: list[dict[str, Any]] = []
        self.unchecked = 0
        self.depth: dict[str, Any] = {"added": 0, "refused": [], "unasked": 0}

    @property
    def available(self) -> bool:
        return self._looks_alike is not None

    def _answer(self, candidate: Mapping[str, Any], keeper: Mapping[str, Any]) -> bool | None:
        pair = (candidate["asset_id"], keeper["asset_id"])
        if pair not in self._answers:
            if self._looks_alike is None or self.checks >= self.limit:
                raise _Unasked
            self.checks += 1
            self._answers[pair] = self._looks_alike(candidate, keeper)
        return self._answers[pair]

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
            try:
                if self._answer(candidate, keeper) is True:
                    return keeper["asset_id"]
            except _Unasked:
                self.unchecked += 1
                return None
        return None

    def shows_something_new(
        self, story: str, candidate: Mapping[str, Any], kept: Sequence[Mapping[str, Any]]
    ) -> bool:
        """Depth inside a moment: True only when every compared frame was asked and differs."""
        try:
            repeated = next((k for k in kept if self._answer(candidate, k) is not False), None)
        except _Unasked:
            self.depth["unasked"] += 1
            return False
        if repeated is not None:
            self.depth["refused"].append(
                {"story": story, "asset_id": candidate["asset_id"], "repeats": repeated["asset_id"]}
            )
            return False
        self.depth["added"] += 1
        return True

    def refuse(self, story: str, asset: str, repeats: str, readmit: Callable[[], bool]) -> None:
        self.refused.append(
            {"story": story, "asset_id": asset, "repeats": repeats, "_readmit": readmit}
        )

    def crowds(self, story: str, asset: str, place: str, readmit: Callable[[], bool]) -> None:
        """A picture refused for its place rather than its look (`editorial_story_places`).

        It joins the same ledger, so it frees its slot for another place and comes back through
        the same readmission when nothing else can take it.
        """
        self.refused.append(
            {"story": story, "asset_id": asset, "crowds": place, "_readmit": readmit}
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
            "depth": self.depth,
        }


class _Unasked(Exception):
    """The pair bound is spent; the pair was never asked."""
