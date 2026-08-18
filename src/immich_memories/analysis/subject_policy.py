"""What a clip is *of*, and how much of each kind belongs in a memory.

A memory video is about the people in it. Scenery earns its place by being good,
animals are a garnish, and a clip of a lawnmower is not a memory. Selection
scored clips on faces, motion and stability alone, so a steady handheld pan
across a lawn outranked a shaky clip of a child.

The policy acts only on evidence. A clip nobody has described yet is kept, not
rationed -- on a real library 35-46% of the pool has no cached description, and
treating that silence as "probably an object" would delete half the memory.
"""

from __future__ import annotations

import logging
import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class SubjectCategory(Enum):
    """What a clip is primarily of."""

    PEOPLE = "people"
    ANIMAL = "animal"
    LANDSCAPE = "landscape"
    OBJECT = "object"
    UNKNOWN = "unknown"


_PEOPLE_TERMS = frozenset(
    {
        "person",
        "people",
        "man",
        "men",
        "woman",
        "women",
        "child",
        "children",
        "kid",
        "kids",
        "baby",
        "babies",
        "toddler",
        "boy",
        "boys",
        "girl",
        "girls",
        "family",
        "adult",
        "adults",
        "couple",
        "crowd",
        "group",
        "someone",
        "father",
        "mother",
        "dad",
        "mom",
        "parent",
        "parents",
        "friends",
    }
)

_ANIMAL_TERMS = frozenset(
    {
        "dog",
        "dogs",
        "puppy",
        "cat",
        "cats",
        "kitten",
        "kittens",
        "animal",
        "animals",
        "pet",
        "pets",
        "bird",
        "birds",
        "horse",
        "horses",
        "cow",
        "cows",
        "sheep",
        "duck",
        "ducks",
        "chicken",
        "rabbit",
        "fish",
        "goat",
    }
)

# Deliberately narrow, and narrowed further by measurement. "view" and "field"
# were dropped after they classified a treadmill, a bike hub and an office
# renovation as scenery -- each of those descriptions said "close-up view".
# A term earns its place only if it cannot also describe an object in a room.
_LANDSCAPE_TERMS = frozenset(
    {
        "landscape",
        "scenery",
        "vista",
        "panorama",
        "horizon",
        "skyline",
        "sunset",
        "sunrise",
        "mountain",
        "mountains",
        "valley",
        "countryside",
        "sea",
        "ocean",
        "lake",
        "river",
        "waterfall",
        "forest",
        "beach",
        "cliff",
        "cliffs",
        "meadow",
        "shoreline",
    }
)


def classify_subject(
    *,
    tagged_people: int,
    category: str | None = None,
    subjects: Sequence[str] | None = None,
    description: str | None = None,
) -> SubjectCategory:
    """Categorise a clip, most trustworthy signal first.

    1. Immich face tags. Face recognition has already run across the library, so
       a tagged clip needs no model call and no guessing.
    2. The category the VLM was asked to pick from a closed set.
    3. Keywords in the VLM's prose, for the segments cached before the model was
       ever asked for a category.

    People win over anything else in frame -- a child chasing a dog is a memory
    about the child.
    """
    if tagged_people > 0:
        return SubjectCategory.PEOPLE

    stated = _stated_category(category)
    if stated is not None:
        return stated

    words = _words(subjects, description)
    if not words:
        return SubjectCategory.UNKNOWN
    if words & _PEOPLE_TERMS:
        return SubjectCategory.PEOPLE
    if words & _ANIMAL_TERMS:
        return SubjectCategory.ANIMAL
    if words & _LANDSCAPE_TERMS:
        return SubjectCategory.LANDSCAPE
    return SubjectCategory.OBJECT


def _stated_category(category: str | None) -> SubjectCategory | None:
    """The model's own label, when it is one of the ones we asked for."""
    if not category:
        return None
    try:
        stated = SubjectCategory(category.strip().lower())
    except ValueError:
        return None
    return None if stated is SubjectCategory.UNKNOWN else stated


def _words(subjects: Sequence[str] | None, description: str | None) -> set[str]:
    """Lowercased word set over the VLM's structured subjects and its prose."""
    blob = " ".join([*(subjects or []), description or ""])
    return set(re.findall(r"[a-z]+", blob.lower()))


@dataclass(frozen=True)
class SubjectCandidate:
    """A selection candidate reduced to what the subject policy needs."""

    key: str
    category: SubjectCategory
    score: float
    scale: str = "motion"


@dataclass(frozen=True)
class QuotaOutcome:
    """Surviving candidate keys, in input order, and what was cut."""

    kept_keys: list[str]
    dropped: dict[SubjectCategory, int] = field(default_factory=dict)
    bars: dict[str, float] = field(default_factory=dict)
    bypassed: bool = False


