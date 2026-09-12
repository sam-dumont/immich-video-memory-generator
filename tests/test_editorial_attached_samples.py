"""Attached acquisition binds real metadata and exact bytes without widening selection."""

from __future__ import annotations

import hashlib
import io
import json
import socket
from datetime import UTC, datetime

import pytest
from PIL import Image

from immich_memories.analysis.editorial_attached_samples import AttachedVideoSamples
from immich_memories.analysis.editorial_bound_sample import (
    attached_link_digest,
    source_metadata_digest,
)
from immich_memories.api.models import AssetType
from tests.conftest import make_asset


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("attached acquisition tests must not reach a network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def source():
    captured_at = datetime(2030, 4, 1, tzinfo=UTC)
    parent = make_asset("still", file_created_at=captured_at).model_copy(
        update={"type": AssetType.IMAGE, "live_photo_video_id": "video"}
    )
    alias = parent.model_copy(update={"id": "alias"}, deep=True)
    video = make_asset("video", file_created_at=captured_at).model_copy(
        update={"duration_seconds": 10.0}
    )
    return {"still": parent, "alias": alias}, {"video": video}


class Effects:
    def __init__(self, *, payload=b"controlled playback bytes"):
        self.payload = payload
        self.fetches = []
        self.decodes = []

    def fetch(self, asset_id):
        self.fetches.append(asset_id)
        return self.payload

    def extract(self, video, *, timestamp, width, output_path):
        self.decodes.append((video.read_bytes(), timestamp, width))
        image = Image.new("RGB", (width, width // 2), (int(timestamp * 20), 40, 100))
        image.save(output_path, "JPEG")
        return output_path


def provider(path, effects, *, assets=None, companions=None, **kwargs):
    captured, attached = source()
    return AttachedVideoSamples(
        assets=assets if assets is not None else captured,
        allowed_ids=captured.keys(),
        companion_assets=companions if companions is not None else attached,
        cache_dir=path,
        fetch_playback=effects.fetch,
        extract=effects.extract,
        **kwargs,
    )


def test_midpoint_cache_is_exact_and_warm_fetch_decode_are_forbidden(tmp_path):
    effects = Effects()
    cold = provider(tmp_path, effects)
    result = cold.acquire("video", parent_ids=("still", "alias"), start=1, end=3)
    assert result is not None
    sample, frame, metadata = result
    parents, videos = source()
    assert sample.parent_ids == ("still", "alias") and sample.source_id == metadata.id == "video"
    assert sample.link_sha256 == attached_link_digest(tuple(parents.values()), "video")
    assert sample.metadata_sha256 == source_metadata_digest(videos["video"])
    assert sample.payload_sha256 == hashlib.sha256(effects.payload).hexdigest()
    assert sample.frame_sha256 == hashlib.sha256(frame).hexdigest()
    assert (sample.start, sample.end, sample.timestamp) == (1, 3, 2)
    assert "transcoded-playback" in sample.extractor_version
    assert cold.acquire("video", parent_ids=("still", "alias"), start=1.0, end=3.0) == result
    assert effects.fetches == ["video"] and len(effects.decodes) == 1
    with Image.open(io.BytesIO(frame)) as image:
        assert image.width == 800

    blocked = Effects()
    blocked.fetch = lambda _: pytest.fail("warm must not fetch playback")
    blocked.extract = lambda *_a, **_k: pytest.fail("warm must not decode video")
    warm = provider(tmp_path, blocked)
    assert warm.acquire("video", parent_ids=("still", "alias"), start=1, end=3) == result
    assert warm.metrics()["fetch_attempts"] == warm.metrics()["frame_decodes"] == 0
    assert warm.metrics()["playback_cache_hits"] == warm.metrics()["frame_cache_hits"] == 1
    # The provider never grants the companion a primary selectable identity.
    assert set(parents) == {"still", "alias"}


def test_one_fetch_per_video_one_decode_per_interval_and_parent_links_are_independent(tmp_path):
    effects = Effects()
    cache = provider(tmp_path, effects)
    first = cache.acquire("video", parent_ids=("still",), start=0, end=2)
    second = cache.acquire("video", parent_ids=("alias",), start=0, end=2)
    third = cache.acquire("video", parent_ids=("still",), start=2, end=4)
    assert first is not None and second is not None and third is not None
    assert first[0].key != second[0].key != third[0].key
    assert first[1] == second[1] != third[1]
    assert len(effects.fetches) == 1 and len(effects.decodes) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"parent_ids": ()},
        {"parent_ids": ("unknown",)},
        {"parent_ids": ("still", "still")},
        {"parent_ids": "still"},
        {"video_id": "undeclared"},
        {"start": -1},
        {"start": True},
        {"end": 0},
        {"end": float("nan")},
        {"end": float("inf")},
        {"end": 11},
    ],
)
def test_unadmitted_links_or_invalid_display_intervals_do_no_work(tmp_path, changes):
    effects = Effects()
    cache = provider(tmp_path, effects)
    request = {"video_id": "video", "parent_ids": ("still",), "start": 0, "end": 2} | changes
    with pytest.raises(ValueError):
        cache.acquire(**request)
    assert effects.fetches == effects.decodes == []


