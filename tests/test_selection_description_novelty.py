"""Backward-only measurements over the permanent literal-description bank."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from immich_memories.analysis.editorial_contracts import DecisionProvenance
from immich_memories.analysis.selection_descriptions import AssetDescription
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from tests.conftest import make_asset


def _description(asset_id: str, text: str) -> AssetDescription:
    return AssetDescription(
        asset_id=asset_id,
        text=text,
        provenance=DecisionProvenance(
            pass_name="asset-description",  # noqa: S106
            pass_version="asset-description-v1",  # noqa: S106
            schema_version="asset-description-v1",
            model_identity="vision-test",
            input_ids=(asset_id,),
            sheet_hashes=(f"sheet-{asset_id}",),
            request_key=f"request-{asset_id}",
            cache_hit=False,
        ),
    )


def _prepared(*asset_ids: str):
    when = datetime(2024, 1, 1, tzinfo=UTC)
    return prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: tuple(
                make_asset(asset_id, file_created_at=when + timedelta(days=index))
                for index, asset_id in enumerate(asset_ids)
            )
        ),
    )


def test_future_descriptions_cannot_change_an_earlier_novelty_observation() -> None:
    """Novelty is a fact about what was known then, never the eventual library."""
    from immich_memories.analysis.selection_description_novelty import (
        measure_description_novelty,
    )

    descriptions = (
        _description("appliance", "a white washing machine in a small kitchen"),
        _description("bicycle", "a red bicycle leaning in an apartment hallway"),
        _description("appliance-again", "a white washing machine with its round door open"),
        _description("bicycle-future", "a red bicycle leaning in an apartment hallway"),
    )
    before_future = measure_description_novelty(
        _prepared("appliance", "bicycle", "appliance-again"),
        descriptions[:3],
    )
    after_future = measure_description_novelty(
        _prepared("appliance", "bicycle", "appliance-again", "bicycle-future"),
        descriptions,
    )

    assert after_future.observations[:3] == before_future.observations
    first, bicycle, repeated_appliance, future_duplicate = after_future.observations
    assert first.prefix_description_count == 0
    assert first.closest_word_set is None
    assert first.closest_character_trigrams is None
    assert repeated_appliance.closest_word_set is not None
    assert bicycle.closest_word_set is not None
    assert repeated_appliance.closest_word_set.similarity > bicycle.closest_word_set.similarity
    assert future_duplicate.prefix_description_count == 3
    assert future_duplicate.closest_word_set is not None
    assert future_duplicate.closest_word_set.asset_id == "bicycle"
    assert future_duplicate.closest_word_set.similarity == 1.0
    assert future_duplicate.closest_character_trigrams is not None
    assert future_duplicate.closest_character_trigrams.asset_id == "bicycle"
    assert future_duplicate.closest_character_trigrams.similarity == 1.0


def test_owner_controls_are_reported_against_a_fixed_chance_floor() -> None:
    """The probe measures separation; it does not derive a keep threshold."""
    from immich_memories.analysis.selection_description_novelty import (
        description_novelty_report,
        evaluate_description_novelty_controls,
        measure_description_novelty,
    )

    descriptions = (
        _description("old-appliance", "a white washing machine in a small kitchen"),
        _description("old-bicycle", "a red bicycle leaning in an apartment hallway"),
        _description("unique-control", "a yellow toy spaceship on a purple rug"),
        _description("ordinary-control", "a red bicycle leaning in an apartment hallway"),
    )
    observations = measure_description_novelty(
        _prepared(*(description.asset_id for description in descriptions)),
        descriptions,
    )

    control = evaluate_description_novelty_controls(
        observations,
        should_surface_ids=("unique-control",),
        ordinary_ids=("ordinary-control",),
    )

    assert control.chance_floor == 0.5
    assert control.word_set.pair_comparisons == 1
    assert control.word_set.pairwise_accuracy == 1.0
    assert control.character_trigrams.pair_comparisons == 1
    assert control.character_trigrams.pairwise_accuracy == 1.0
    assert description_novelty_report(observations, control)["control"] == {
        "chance_floor": 0.5,
        "word_set": {
            "pair_comparisons": 1,
            "pairwise_accuracy": 1.0,
            "unavailable_ids": [],
        },
        "character_trigrams": {
            "pair_comparisons": 1,
            "pairwise_accuracy": 1.0,
            "unavailable_ids": [],
        },
    }