def apply_subject_quotas(
    candidates: list[SubjectCandidate],
    *,
    animal_ratio: float,
    object_ratio: float,
    expected_clips: int,
) -> QuotaOutcome:
    """Keep every person, ration animals and objects, make the rest earn a place.

    Scenery and objects must beat the median people-clip score. That bar is
    derived rather than configured because scores are not comparable across
    pools -- photos and video clips sit on different scales, and one fixed
    threshold would silently exclude one of them wholesale.

    Objects are rationed rather than banned: a new car is a memory and a
    lawnmower is not, and the only thing separating them is whether the clip
    is actually any good. Holding objects to the same bar as scenery, in a
    quota of about one per short video, is what tells them apart.
    """
    max_animal = quota_for(animal_ratio, expected_clips)
    max_object = quota_for(object_ratio, expected_clips)

    by_category: dict[SubjectCategory, list[SubjectCandidate]] = {}
    for candidate in candidates:
        by_category.setdefault(candidate.category, []).append(candidate)

    bars = _quality_bars(candidates)

    keep: set[str] = set()
    dropped: dict[SubjectCategory, int] = {}
    for category, members in by_category.items():
        survivors = _survivors(category, members, bars, max_animal, max_object)
        keep.update(c.key for c in survivors)
        if len(survivors) < len(members):
            dropped[category] = len(members) - len(survivors)

    if not keep:
        # A pool of nothing but rationed categories -- an all-object month. A
        # shorter video is the goal; an empty one is a failure, so the policy
        # stands down rather than deleting the memory.
        return QuotaOutcome(
            kept_keys=[c.key for c in candidates],
            bars=bars,
            bypassed=True,
        )

    return QuotaOutcome(
        kept_keys=[c.key for c in candidates if c.key in keep],
        dropped=dropped,
        bars=bars,
    )


def _survivors(
    category: SubjectCategory,
    members: list[SubjectCandidate],
    bars: dict[str, float],
    max_animal: int,
    max_object: int,
) -> list[SubjectCandidate]:
    if category is SubjectCategory.ANIMAL:
        return _top(members, max_animal)

    def clears(candidate: SubjectCandidate) -> bool:
        return candidate.score >= bars.get(candidate.scale, 0.0)

    if category is SubjectCategory.OBJECT:
        return _top([c for c in members if clears(c)], max_object)
    if category is SubjectCategory.LANDSCAPE:
        return [c for c in members if clears(c)]
    return members


def quota_for(ratio: float, expected_clips: int) -> int:
    """How many clips of a rationed category fit in a video of this length.

    A ten-minute video should not get the same allowance as a sixty-second one,
    so quotas are a share of the finished video rather than a fixed count. Any
    non-zero ratio yields at least one slot: 5% of a fifteen-clip video rounds
    below one, and the point of allowing objects at all is that a new car is a
    memory. A ratio of zero is the lever for wanting none.
    """
    if ratio <= 0:
        return 0
    return max(1, round(ratio * expected_clips))


def _top(members: list[SubjectCandidate], limit: int) -> list[SubjectCandidate]:
    if limit <= 0:
        return []
    return sorted(members, key=lambda c: -c.score)[:limit]


def _quality_bars(candidates: list[SubjectCandidate]) -> dict[str, float]:
    """The median people-clip score, computed separately for each score scale.

    Photos and motion clips are scored by different pipelines and land in
    different ranges -- on a real June pool, people motion clips sat at a 0.70
    median while photos sat far below. One pooled median put the bar at 0.43,
    low enough for a clip of a string trimmer to clear it. Each scale is
    therefore judged against its own people.
    """
    by_scale: dict[str, list[float]] = {}
    for candidate in candidates:
        by_scale.setdefault(candidate.scale, []).append(candidate.score)

    people: dict[str, list[float]] = {}
    for candidate in candidates:
        if candidate.category is SubjectCategory.PEOPLE:
            people.setdefault(candidate.scale, []).append(candidate.score)

    return {
        scale: statistics.median(people.get(scale) or scores) for scale, scores in by_scale.items()
    }


def filter_candidates_by_subject(
    candidates: list,
    *,
    animal_ratio: float,
    object_ratio: float,
    content_budget_seconds: float,
    photo_asset_ids: set[str] | None = None,
) -> list:
    """Apply the subject policy to a pool of ClipWithSegment candidates.

    Quotas are a share of the finished video, so the expected clip count is
    estimated from the runtime budget and the typical candidate length rather
    than from the size of the pool, which is many times larger.
    """
    if not candidates:
        return candidates

    expected_clips = _expected_clip_count(candidates, content_budget_seconds)
    photos = photo_asset_ids or set()

    described = [
        SubjectCandidate(
            key=candidate.clip.asset.id,
            category=classify_subject(
                tagged_people=len(candidate.clip.asset.people or []),
                category=candidate.clip.llm_category,
                subjects=candidate.clip.llm_subjects,
                description=candidate.clip.llm_description,
            ),
            score=candidate.score,
            scale="photo" if candidate.clip.asset.id in photos else "motion",
        )
        for candidate in candidates
    ]

    outcome = apply_subject_quotas(
        described,
        animal_ratio=animal_ratio,
        object_ratio=object_ratio,
        expected_clips=expected_clips,
    )
    if outcome.bypassed:
        logger.info(
            "Subject policy: nothing would survive — keeping all %d candidates",
            len(candidates),
        )
        return candidates

    if outcome.dropped:
        counts = ", ".join(
            f"{n} {category.value}" for category, n in sorted(outcome.dropped.items(), key=str)
        )
        logger.info(
            "Subject policy: dropped %s (had to beat %s)",
            counts,
            ", ".join(f"{scale} {bar:.2f}" for scale, bar in sorted(outcome.bars.items())),
        )

    kept = set(outcome.kept_keys)
    return [c for c in candidates if c.clip.asset.id in kept]


def _expected_clip_count(candidates: list, content_budget_seconds: float) -> int:
    """Roughly how many clips the finished video will hold.

    The candidate pool is many times larger than the final selection, so a share
    of the pool would let far too many animals through. The runtime budget
    divided by a typical candidate length is the closest estimate available
    before selection has run.
    """
    lengths = [length for c in candidates if (length := (c.end_time - c.start_time)) > 0]
    if not lengths or content_budget_seconds <= 0:
        return len(candidates)
    return max(1, round(content_budget_seconds / statistics.median(lengths)))
