"""Real observation gateway binds attached frames without changing primary requests."""

import hashlib
from dataclasses import replace

import pytest

from immich_memories.analysis.editorial_bound_sample import (
    BoundVideoSample,
    source_metadata_digest,
)
from immich_memories.analysis.editorial_picture_facts import PROMPT
from immich_memories.analysis.selection_trace import Trace
from immich_memories.api.models import Asset
from tests.test_editorial_picture_facts import fake_transport, preview, provider


def sample(frame):
    return BoundVideoSample(
        source_id="attached-video",
        parent_ids=("admitted-still", "alias-still"),
        link_sha256="1" * 64,
        metadata_sha256="2" * 64,
        payload_sha256="3" * 64,
        start=1.0,
        end=2.0,
        timestamp=1.5,
        frame_sha256=hashlib.sha256(frame).hexdigest(),
        extractor_version="controlled-extractor-v1",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"start": float("nan")},
        {"end": float("inf")},
        {"timestamp": True},
        {"start": -1},
        {"start": 2},
        {"timestamp": 2},
        {"timestamp": 0.9},
        {"parent_ids": ()},
        {"parent_ids": ("p", "p")},
        {"parent_ids": ("attached-video",)},
        {"payload_sha256": "not-a-digest"},
        {"extractor_version": ""},
    ],
)
def test_invalid_or_outside_interval_samples_reject(changes):
    with pytest.raises(ValueError):
        replace(sample(preview()), **changes)


def test_supplied_frame_uses_real_source_and_binding_not_parent_preview(tmp_path, monkeypatch):
    calls = fake_transport(monkeypatch)
    frame = preview("red")
    bound = sample(frame)
    trace = Trace()

    def forbidden(_asset):
        raise AssertionError("attached observations cannot acquire a still or poster")

    reader = provider(tmp_path, read=forbidden, trace=trace)
    try:
        result = reader.observe_sample(bound, frame)
        assert result["status"] == "available"
        assert result["source_asset_id"] == "attached-video"
        assert result["sample_id"] == bound.key
        assert BoundVideoSample.from_dict(result["sample_binding"]) == bound
        assert trace.requests[0].provenance.input_ids == ("attached-video",)
        assert calls[0][0] == PROMPT and len(calls) == 1
        assert "admitted-still" not in calls[0][0]
        assert reader.observe_sample(bound, frame) == result
        with pytest.raises(ValueError, match="differs"):
            reader.observe_sample(bound, preview("blue"))
        assert len(calls) == 1
    finally:
        reader.close()

    async def no_model(*args, **kwargs):
        raise AssertionError("exact bound sample must replay")

    monkeypatch.setattr("immich_memories.analysis.editorial_gateway.query_llm", no_model)
    warm = provider(tmp_path, read=forbidden)
    try:
        assert warm.observe_sample(bound, frame) == result
        assert warm.metrics()["inference_calls"] == 0
        assert warm.metrics()["cache_hits"] == 1
    finally:
        warm.close()


def test_same_video_and_pixels_with_changed_material_never_reuse_wrong_sample(
    tmp_path, monkeypatch
):
    calls = fake_transport(monkeypatch)
    frame = preview()
    original = sample(frame)
    variants = [
        original,
        replace(original, start=1.1),
        replace(original, timestamp=1.6),
        replace(original, payload_sha256="4" * 64),
        replace(original, link_sha256="5" * 64),
        replace(original, metadata_sha256="6" * 64),
    ]
    reader = provider(tmp_path)
    try:
        records = [reader.observe_sample(bound, frame) for bound in variants]
        assert len({record["identity"] for record in records}) == len(variants)
        assert len({record["image_sha256"] for record in records}) == 1
        assert len(calls) == len(variants)
        assert original.key == replace(original, start=1, end=2).key
    finally:
        reader.close()


def test_sample_and_primary_observations_are_separate_but_primary_keys_stay_exact(
    tmp_path, monkeypatch
):
    calls = fake_transport(monkeypatch)
    frame = preview()
    bound = sample(frame)
    reader = provider(tmp_path, read=lambda _: frame)
    try:
        primary = reader.observe(bound.source_id)
        attached = reader.observe_sample(bound, frame)
        assert primary["identity"] != attached["identity"]
        assert "sample_binding" not in primary
        assert reader.observe(bound.source_id) == primary
        # Even a library ID with a sample-looking string cannot collide in the memo.
        assert "sample_binding" not in reader.observe(bound.key)
        assert len(calls) == 3
    finally:
        reader.close()


# Immich sends every one of these for an ordinary photo. The sample digest binds the
# whole asset payload, so a wire field the model stops parsing makes two genuinely
# different sources one evidence key -- and silently re-keys every banked observation
# of the assets that carry it, for every user, on upgrade.
IMMICH_EXIF = {
    "make": ("Apple", "Canon"),
    "model": ("iPhone 13 Pro", "EOS R6"),
    "exposureTime": ("1/120", "1/60"),
    "fNumber": (1.5, 2.8),
    "iso": (250, 400),
    "focalLength": (5.7, 35.0),
    "latitude": (50.85, 48.85),
    "longitude": (4.35, 2.35),
    "city": ("Brussels", "Paris"),
    "state": ("Brussels", "Grand Est"),
    "country": ("Belgium", "France"),
    "dateTimeOriginal": ("2026-06-01T10:00:00+00:00", "2026-06-01T11:00:00+00:00"),
    "lensModel": ("back dual wide camera", "RF24-105mm"),
    "fileSizeInByte": (2_500_000, 3_100_000),
}


def _asset_with(exif):
    return Asset.model_validate(
        {
            "id": "admitted-still",
            "type": "IMAGE",
            "fileCreatedAt": "2026-06-01T10:00:00+00:00",
            "fileModifiedAt": "2026-06-01T10:00:00+00:00",
            "updatedAt": "2026-06-01T10:00:00+00:00",
            "exifInfo": exif,
        }
    )


@pytest.mark.parametrize("field", sorted(IMMICH_EXIF))
def test_assets_immich_reports_differently_are_different_evidence(field):
    base = {key: values[0] for key, values in IMMICH_EXIF.items()}
    assert source_metadata_digest(_asset_with(base)) != source_metadata_digest(
        _asset_with(base | {field: IMMICH_EXIF[field][1]})
    )
