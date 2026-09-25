"""The final look-alike review every film runs, on previews it already hashed.

Pictures are read once, at ingest, so this is the only duplicate review a film gets, on every
tier: no pair's pixels go to a model. The perceptual hashes the burst pass already caches answer
the repetition question over the frames a cut actually holds, keeping the picture the product
would keep.

Unlike the selection-time check, this reads every frame of one story or one day against every
other, not a picture's neighbours: by the time the film is settled, two frames of the same
thing may sit four shots apart, and nothing else will ask about them again. It stops at the
edge of a story and a day, because nothing confirms a pair here and an average hash that
agrees about two occasions months apart is agreeing about how flat they are.

A hash only agrees about two frames of one framing, so the review also reads each frame's scene
print (`editorial_scene_prints`) when the run has one: the same path at dusk shot twice, the
same couple's selfie a week apart or the same stage filmed twice in one evening hash as
strangers, yet a viewer sees the film say one thing twice. The scene is read further than the
hash: across stories, within two weeks, because that is where such a repeat sits.

A close family member's only shot in the cut never leaves it: of a pair where one frame is
somebody's only appearance, the other frame goes, a refill must still show them, and when
neither can happen both frames stay. A seat given to someone the period is full of is not
taken back by a pass that only asked whether two frames look alike.

A refused frame does not simply leave a hole. The film asks the moment it came from for
another picture, then the story for a moment it has not shown, and only takes the shortfall
when neither has one that is not itself a repeat.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np

from immich_memories.analysis.duplicate_hashing import hamming_distance
from immich_memories.analysis.editorial_shareability import STORY_CONTEXT_KEYS
from immich_memories.analysis.editorial_story_lookalike import MOTION_KINDS

POLICY = "final-cached-hash-duplicates-v2"

# WHY this is not `SELECTS_MAX_CORROBORATION`: that cap says how far apart two previews may be
# and still have a reader's "same" answer trusted without asking the pair the other way round.
# Nothing confirms a pair here, so the hash is the whole verdict and has to carry it alone.
# Measured at the reader's cap of 10 over thirty no-model runs: 50 of 61 refusals sat at 9 or
# 10 bits, and 20 of those were between frames months apart, the widest 246 days. At 6 of the
# 64 bits of an average hash the pass still takes what a viewer would call the same picture and
# stops guessing at the edge.
STANDALONE_REPEAT_DISTANCE = 6

# Cosine between two scene prints at and above which the frames show one scene. Measured on the
# five cells of the 2026-09-23 contact sheets: every repeat the owner named sat at 0.65 to 0.85,
# and the closest same-day pair of favourites the owner kept as two moments sat at 0.63.
SAME_SCENE_SIMILARITY = 0.65
# How far apart a scene repeat can sit. The named repeats sat three to ten days apart: the same
# ride taken again the next weekend, the same selfie the week after.
SCENE_WINDOW_DAYS = 14
# Two starred frames of one scene this close in time are one moment starred twice (the owner's
# rule): the same pose in the same place on consecutive days. Further apart, two stars are two
# moments, however alike they look.
FAVOURITE_TWIN_DAYS = 2

ScenePrint = Callable[[str], "np.ndarray | None"]
# How many faces a still shows and how sharp it is, for choosing between starred twins.
FrameQuality = Callable[[str], "tuple[int, float] | None"]


def _no_quality(_asset_id: str) -> tuple[int, float] | None:
    return None


def _keep_first(
    carrier: Mapping[str, Any],
    protected: frozenset[str],
    only_shots: frozenset[str],
    quality: FrameQuality = _no_quality,
) -> tuple:
    """Which of two look-alikes stays: the owner's tick, then the star, then motion (a true
    video before a Live Photo's clip), then a close family member's only shot, then, between
    two starred frames, the one showing more faces and then the sharper, then the earlier
    frame. A video product never drops the clip of a thing for a still of it."""
    faces, sharpness = (
        (quality(carrier["asset_id"]) or (0, 0.0)) if carrier.get("favourite") else (0, 0.0)
    )
    return (
        carrier["asset_id"] not in protected,
        not carrier.get("favourite"),
        _MOTION_RANK.get(str(carrier.get("kind")), len(_MOTION_RANK)),
        carrier["asset_id"] not in only_shots,
        -faces,
        -sharpness,
        _taken(carrier),
        carrier["asset_id"],
    )


_MOTION_RANK = {"video": 0, "live-motion": 1}


def _seconds(carrier: Mapping[str, Any]) -> float:
    return float(carrier.get("seconds") or 0.0)


def _taken(carrier: Mapping[str, Any]) -> datetime:
    taken = datetime.fromisoformat(str(carrier["taken"]))
    return taken if taken.tzinfo is not None else taken.replace(tzinfo=UTC)


def _cached_hashes(
    carriers: Sequence[dict[str, Any]], thumbnail_hash: Callable[[str], str | None]
) -> tuple[dict[str, str], list[str]]:
    """The preview hash of every frame in the cut, and the frames that have none."""
    hashes: dict[str, str] = {}
    unavailable: list[str] = []
    for carrier in carriers:
        digest = thumbnail_hash(carrier["asset_id"])
        if digest:
            hashes[carrier["asset_id"]] = digest
        else:
            unavailable.append(carrier["asset_id"])
    return hashes, unavailable


def _within_reach(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Whether two frames are near enough in the film for one to be a repeat of the other.

    One story or one calendar day. Two frames that share neither are two occasions, and an
    average hash agreeing about them says more about how flat they are than about what they
    show.
    """
    same_story = bool(left.get("story_episode")) and left.get("story_episode") == right.get(
        "story_episode"
    )
    return same_story or str(left.get("taken", ""))[:10] == str(right.get("taken", ""))[:10]


