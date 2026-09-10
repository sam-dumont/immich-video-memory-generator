"""What a clip is *of*, and how much of each kind belongs in a memory.

A memory video is about the people in it. Scenery earns its place by being good,
animals are a garnish, and a clip of a lawnmower is not a memory. Selection
scored clips on faces, motion and stability alone, so a steady handheld pan
across a lawn outranked a shaky clip of a child.

The policy acts only on evidence, and the evidence is a label, never prose.
Keyword matching over the model's description was tried and measured: it called
a treadmill and a driver's-eye road view "landscape" because both descriptions
said "close-up view", a tray of animal figurines "animal", and a smartwatch
demo "people" because a person was holding the watch. The model is now asked
for the category outright. A clip it has not labelled is unknown, and unknown
is kept -- on a real library a third of the pool has no analysis yet, and
treating that silence as "probably an object" would delete half the memory.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class SubjectCategory(Enum):
    """What a clip is primarily of."""

    PEOPLE = "people"
    ANIMAL = "animal"
    LANDSCAPE = "landscape"
    OBJECT = "object"
    SCREEN = "screen"
    UNKNOWN = "unknown"


def classify_subject(
    *,
    tagged_people: int,
    category: str | None = None,
    description: str | None = None,
) -> SubjectCategory:
    """Categorise a clip from Immich face tags and the model's own label.

    Face tags settle it outright -- face recognition has already run across the
    library, so a tagged clip needs no model call. Otherwise the answer is
    whichever category the model was asked to choose, and nothing else. The
    description is accepted so callers can pass what they have, and is
    deliberately ignored; the module docstring records what happened when it
    was trusted.
    """
    if tagged_people > 0:
        return SubjectCategory.PEOPLE
    return _stated_category(category) or SubjectCategory.UNKNOWN


def subject_evidence(*, tagged_people: int, category: str | None) -> str:
    """Expose the closed-set observation without making a membership decision."""
    return (
        f"subject-evidence:{classify_subject(tagged_people=tagged_people, category=category).value}"
    )


def _stated_category(category: str | None) -> SubjectCategory | None:
    """The model's own label, when it is one of the ones we asked for."""
    if not category:
        return None
    try:
        stated = SubjectCategory(category.strip().lower())
    except ValueError:
        return None
    return None if stated is SubjectCategory.UNKNOWN else stated


@dataclass(frozen=True)
class SubjectCandidate:
    """A selection candidate reduced to what the subject policy needs."""

    key: str
    category: SubjectCategory
    score: float
    scale: str = "motion"
    labelled: bool = True


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

    if category is SubjectCategory.SCREEN:
        return []
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
    """The median people-clip score, per score scale, over comparable scores only.

    Two things make scores incomparable. Photos and motion clips come from
    different pipelines and land in different ranges, so each scale gets its own
    bar -- pooling them once put the bar at 0.43, low enough for a clip of a
    string trimmer scoring 0.61 to clear it.

    Within a scale, a score is only a semantic judgement if the clip was actually
    analysed. One that was merely pre-filtered carries Asset.quality_score --
    resolution and bitrate, not how good the moment is -- and one whose analysis
    failed carries 0.0. Only clips the model labelled went through the same
    scoring as the objects and scenery being judged against them, so only those
    set the bar. A real run mixing all three produced a 0.31 motion bar against a
    labelled-people median of about 0.83.
    """
    by_scale: dict[str, list[float]] = {}
    comparable: dict[str, list[float]] = {}
    for candidate in candidates:
        by_scale.setdefault(candidate.scale, []).append(candidate.score)
        if candidate.category is SubjectCategory.PEOPLE and candidate.labelled:
            comparable.setdefault(candidate.scale, []).append(candidate.score)

    return {
        scale: statistics.median(comparable.get(scale) or scores)
        for scale, scores in by_scale.items()
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
            ),
            score=candidate.score,
            scale="photo" if candidate.clip.asset.id in photos else "motion",
            labelled=bool(candidate.clip.llm_category),
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
            "Subject policy: dropped %s (had to beat %s, from %d labelled people clips)",
            counts,
            ", ".join(f"{scale} {bar:.2f}" for scale, bar in sorted(outcome.bars.items())),
            sum(1 for c in described if c.labelled and c.category is SubjectCategory.PEOPLE),
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
