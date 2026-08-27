"""Measure literal-description recurrence without making an editorial decision."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from immich_memories.analysis.selection_descriptions import AssetDescription
from immich_memories.analysis.selection_source import PreparedEditorialSource

__all__ = [
    "DescriptionNoveltyControlResult",
    "DescriptionNoveltyObservation",
    "DescriptionNoveltyResult",
    "MetricControlSeparation",
    "PriorDescriptionMatch",
    "description_novelty_report",
    "evaluate_description_novelty_controls",
    "measure_description_novelty",
]

_WORD_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
_CHARACTER_NGRAM_SIZE = 3


@dataclass(frozen=True)
class PriorDescriptionMatch:
    """The closest earlier description under one named lexical view."""

    asset_id: str
    similarity: float


@dataclass(frozen=True)
class DescriptionNoveltyObservation:
    """Raw backward-prefix evidence for one described asset."""

    asset_id: str
    prefix_description_count: int
    closest_word_set: PriorDescriptionMatch | None
    closest_character_trigrams: PriorDescriptionMatch | None


@dataclass(frozen=True)
class DescriptionNoveltyResult:
    """Chronological observations with no keep, reject, rank or threshold."""

    observations: tuple[DescriptionNoveltyObservation, ...]


@dataclass(frozen=True)
class MetricControlSeparation:
    """Pairwise separation from owner controls under one lexical view."""

    pair_comparisons: int
    pairwise_accuracy: float | None
    unavailable_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class DescriptionNoveltyControlResult:
    """Two observational metrics beside their fixed chance floor."""

    word_set: MetricControlSeparation
    character_trigrams: MetricControlSeparation
    chance_floor: float = 0.5


@dataclass(frozen=True)
class _DescriptionFeatures:
    asset_id: str
    words: frozenset[str]
    character_trigrams: frozenset[str]


def measure_description_novelty(
    prepared: PreparedEditorialSource,
    descriptions: tuple[AssetDescription, ...],
) -> DescriptionNoveltyResult:
    """Compare each description only with descriptions earlier in capture time."""
    description_by_id = _index_descriptions(descriptions)
    unknown_ids = set(description_by_id).difference(prepared.candidate_ids)
    if unknown_ids:
        raise ValueError("description assets must belong to the prepared prefix")

    prior: list[_DescriptionFeatures] = []
    observations: list[DescriptionNoveltyObservation] = []
    for candidate in prepared.candidates:
        description = description_by_id.get(candidate.asset_id)
        if description is None:
            continue
        features = _features(description)
        observations.append(
            DescriptionNoveltyObservation(
                asset_id=candidate.asset_id,
                prefix_description_count=len(prior),
                closest_word_set=_closest(features.words, prior),
                closest_character_trigrams=_closest(
                    features.character_trigrams,
                    prior,
                    use_character_trigrams=True,
                ),
            )
        )
        prior.append(features)
    return DescriptionNoveltyResult(tuple(observations))


def evaluate_description_novelty_controls(
    result: DescriptionNoveltyResult,
    *,
    should_surface_ids: tuple[str, ...],
    ordinary_ids: tuple[str, ...],
) -> DescriptionNoveltyControlResult:
    """Measure whether owner positives are less recurrent than owner negatives."""
    _validate_control_ids(should_surface_ids, ordinary_ids)
    by_id = {observation.asset_id: observation for observation in result.observations}
    return DescriptionNoveltyControlResult(
        word_set=_control_separation(
            by_id,
            should_surface_ids,
            ordinary_ids,
            closest=lambda observation: observation.closest_word_set,
        ),
        character_trigrams=_control_separation(
            by_id,
            should_surface_ids,
            ordinary_ids,
            closest=lambda observation: observation.closest_character_trigrams,
        ),
    )


def description_novelty_report(
    result: DescriptionNoveltyResult,
    control: DescriptionNoveltyControlResult | None = None,
) -> dict[str, object]:
    """Return replayable observations without inventing an editorial verdict."""
    return {
        "observations": [
            {
                "asset_id": observation.asset_id,
                "prefix_description_count": observation.prefix_description_count,
                "closest_word_set": _match_report(observation.closest_word_set),
                "closest_character_trigrams": _match_report(observation.closest_character_trigrams),
            }
            for observation in result.observations
        ],
        "control": _control_report(control) if control is not None else None,
    }


def _index_descriptions(
    descriptions: tuple[AssetDescription, ...],
) -> dict[str, AssetDescription]:
    indexed = {description.asset_id: description for description in descriptions}
    if len(indexed) != len(descriptions):
        raise ValueError("asset descriptions must have unique asset IDs")
    return indexed


def _validate_control_ids(
    should_surface_ids: tuple[str, ...], ordinary_ids: tuple[str, ...]
) -> None:
    all_ids = (*should_surface_ids, *ordinary_ids)
    if len(set(all_ids)) != len(all_ids):
        raise ValueError("owner controls must be unique and belong to one class")


def _match_report(match: PriorDescriptionMatch | None) -> dict[str, object] | None:
    if match is None:
        return None
    return {"asset_id": match.asset_id, "similarity": match.similarity}


def _control_report(control: DescriptionNoveltyControlResult) -> dict[str, object]:
    return {
        "chance_floor": control.chance_floor,
        "word_set": _metric_control_report(control.word_set),
        "character_trigrams": _metric_control_report(control.character_trigrams),
    }


def _metric_control_report(control: MetricControlSeparation) -> dict[str, object]:
    return {
        "pair_comparisons": control.pair_comparisons,
        "pairwise_accuracy": control.pairwise_accuracy,
        "unavailable_ids": list(control.unavailable_ids),
    }


def _control_separation(
    observations: dict[str, DescriptionNoveltyObservation],
    should_surface_ids: tuple[str, ...],
    ordinary_ids: tuple[str, ...],
    *,
    closest: Callable[[DescriptionNoveltyObservation], PriorDescriptionMatch | None],
) -> MetricControlSeparation:
    unavailable: list[str] = []

    def _similarities(asset_ids: tuple[str, ...]) -> tuple[float, ...]:
        similarities: list[float] = []
        for asset_id in asset_ids:
            observation = observations.get(asset_id)
            match = closest(observation) if observation is not None else None
            if match is None:
                unavailable.append(asset_id)
            else:
                similarities.append(match.similarity)
        return tuple(similarities)

    positive = _similarities(should_surface_ids)
    ordinary = _similarities(ordinary_ids)
    pairwise = tuple(
        1.0 if surface < repeated else 0.5 if surface == repeated else 0.0
        for surface in positive
        for repeated in ordinary
    )
    return MetricControlSeparation(
        pair_comparisons=len(pairwise),
        pairwise_accuracy=sum(pairwise) / len(pairwise) if pairwise else None,
        unavailable_ids=tuple(unavailable),
    )


def _features(description: AssetDescription) -> _DescriptionFeatures:
    tokens = tuple(_WORD_PATTERN.findall(description.text.casefold()))
    words = frozenset(tokens)
    normalized = " ".join(tokens)
    character_trigrams = frozenset(
        normalized[index : index + _CHARACTER_NGRAM_SIZE]
        for index in range(max(1, len(normalized) - _CHARACTER_NGRAM_SIZE + 1))
    )
    return _DescriptionFeatures(description.asset_id, words, character_trigrams)


def _closest(
    current: frozenset[str],
    prior: list[_DescriptionFeatures],
    *,
    use_character_trigrams: bool = False,
) -> PriorDescriptionMatch | None:
    closest: PriorDescriptionMatch | None = None
    for earlier in prior:
        earlier_values = earlier.character_trigrams if use_character_trigrams else earlier.words
        similarity = _jaccard(current, earlier_values)
        if closest is None or similarity > closest.similarity:
            closest = PriorDescriptionMatch(earlier.asset_id, similarity)
    return closest


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union_size = len(left | right)
    return len(left & right) / union_size if union_size else 0.0