def _scene_reach(candidate: Mapping[str, Any], keeper: Mapping[str, Any]) -> bool:
    """Whether the candidate may be read as a repeat of the keeper's scene.

    A star is never refused for a picture the owner did not star, and a frame that plays is
    never refused for a still. Two starred frames are one scene only within
    `FAVOURITE_TWIN_DAYS`: further apart the owner starred two moments. Any other pair is read
    within the scene window.
    """
    if candidate.get("favourite") and not keeper.get("favourite"):
        return False
    if candidate.get("kind") in MOTION_KINDS and keeper.get("kind") not in MOTION_KINDS:
        return False
    if candidate.get("favourite") and keeper.get("favourite"):
        return abs(_taken(candidate) - _taken(keeper)) <= timedelta(days=FAVOURITE_TWIN_DAYS)
    return abs(_taken(candidate) - _taken(keeper)) <= timedelta(days=SCENE_WINDOW_DAYS)


class _Repeats:
    """Which kept frame a frame repeats: by its cached hash first, then by its scene."""

    def __init__(
        self, hashes: dict[str, str], distance: int, scene_print: ScenePrint | None
    ) -> None:
        self.hashes = hashes
        self._distance = distance
        self._scene_print = scene_print
        self._prints: dict[str, np.ndarray | None] = {}
        self.compared = 0
        self.scenes_compared = 0

    def of(
        self, carrier: Mapping[str, Any], kept: Sequence[Mapping[str, Any]]
    ) -> tuple[str | None, float | None]:
        """The kept frame this one repeats, and the scene similarity when the scene said so."""
        keeper = self._by_hash(carrier, kept)
        if keeper is not None:
            return keeper, None
        return self._by_scene(carrier, kept)

    def _by_hash(self, carrier, kept) -> str | None:
        asset_id = carrier["asset_id"]
        if asset_id not in self.hashes:
            return None
        for other in kept:
            if other["asset_id"] not in self.hashes or not _within_reach(carrier, other):
                continue
            self.compared += 1
            distance = hamming_distance(self.hashes[asset_id], self.hashes[other["asset_id"]])
            if distance <= self._distance:
                return other["asset_id"]
        return None

    def _by_scene(self, carrier, kept) -> tuple[str | None, float | None]:
        own = self.print_of(carrier["asset_id"])
        if own is None:
            return None, None
        for other in kept:
            theirs = self.print_of(other["asset_id"]) if _scene_reach(carrier, other) else None
            if theirs is None:
                continue
            self.scenes_compared += 1
            similarity = float(own @ theirs)
            if similarity >= SAME_SCENE_SIMILARITY:
                return other["asset_id"], similarity
        return None, None

    def print_of(self, asset_id: str) -> np.ndarray | None:
        if self._scene_print is None:
            return None
        if asset_id not in self._prints:
            vector = self._scene_print(asset_id)
            norm = float(np.linalg.norm(vector)) if vector is not None else 0.0
            self._prints[asset_id] = (
                np.asarray(vector, dtype=np.float64) / norm if vector is not None and norm else None
            )
        return self._prints[asset_id]

    def unavailable(self) -> list[str]:
        return sorted(asset for asset, vector in self._prints.items() if vector is None)


def _replacement_row(carrier: Mapping[str, Any], unit: Mapping[str, Any]) -> dict[str, Any]:
    """The refused carrier's place in the film, filled by the picture that takes it over.

    The story context is the slot's and stays; everything describing a picture is the
    replacement's own, so no row names a frame the film no longer shows.
    """
    return (
        {key: carrier[key] for key in STORY_CONTEXT_KEYS if key in carrier}
        | dict(unit)
        | {"why": unit.get("why") or "Replaces a frame the film already showed"}
    )


