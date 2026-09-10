"""Actual bound observation → stored pixels → unchanged pair gateway and reducer."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from immich_memories.analysis.editorial_bound_sample import (
    attached_link_digest,
    source_metadata_digest,
)
from immich_memories.analysis.editorial_sampled_pair_confirmation import CachedSampledPairConfirmer
from immich_memories.analysis.selection_trace import Trace
from immich_memories.api.models import AssetType
from tests.conftest import make_asset
from tests.test_editorial_bound_sample import sample
from tests.test_editorial_final_sampled_duplicates import run, unit
from tests.test_editorial_picture_facts import config, fake_transport, preview, provider
from tests.test_editorial_sampled_pair_confirmation import answer, replies


def setup(tmp_path, monkeypatch):
    fake_transport(monkeypatch)
    frame = preview()
    video = make_asset("attached-video", file_created_at=datetime(2024, 1, 1, tzinfo=UTC))
    parent = make_asset("parent").model_copy(
        update={
            "type": AssetType.IMAGE,
            "live_photo_video_id": video.id,
        }
    )
    bound = replace(
        sample(frame),
        parent_ids=(parent.id,),
        link_sha256=attached_link_digest((parent,), video.id),
        metadata_sha256=source_metadata_digest(video),
    )
    reader = provider(tmp_path, read=lambda _: frame)
    records = {bound.key: reader.observe_sample(bound, frame), "parent": reader.observe("parent")}
    reader.close()

    def adapter():
        return CachedSampledPairConfirmer(
            assets={parent.id: parent},
            allowed_ids={parent.id},
            llm_config=config(),
            cache_path=tmp_path / "facts.sqlite",
            image_dir=tmp_path / "picture-facts-images",
            trace=Trace(),
            sheet_dir=tmp_path / "pairs",
        )

    return bound, video, records, adapter


def test_real_companion_observation_to_pair_and_exact_warm(tmp_path, monkeypatch):
    bound, video, records, factory = setup(tmp_path, monkeypatch)
    calls = replies(monkeypatch, [answer(), answer()])
    adapter = factory()
    try:
        adapter.bind_sample(bound, video)
        assert set(adapter.preview_hashes(tuple(records), records)) == set(records)
        cold, audit = adapter(((bound.key, "parent"),), records)
        assert cold[0].same and not cold[0].warning
        assert len(calls) == 2 and audit["actual_http_attempts"] == 2
    finally:
        adapter.close()
    replies(monkeypatch, [])
    adapter = factory()
    try:
        adapter.bind_sample(bound, video)
        warm, audit = adapter(((bound.key, "parent"),), records)
        assert warm == cold and audit["cache_hits"] == 2
        assert audit["actual_http_attempts"] == 0
    finally:
        adapter.close()


@pytest.mark.parametrize("mutation", ["unlinked", "outside", "wrong-type", "changed-metadata"])
def test_registration_rejects_unauthorized_or_changed_companion(tmp_path, monkeypatch, mutation):
    bound, video, _records, factory = setup(tmp_path, monkeypatch)
    if mutation == "unlinked":
        bound = replace(bound, source_id="unlinked-video")
        video = video.model_copy(update={"id": bound.source_id})
    elif mutation == "outside":
        bound = replace(bound, parent_ids=("outside",))
    elif mutation == "wrong-type":
        video = video.model_copy(update={"type": AssetType.IMAGE})
    else:
        video = video.model_copy(update={"is_favorite": not video.is_favorite})
    adapter = factory()
    try:
        with pytest.raises(ValueError):
            adapter.bind_sample(bound, video)
    finally:
        adapter.close()


def test_same_video_wrong_interval_cannot_relabel_an_existing_observation(tmp_path, monkeypatch):
    bound, video, records, factory = setup(tmp_path, monkeypatch)
    changed = replace(bound, start=1.1)
    forged = {**records[bound.key], "sample_id": changed.key, "sample_binding": changed.as_dict()}
    calls = replies(monkeypatch, [])
    adapter = factory()
    try:
        adapter.bind_sample(changed, video)
        altered = {"parent": records["parent"], changed.key: forged}
        assert changed.key not in adapter.preview_hashes(tuple(altered), altered)
        result, audit = adapter(((changed.key, "parent"),), altered)
        assert not result[0].same and result[0].warning
        assert audit["routed_pairs"] == 0 and not calls
    finally:
        adapter.close()


def test_two_intervals_of_one_video_need_real_pair_proof_and_preserve_source_fields():
    units = [
        unit("a", kind="live-motion", video_ids=["video"]),
        unit("b", kind="live-motion", video_ids=["video"]),
    ]
    members = {"a": ("sample-a",), "b": ("sample-b",)}
    records = {key: {"status": "available"} for key in ("sample-a", "sample-b")}
    hashes = dict.fromkeys(records, "0000000000000000")
    kept, audit, calls = run(
        units, records=records, hashes=hashes, bound_sample_members=members, outcomes={}
    )
    assert kept == units and calls == [("sample-a", "sample-b")]
    assert not audit["removals"]
    kept, audit, calls = run(units, records=records, hashes=hashes, bound_sample_members=members)
    assert kept == [units[0]] and kept[0] is units[0]
    assert audit["removals"][0]["direct_member_proof"][0]["remove_member"] == "sample-b"


def test_empty_bound_material_is_an_explicit_gap_not_a_vacuous_duplicate_proof():
    units = [unit("a"), unit("b", kind="live-motion", video_ids=["video"])]
    kept, audit, calls = run(units, bound_sample_members={"b": ()})
    assert kept == units and not calls and audit["incomplete"]
    assert audit["unavailable"]["b"][0]["reason"] == "live_motion_has_no_bound_displayed_samples"
