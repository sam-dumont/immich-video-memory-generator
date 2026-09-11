"""One annotation snapshot must partition its request and carry no blank facts."""

from __future__ import annotations

import pytest

from immich_memories.analysis.annotation_lines import (
    AnnotationContract,
    AnnotationLineBatch,
    AssetAnnotationLine,
)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"renderer_version": " ", "producer_versions": ("pixel:v1",)},
        {"renderer_version": "v1", "producer_versions": ()},
        {"renderer_version": "v1", "producer_versions": ("pixel:v1", "pixel:v1")},
        {"renderer_version": "v1", "producer_versions": ("pixel:v1", " ")},
    ],
)
def test_an_annotation_contract_that_cannot_key_a_cache_is_refused(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        AnnotationContract(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"asset_id": " ", "text": "a line"},
        {"asset_id": "a", "text": " "},
        {"asset_id": "a", "text": "a line", "description": " "},
        {"asset_id": "a", "text": "a line", "heads": (("activity", " "),)},
        {"asset_id": "a", "text": "a line", "heads": (("activity", "x"), ("activity", "y"))},
        {"asset_id": "a", "text": "a line", "stitching_burst_id": " "},
    ],
)
def test_an_annotation_line_with_blank_or_repeated_facts_is_refused(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        AssetAnnotationLine(**kwargs)


@pytest.mark.parametrize(
    "requested,lines,missing",
    [
        ((), (), ()),
        (("a", "a"), (), ("a",)),
        (("a", "b"), (AssetAnnotationLine("b", "second"), AssetAnnotationLine("a", "first")), ()),
        (("a", "b"), (AssetAnnotationLine("a", "first"),), ()),
        (("a", "b"), (AssetAnnotationLine("a", "first"),), ("a", "b")),
    ],
)
def test_a_batch_whose_outcomes_do_not_partition_the_request_is_refused(
    requested: tuple, lines: tuple, missing: tuple
) -> None:
    """Losing track of which assets were answered silently drops occasions."""
    with pytest.raises(ValueError):
        AnnotationLineBatch(
            requested_asset_ids=requested,
            lines=lines,
            missing_asset_ids=missing,
            contract=AnnotationContract("v1", ("pixel:v1",)),
        )