def _refill(
    carrier: Mapping[str, Any],
    offers: Sequence[tuple[str, Mapping[str, Any]]],
    *,
    kept: Sequence[Mapping[str, Any]],
    taken: set[str],
    repeats: _Repeats,
    thumbnail_hash: Callable[[str], str | None],
    admits: Admits,
) -> tuple[str | None, dict[str, Any] | None]:
    """The first offered picture that the film can show instead, and the rung it came from.

    A candidate the film already holds, one whose preview was never cached, and one that
    repeats a frame already kept are all passed over: refilling a repeat with a repeat would
    only move the problem to the next pass. A candidate the audience gate refuses is passed
    over too, and it is asked last, since asking it can cost a model call.
    """
    for rung, unit in offers:
        asset_id = str(unit.get("asset_id") or "")
        if not asset_id or asset_id in taken:
            continue
        digest = repeats.hashes.get(asset_id) or thumbnail_hash(asset_id)
        if not digest:
            continue
        repeats.hashes[asset_id] = digest
        candidate = _replacement_row(carrier, unit)
        repeated, _similarity = repeats.of(candidate, kept)
        if repeated is None and admits(candidate):
            return rung, candidate
    return None, None


FamilyOf = Callable[[str], Collection[str]]
Admits = Callable[[Mapping[str, Any]], bool]


def _no_family(_asset_id: str) -> Collection[str]:
    return ()


def _admit_all(_row: Mapping[str, Any]) -> bool:
    return True


class _Cut:
    """The film as the review settles it, one carrier at a time in keeping order."""

    def __init__(
        self,
        carriers,
        repeats: _Repeats,
        thumbnail_hash,
        content_floor: float,
        family_of: FamilyOf,
        admits: Admits = _admit_all,
        film_floor: tuple[int, float] = (0, 0.0),
    ) -> None:
        self.family_of = family_of
        self.film_floor = film_floor
        self.shown = len(carriers)
        self.admits = admits
        self.shots = Counter(p for c in carriers for p in set(family_of(c["asset_id"])))
        self.kept_only_shots: list[str] = []
        self.repeats = repeats
        self.thumbnail_hash = thumbnail_hash
        self.content_floor = content_floor
        self.content = sum(_seconds(c) for c in carriers)
        self.taken = {c["asset_id"] for c in carriers}
        self.kept: list[Mapping[str, Any]] = []
        self.added: list[dict[str, Any]] = []
        self.removals: list[dict[str, Any]] = []
        self.collapsed: list[dict[str, Any]] = []
        self.rungs: Counter[str] = Counter()

    def settle(self, carrier, offers: Sequence[tuple[str, Mapping[str, Any]]]) -> None:
        keeper, similarity = self.repeats.of(carrier, self.kept)
        if keeper is None:
            self.kept.append(carrier)
            return
        removal: dict[str, Any] = {"asset_id": carrier["asset_id"], "keeper": keeper}
        if similarity is not None:
            removal["same_scene"] = round(similarity, 3)
        twin = self._starred_twin(carrier, keeper)
        alone = {p for p in set(self.family_of(carrier["asset_id"])) if self.shots[p] == 1}
        refilled = self._take_refill(carrier, self._offers(offers, twin=twin, alone=alone), removal)
        if not refilled and alone:
            self.kept.append(carrier)
            self.kept_only_shots.append(carrier["asset_id"])
            return
        if not refilled and (
            (
                similarity is not None
                and not twin
                and self.content - _seconds(carrier) < self.content_floor
            )
            or (twin and self._below_the_film_floor(carrier))
        ):
            self.kept.append(carrier)
            return
        self.content -= _seconds(carrier)
        if not refilled:
            self.shown -= 1
        self.shots.subtract(set(self.family_of(carrier["asset_id"])))
        self.removals.append(removal)
        if twin:
            self.collapsed.append(removal)

    def _below_the_film_floor(self, carrier: Mapping[str, Any]) -> bool:
        """Whether this shot leaving unreplaced would leave too little for any film at all."""
        shots, seconds = self.film_floor
        return self.shown - 1 < shots or self.content - _seconds(carrier) < seconds

    def _starred_twin(self, carrier: Mapping[str, Any], keeper: str) -> bool:
        """Two starred frames of one moment: the twin leaves whatever the film's length says."""
        return bool(carrier.get("favourite")) and any(
            k["asset_id"] == keeper and k.get("favourite") for k in self.kept
        )

    def _offers(self, offers, *, twin: bool, alone: set[str]):
        if twin:
            # Its moment is shown by the keeper: a plain frame of it would ship the moment
            # without its favourite, so only another starred frame of it may take the slot.
            offers = [o for o in offers if o[0] != "moment" or o[1].get("favourite")]
        if alone:
            offers = [o for o in offers if alone <= set(self.family_of(str(o[1].get("asset_id"))))]
        return offers

    def _take_refill(self, carrier, offers, removal: dict[str, Any]) -> bool:
        rung, replacement = _refill(
            carrier,
            offers,
            kept=self.kept,
            taken=self.taken,
            repeats=self.repeats,
            thumbnail_hash=self.thumbnail_hash,
            admits=self.admits,
        )
        if replacement is None or rung is None:
            return False
        removal["replacement"] = replacement["asset_id"]
        self.rungs[rung] += 1
        self.taken.add(replacement["asset_id"])
        self.kept.append(replacement)
        self.added.append(replacement)
        self.content += _seconds(replacement)
        self.shots.update(set(self.family_of(replacement["asset_id"])))
        return True


