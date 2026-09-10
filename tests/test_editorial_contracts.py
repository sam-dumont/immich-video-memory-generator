"""Editorial pass records are safe to hand from one pass to the next."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from immich_memories.analysis.editorial_contracts import (
    ConservationCheck,
    DecisionProvenance,
    InsightEvidence,
    LivePhotoRenderingFamily,
    PassTrace,
    PeriodInsight,
    RequestTrace,
    TraceDecision,
    live_photo_rendering_family_id,
)
from immich_memories.processing.live_material import LiveRenderMaterial, LiveSourceEntry


def test_editorial_contracts_cannot_rewrite_a_previous_pass() -> None:
    """A later pass cannot mutate the inputs, fate, or request it inherited."""
    provenance = DecisionProvenance(
        pass_name="cull",  # noqa: S106 - test-only pass identity
        pass_version="1",  # noqa: S106 - test-only pass identity
        schema_version="1",
        model_identity="test-model",
        input_ids=("first", "second"),
        sheet_hashes=("sheet-hash",),
        request_key="request-key",
        cache_hit=False,
    )
    decision = TraceDecision("second", "unusable exposure")
    pass_trace = PassTrace(
        name="cull",
        input_ids=("first", "second"),
        kept_ids=("first",),
        rejected=(decision,),
        unresolved=(),
        duration_before=8.0,
        duration_after=4.0,
        provenance=provenance,
    )
    request = RequestTrace(provenance=provenance, attached_sheet_hashes=("sheet-hash",))
    conservation = ConservationCheck(valid=True, missing_ids=(), duplicate_ids=())

    with pytest.raises(FrozenInstanceError):
        provenance.input_ids = ("replacement",)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        decision.reason = "replacement"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        pass_trace.kept_ids = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        request.attached_sheet_hashes = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        conservation.valid = False  # type: ignore[misc]


def _provenance() -> DecisionProvenance:
    return DecisionProvenance(
        pass_name="insight",  # noqa: S106 - test-only pass identity
        pass_version="1",  # noqa: S106 - test-only pass identity
        schema_version="1",
        model_identity="test-model",
        input_ids=("first",),
        sheet_hashes=("sheet-hash",),
        request_key="request-key",
        cache_hit=False,
    )


def _family_fields(**overrides) -> dict:
    return {
        "still_ids": ("still-a", "still-b"),
        "video_ids": ("video-a", "video-b"),
        "trim_points": ((0.0, 2.0), (1.5, 3.0)),
        "shutter_timestamps": (1.0, 2.0),
        "motion_duration_seconds": 3.5,
        "minimum_motion_seconds": 1.0,
        "material": None,
        **overrides,
    }


def _family(**overrides) -> LivePhotoRenderingFamily:
    fields = _family_fields(**overrides)
    family_id = live_photo_rendering_family_id(
        fields["still_ids"],
        fields["video_ids"],
        fields["trim_points"],
        fields["shutter_timestamps"],
        motion_duration_seconds=fields["motion_duration_seconds"],
        minimum_motion_seconds=fields["minimum_motion_seconds"],
        material=fields["material"],
    )
    return LivePhotoRenderingFamily(family_id=family_id, **fields)


def test_a_rendering_family_id_that_no_longer_hashes_its_manifest_is_refused() -> None:
    """A stale ID would route one burst's stills through another burst's trims."""
    original = _family()

    with pytest.raises(ValueError, match="hash its canonical manifest"):
        LivePhotoRenderingFamily(
            family_id=original.family_id,
            **_family_fields(trim_points=((0.0, 2.0), (1.5, 2.5))),
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"still_ids": ()},
        {"still_ids": ("still-a", "still-a")},
        {"video_ids": ("video-a", "video-a")},
        {"still_ids": ("still-a", " ")},
        {"trim_points": ((0.0, 2.0), (3.0, 1.5))},
        {"trim_points": ((-1.0, 2.0), (1.5, 3.0))},
        {"shutter_timestamps": (1.0, float("nan"))},
        {"shutter_timestamps": (2.0, 1.0)},
        {"motion_duration_seconds": None},
        {"minimum_motion_seconds": 0.0},
    ],
)
def test_a_rendering_family_without_safe_aligned_sources_is_refused(overrides: dict) -> None:
    with pytest.raises(ValueError):
        LivePhotoRenderingFamily(family_id="unchecked", **_family_fields(**overrides))


def test_canonical_material_replaces_the_aligned_arrays_as_the_authority() -> None:
    """The arrays are a projection of the material, so they cannot disagree with it."""
    material = LiveRenderMaterial(
        (
            LiveSourceEntry("still-a", "video-a", 1.0, 0.0, 2.0),
            LiveSourceEntry("still-b", "video-b", 2.0, 1.5, 3.0),
            LiveSourceEntry("still-c", "video-c", 3.0, 1.0, 1.0),
        )
    )
    from_material = _family(
        material=material,
        still_ids=material.still_ids,
        video_ids=material.video_ids,
        trim_points=material.trim_points,
        shutter_timestamps=material.shutter_timestamps,
    )

    assert from_material.family_id.startswith("live-photo-rendering-family-v2-positive-segments-")
    with pytest.raises(ValueError, match="arrays disagree"):
        LivePhotoRenderingFamily(
            family_id="unchecked",
            **_family_fields(
                material=material,
                still_ids=material.still_ids,
                video_ids=("unrelated",),
                trim_points=material.trim_points,
                shutter_timestamps=material.shutter_timestamps,
            ),
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"observation": "  "},
        {"episode_ids": ()},
        {"episode_ids": (" ",)},
        {"asset_ids": ()},
        {"asset_ids": (" ",)},
    ],
)
def test_an_observation_nobody_can_trace_back_to_pictures_is_refused(overrides: dict) -> None:
    fields = {
        "observation": "the same walk recurs",
        "episode_ids": ("episode-1",),
        "asset_ids": ("asset-1",),
        **overrides,
    }
    with pytest.raises(ValueError):
        InsightEvidence(**fields)


@pytest.mark.parametrize(
    "overrides",
    [
        {"revision": -1},
        {"unavailable_reason": "no wall"},
        {"thesis": None},
        {"thesis": "  "},
        {"evidence": ()},
        {"thesis": None, "unavailable_reason": " "},
    ],
)
def test_a_period_reading_must_be_exactly_a_grounded_thesis_or_a_stated_absence(
    overrides: dict,
) -> None:
    """A blank half-reading downstream reads as a period with nothing worth showing."""
    fields = {
        "thesis": "a month of long walks",
        "evidence": (
            InsightEvidence(
                observation="the same walk recurs",
                episode_ids=("episode-1",),
                asset_ids=("asset-1",),
            ),
        ),
        "tensions": (),
        "recurring_threads": (),
        "unavailable_reason": None,
        "revision": 0,
        "provenance": _provenance(),
        **overrides,
    }
    with pytest.raises(ValueError):
        PeriodInsight(**fields)