def test_missing_actual_metadata_is_unavailable_without_fetching_or_faking_parent(tmp_path):
    effects = Effects()
    cache = provider(tmp_path, effects, companions={})
    assert cache.acquire("video", parent_ids=("still",), start=0, end=2) is None
    assert effects.fetches == effects.decodes == []
    assert list(cache.unavailable().values()) == ["missing_captured_video_metadata"]
    parents, _ = source()
    with pytest.raises(ValueError, match="real video"):
        provider(
            tmp_path,
            effects,
            companions={"video": parents["still"].model_copy(update={"id": "video"})},
        )


@pytest.mark.parametrize("phase", ["fetch", "decode"])
def test_failures_are_attempt_local_and_never_become_a_shared_negative_cache(tmp_path, phase):
    effects = Effects(payload=None if phase == "fetch" else b"playback")
    if phase == "decode":
        effects.extract = lambda *_a, **_k: None
    cold = provider(tmp_path, effects)
    for _ in range(2):
        assert cold.acquire("video", parent_ids=("still",), start=0, end=2) is None
    assert cold.metrics()["fetch_attempts"] == 1
    assert cold.metrics()["frame_decodes"] == (1 if phase == "decode" else 0)
    assert not list((tmp_path / "frames").glob("*.json"))
    later = Effects(payload=b"playback")
    assert (
        provider(tmp_path, later).acquire("video", parent_ids=("still",), start=0, end=2)
        is not None
    )
    assert len(later.fetches) == (1 if phase == "fetch" else 0)
    assert len(later.decodes) == 1


@pytest.mark.parametrize(
    "change", ["video-metadata", "parent-metadata", "extractor", "width", "payload-kind"]
)
def test_exact_material_or_producer_changes_cannot_reuse_the_wrong_binding(tmp_path, change):
    effects = Effects()
    first = provider(tmp_path, effects).acquire("video", parent_ids=("still",), start=0, end=2)
    parents, videos = source()
    kwargs = {}
    if change == "video-metadata":
        videos["video"].checksum = "changed-source-checksum"
    elif change == "parent-metadata":
        parents["still"].is_favorite = True
    elif change == "extractor":
        kwargs["extractor_version"] = "next-extractor"
    elif change == "width":
        kwargs["width"] = 400
    else:
        kwargs["payload_kind"] = "original"
    second = provider(tmp_path, effects, assets=parents, companions=videos, **kwargs).acquire(
        "video", parent_ids=("still",), start=0, end=2
    )
    assert first is not None and second is not None and first[0].key != second[0].key
    assert len(effects.fetches) == (2 if change in {"video-metadata", "payload-kind"} else 1)
    assert len(effects.decodes) == (1 if change == "parent-metadata" else 2)


@pytest.mark.parametrize("blob,extension", [("playback", "mp4"), ("frames", "jpg")])
def test_corrupt_cached_bytes_are_rejected_without_refetch_or_decode(tmp_path, blob, extension):
    effects = Effects()
    provider(tmp_path, effects).acquire("video", parent_ids=("still",), start=0, end=2)
    next((tmp_path / blob).glob(f"*.{extension}")).write_bytes(b"corrupted")
    before = (len(effects.fetches), len(effects.decodes))
    with pytest.raises(ValueError, match="provenance"):
        provider(tmp_path, effects).acquire("video", parent_ids=("still",), start=0, end=2)
    assert (len(effects.fetches), len(effects.decodes)) == before


def test_transcoded_and_original_playback_have_explicit_persisted_provenance(tmp_path):
    effects = Effects()
    for kind in ("transcoded-playback", "original"):
        result = provider(tmp_path, effects, payload_kind=kind).acquire(
            "video", parent_ids=("still",), start=0, end=2
        )
        assert result is not None and kind in result[0].extractor_version
    records = [json.loads(path.read_text()) for path in (tmp_path / "playback").glob("*.json")]
    assert {row["identity"]["payload_kind"] for row in records} == {
        "transcoded-playback",
        "original",
    }
    assert all(row["identity"]["metadata"]["id"] == "video" for row in records)


def test_remembered_motion_playback_avoids_another_fetch_and_keeps_download_counters_separate(
    tmp_path,
):
    effects = Effects()
    effects.fetch = lambda _: pytest.fail("ordinary motion already fetched this playback")
    cache = provider(tmp_path, effects)
    payload = b"actual successful ordinary playback"
    assert cache.remember_playback("video", payload)
    result = cache.acquire("video", parent_ids=("still",), start=0, end=2)
    assert result is not None
    assert result[0].payload_sha256 == hashlib.sha256(payload).hexdigest()
    assert effects.decodes == [(payload, 1, 800)]
    assert cache.metrics()["remembered_playbacks"] == cache.metrics()["playback_memo_hits"] == 1
    assert cache.metrics()["fetch_attempts"] == cache.metrics()["new_playback_downloads"] == 0
    assert cache.metrics()["download_bytes"] == 0
    record = json.loads(next((tmp_path / "playback").glob("*.json")).read_text())
    assert record["identity"]["metadata"] == source()[1]["video"].model_dump(mode="json")
    assert record["identity"]["payload_kind"] == "transcoded-playback"