def review_cut_by_cached_hashes(
    carriers: Sequence[dict[str, Any]],
    *,
    thumbnail_hash: Callable[[str], str | None],
    protected_asset_ids: Sequence[str] = (),
    distance: int = STANDALONE_REPEAT_DISTANCE,
    replacements_for: Callable[[Mapping[str, Any]], Sequence[tuple[str, Mapping[str, Any]]]]
    | None = None,
    scene_print: ScenePrint | None = None,
    content_floor: float = math.inf,
    close_family_of: FamilyOf = _no_family,
    admits: Admits = _admit_all,
    frame_quality: FrameQuality = _no_quality,
    film_floor: tuple[int, float] = (0, 0.0),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Drop the frames of a finished cut that repeat one it already holds, and refill the slot.

    ``replacements_for`` offers, for a refused carrier, ``(rung, unit)`` pairs in the order the
    film would rather show them. A frame with no cached hash is never removed and is named in
    the record, so a run that could not read a preview reports itself incomplete instead of
    silently keeping a repeat. ``scene_print`` gives a frame's scene print, or None when it has
    none; without the port only the hash is read.

    A scene repeat is less certain than a hash repeat, so it only leaves when its slot can be
    spent elsewhere: a replacement takes it, or the film keeps ``content_floor`` seconds without
    it (the film has other material). A film already short of its target keeps its repeats.

    ``close_family_of`` names the close family members a frame shows. A frame that is one of
    them's only shot is kept ahead of its look-alike, is refilled only by a picture that still
    shows them, and otherwise stays (named under ``kept_only_shots``).

    ``admits`` is the audience gate's say on a refill: a slot takes no picture it refuses, and
    when every offer is refused the slot stays empty.

    Two starred frames of one scene within ``FAVOURITE_TWIN_DAYS`` are one moment: the best
    stays (a video, then more faces and a sharper frame from ``frame_quality``, then the
    earlier), the other leaves even from a film short of its target, and both are named under
    ``collapsed_favourites``. It never leaves unreplaced when the film would fall under
    ``film_floor`` (shots, seconds): below that the film abstains, and a starred twin is better
    than no film.
    """
    protected = frozenset(protected_asset_ids)
    hashes, unavailable = _cached_hashes(carriers, thumbnail_hash)
    repeats = _Repeats(hashes, distance, scene_print)
    cut = _Cut(
        carriers,
        repeats,
        thumbnail_hash,
        content_floor,
        close_family_of,
        admits,
        film_floor=film_floor,
    )
    only_shots = frozenset(
        c["asset_id"]
        for c in carriers
        if any(cut.shots[p] == 1 for p in set(close_family_of(c["asset_id"])))
    )
    for carrier in sorted(
        carriers, key=lambda c: _keep_first(c, protected, only_shots, frame_quality)
    ):
        if carrier["asset_id"] in protected:
            cut.kept.append(carrier)
        else:
            cut.settle(carrier, replacements_for(carrier) if replacements_for else ())
    removals, rungs, added = cut.removals, cut.rungs, cut.added
    removed = {row["asset_id"] for row in removals}
    survivors = [c for c in carriers if c["asset_id"] not in removed]
    if added:
        survivors = sorted([*survivors, *added], key=lambda c: str(c.get("taken", "")))
    record = {
        "policy": POLICY,
        "scope": "cached preview hashes within one story or one day; no model comparison",
        "maximum_hash_distance": distance,
        "pairs_compared": repeats.compared,
        "input_carriers": len(carriers),
        "output_carriers": len(survivors),
        "removals": removals,
        "replaced_from": dict(rungs),
        "kept_only_shots": cut.kept_only_shots,
        "collapsed_favourites": cut.collapsed,
        "unavailable": sorted(unavailable),
        "incomplete": bool(unavailable),
    }
    if scene_print is not None:
        record["scene"] = {
            "similarity": SAME_SCENE_SIMILARITY,
            "window_days": SCENE_WINDOW_DAYS,
            "pairs_compared": repeats.scenes_compared,
            "unavailable": repeats.unavailable(),
        }
    return survivors, record