def test_remember_playback_cannot_invent_metadata_or_mislabel_original_bytes(tmp_path):
    effects = Effects()
    missing = provider(tmp_path / "missing", effects, companions={})
    original = provider(tmp_path / "original", effects, payload_kind="original")
    assert not missing.remember_playback("video", b"playback")
    assert not original.remember_playback("video", b"playback")
    assert not provider(tmp_path / "unknown", effects).remember_playback("unknown", b"playback")
    assert not provider(tmp_path / "empty", effects).remember_playback("video", b"")
    assert not list(tmp_path.rglob("*"))
    assert effects.fetches == effects.decodes == []


def test_a_new_successful_payload_has_new_frame_identity_without_erasing_old_pixels(tmp_path):
    effects = Effects()
    cache = provider(tmp_path, effects)
    assert cache.remember_playback("video", b"first")
    first = cache.acquire("video", parent_ids=("still",), start=0, end=2)
    assert cache.remember_playback("video", b"replacement")
    second = cache.acquire("video", parent_ids=("still",), start=0, end=2)
    assert first is not None and second is not None
    assert first[0].payload_sha256 != second[0].payload_sha256
    assert first[0].key != second[0].key
    assert len(list((tmp_path / "playback").glob("*.mp4"))) == 2
    assert len(list((tmp_path / "frames").glob("*.json"))) == 2
    assert [row[0] for row in effects.decodes] == [b"first", b"replacement"]
    assert effects.fetches == []


def test_payload_modified_between_intervals_is_not_decoded_under_its_old_digest(tmp_path):
    effects = Effects()
    cache = provider(tmp_path, effects)
    assert cache.acquire("video", parent_ids=("still",), start=0, end=2) is not None
    next((tmp_path / "playback").glob("*.mp4")).write_bytes(b"changed during attempt")
    with pytest.raises(ValueError, match="provenance"):
        cache.acquire("video", parent_ids=("still",), start=2, end=4)
    assert len(effects.fetches) == len(effects.decodes) == 1


def test_acquired_sample_uses_actual_observation_and_adapter_with_exact_blocked_warm(
    tmp_path, monkeypatch
):
    from immich_memories.analysis.editorial_sampled_pair_confirmation import (
        CachedSampledPairConfirmer,
    )
    from immich_memories.analysis.selection_trace import Trace
    from tests.test_editorial_picture_facts import config, fake_transport
    from tests.test_editorial_picture_facts import provider as reader

    effects = Effects()
    result = provider(tmp_path / "attached", effects).acquire(
        "video", parent_ids=("still",), start=1, end=3
    )
    assert result is not None
    sample, frame, video = result
    calls = fake_transport(monkeypatch)
    observed = reader(tmp_path, read=lambda _: pytest.fail("no parent/poster fetch"))
    try:
        facts = observed.observe_sample(sample, frame)
    finally:
        observed.close()

    parents, _ = source()

    def adapter():
        return CachedSampledPairConfirmer(
            assets=parents,
            allowed_ids=parents.keys(),
            llm_config=config(),
            cache_path=tmp_path / "facts.sqlite",
            image_dir=tmp_path / "picture-facts-images",
            trace=Trace(),
            sheet_dir=tmp_path / "pair-sheets",
        )

    cold = adapter()
    try:
        cold.bind_sample(sample, video)
        hashes = cold.preview_hashes([sample.key], {sample.key: facts})
        assert set(hashes) == {sample.key}
        assert cold.preview_hashes(["video"], {"video": facts}) == {}
    finally:
        cold.close()
    assert (
        len(calls) == 1
    )  # Only the controlled own-frame observation; no pair/model inference here.

    async def no_model(*_args, **_kwargs):
        pytest.fail("warm observation must not call a model")

    monkeypatch.setattr("immich_memories.analysis.editorial_gateway.query_llm", no_model)
    blocked = Effects()
    blocked.fetch = lambda _: pytest.fail("warm playback fetch")
    blocked.extract = lambda *_a, **_k: pytest.fail("warm video decode")
    warm_sample = provider(tmp_path / "attached", blocked).acquire(
        "video", parent_ids=("still",), start=1, end=3
    )
    assert warm_sample == result
    warm_reader = reader(tmp_path, read=lambda _: pytest.fail("warm poster fetch"))
    warm_adapter = adapter()
    try:
        assert warm_reader.observe_sample(sample, frame) == facts
        warm_adapter.bind_sample(sample, video)
        assert warm_adapter.preview_hashes([sample.key], {sample.key: facts}) == hashes
        assert warm_reader.metrics()["inference_calls"] == 0
        assert warm_adapter.metrics()["preview_hashes_computed"] == 0
    finally:
        warm_reader.close()
        warm_adapter.close()
