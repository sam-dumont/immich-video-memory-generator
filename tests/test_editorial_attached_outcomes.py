"""Exact attempt replay conserves gaps without turning them into permanent negatives."""

from __future__ import annotations

import copy
import hashlib
import json
import socket

import httpx
import pytest

from immich_memories.analysis.editorial_attached_outcomes import (
    AttachedAttemptOutcomes,
    AttachedOutcomeReplay,
)
from immich_memories.processing.live_material import LiveRenderMaterial, LiveSourceEntry
from tests.test_editorial_attached_samples import Effects, provider, source


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("attached outcome tests must not reach a network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def request(**changes):
    return {"video_id": "video", "parent_ids": ["still"], "start": 0, "end": 2} | changes


def materials():
    parent = source()[0]["still"]
    canonical = LiveRenderMaterial(
        (LiveSourceEntry("still", "video", parent.file_created_at.timestamp(), 0, 2),)
    )
    return [{"primary": "still", "canonical": canonical.as_dict(), "start": 0, "end": 2}]


def attempt(tmp_path, name, effects, *, replay=None, scope=None, **kwargs):
    journal = AttachedAttemptOutcomes(
        output=tmp_path / name,
        scope=scope if scope is not None else {"source": "captured-source", "config": "current"},
        replay=replay,
    )
    return provider(tmp_path / "cache", effects, outcomes=journal, **kwargs), journal


def closed_attempt(tmp_path, effects, **kwargs):
    cache, journal = attempt(tmp_path, "first", effects, **kwargs)
    cache.begin_material(materials(), [request()])
    result = cache.acquire(**request())
    cache.finish_material()
    return cache, journal, result, AttachedOutcomeReplay.from_output(tmp_path / "first")


def forbidden_effects():
    effects = Effects()
    effects.fetch = lambda *_a, **_k: pytest.fail("exact replay must not fetch")
    effects.extract = lambda *_a, **_k: pytest.fail("exact replay must not decode")
    return effects


@pytest.mark.parametrize("phase", ["fetch", "decode"])
def test_old_failure_replays_before_newer_positive_cache_without_poisoning_fresh_attempts(
    tmp_path, phase
):
    failed = Effects(payload=None if phase == "fetch" else b"playback")
    if phase == "decode":
        failed.extract = lambda *_a, **_k: None
    cold, journal, result, reference = closed_attempt(tmp_path, failed)
    assert result is None
    original = reference.path.read_bytes()
    old_outcome = next(iter(reference.read()["outcomes"].values()))
    assert old_outcome["phase"] == phase
    assert old_outcome["error_type"] is old_outcome["http_status"] is None
    assert (old_outcome["payload_sha256"] is not None) == (phase == "decode")

    fresh_effects = Effects(payload=b"playback")
    fresh, _ = attempt(tmp_path, "fresh", fresh_effects)
    fresh.begin_material(materials(), [request()])
    assert fresh.acquire(**request()) is not None
    fresh.finish_material()
    assert len(fresh_effects.fetches) == (1 if phase == "fetch" else 0)
    assert len(fresh_effects.decodes) == 1

    replay, replay_journal = attempt(tmp_path, "replay", forbidden_effects(), replay=reference)
    replay.begin_material(materials(), [request()])
    assert replay.acquire(**request()) is None
    replay.finish_material()
    assert replay.unavailable() == cold.unavailable()
    assert replay_journal.record == journal.record
    assert replay_journal.path != reference.path
    assert reference.path.read_bytes() == original
    assert replay.metrics()["outcome_replay_hits"] == 1
    for field in ("fetch_attempts", "frame_decodes", "download_bytes", "decode_seconds"):
        assert replay.metrics()[field] == 0


def test_positive_replay_uses_recorded_payload_even_after_current_playback_pointer_changes(
    tmp_path,
):
    _, journal, result, reference = closed_attempt(tmp_path, Effects(payload=b"first payload"))
    assert result is not None
    fresh, _ = attempt(tmp_path, "fresh", Effects())
    assert fresh.remember_playback("video", b"replacement payload")
    fresh.begin_material(materials(), [request()])
    newer = fresh.acquire(**request())
    fresh.finish_material()
    assert newer is not None and newer[0].payload_sha256 != result[0].payload_sha256

    replay, replay_journal = attempt(tmp_path, "replay", forbidden_effects(), replay=reference)
    replay.begin_material(materials(), [request()])
    assert replay.acquire(**request()) == result
    replay.finish_material()
    assert replay_journal.record == journal.record
    assert replay.metrics()["playback_cache_hits"] == replay.metrics()["frame_cache_hits"] == 1
    assert replay.metrics()["fetch_attempts"] == replay.metrics()["frame_decodes"] == 0


@pytest.mark.parametrize(
    "change",
    [
        "video-metadata",
        "parent-metadata",
        "parent-ids",
        "interval",
        "extractor",
        "width",
        "payload-kind",
        "canonical-material",
        "extra-material",
        "missing-request",
        "extra-request",
    ],
)
def test_valid_journal_hash_cannot_substitute_for_exact_material_and_request_binding(
    tmp_path, change
):
    _, _, _, reference = closed_attempt(tmp_path, Effects(payload=None))
    parents, videos = source()
    kwargs = {}
    declared, material = [request()], materials()
    if change == "video-metadata":
        videos["video"].checksum = "new captured source checksum"
    elif change == "parent-metadata":
        parents["still"].is_favorite = True
    elif change == "parent-ids":
        declared = [request(parent_ids=["alias"])]
    elif change == "interval":
        declared = [request(start=1, end=3)]
    elif change == "extractor":
        kwargs["extractor_version"] = "changed-extractor"
    elif change == "width":
        kwargs["width"] = 400
    elif change == "payload-kind":
        kwargs["payload_kind"] = "original"
    elif change == "canonical-material":
        material[0]["canonical"]["source_entries"][0]["end"] = 3
    elif change == "extra-material":
        material = [{"primary": "another selected carrier"}, *material]
    elif change == "missing-request":
        declared = []
    else:
        declared.append(request(start=2, end=4))
    replay, _ = attempt(
        tmp_path,
        "replay",
        forbidden_effects(),
        replay=reference,
        assets=parents,
        companions=videos,
        **kwargs,
    )
    with pytest.raises(ValueError, match="material or complete request set changed"):
        replay.begin_material(material, declared)
    assert replay.metrics()["requested_samples"] == 0


def test_source_config_scope_is_checked_before_begin_or_effects(tmp_path):
    _, _, _, reference = closed_attempt(tmp_path, Effects(payload=None))
    with pytest.raises(ValueError, match="source/config scope mismatch"):
        attempt(
            tmp_path, "replay", forbidden_effects(), replay=reference, scope={"config": "other"}
        )


def test_reordering_actual_final_material_is_not_an_exact_replay_even_with_identical_requests(
    tmp_path,
):
    material = materials()
    alias = source()[0]["alias"]
    canonical = LiveRenderMaterial(
        (LiveSourceEntry("alias", "video", alias.file_created_at.timestamp(), 2, 4),)
    )
    material.append({"primary": "alias", "canonical": canonical.as_dict(), "start": 0, "end": 2})
    declared = [request(), request(parent_ids=["alias"], start=2, end=4)]
    cold, _ = attempt(tmp_path, "first", Effects(payload=None))
    cold.begin_material(material, declared)
    for row in declared:
        assert cold.acquire(**row) is None
    cold.finish_material()
    replay, _ = attempt(
        tmp_path,
        "replay",
        forbidden_effects(),
        replay=AttachedOutcomeReplay.from_output(tmp_path / "first"),
    )
    with pytest.raises(ValueError, match="material or complete request set changed"):
        replay.begin_material(list(reversed(material)), declared)
    assert replay.metrics()["requested_samples"] == 0


def test_all_demands_are_validated_before_effects_and_complete_outcomes_are_required(tmp_path):
    effects = Effects()
    cache, _ = attempt(tmp_path, "invalid", effects)
    with pytest.raises(ValueError):
        cache.begin_material(materials(), [request(), request(parent_ids=["unknown"])])
    assert effects.fetches == effects.decodes == []

    cache, _ = attempt(tmp_path, "partial", effects)
    declared = [request(), request(start=2, end=4)]
    cache.begin_material(materials(), declared)
    with pytest.raises(ValueError, match="outside the open declared material"):
        cache.acquire(**request(start=4, end=6))
    assert effects.fetches == effects.decodes == []
    assert cache.acquire(**declared[0]) is not None
    with pytest.raises(ValueError, match="missing demanded outcomes"):
        cache.finish_material()
    with pytest.raises(ValueError, match="complete exact attempt"):
        AttachedOutcomeReplay.from_output(tmp_path / "partial")
    assert cache.acquire(**declared[1]) is not None
    cache.finish_material()
    assert AttachedOutcomeReplay.from_output(tmp_path / "partial").read()["complete"]
    with pytest.raises(ValueError, match="outside the open declared material"):
        cache.acquire(**declared[0])


def test_empty_refusal_or_no_live_material_has_a_public_complete_replay_contract(tmp_path):
    journal = AttachedAttemptOutcomes(output=tmp_path / "empty", scope={"case": "refusal"})
    journal.begin({"materials": []}, {})
    journal.finish()
    reference = AttachedOutcomeReplay.from_output(tmp_path / "empty")
    replay = AttachedAttemptOutcomes(
        output=tmp_path / "warm", scope={"case": "refusal"}, replay=reference
    )
    replay.begin({"materials": []}, {})
    replay.finish()
    assert replay.record == journal.record
    assert reference.read()["requests"] == reference.read()["outcomes"] == {}


def test_provider_empty_material_and_duplicate_demands_seal_without_extra_effects(tmp_path):
    empty, _ = attempt(tmp_path, "empty", forbidden_effects())
    empty.begin_material([], [])
    empty.finish_material()
    assert AttachedOutcomeReplay.from_output(tmp_path / "empty").read()["complete"]
    effects = Effects()
    cache, journal = attempt(tmp_path, "duplicates", effects)
    cache.begin_material(materials(), [request(), request()])
    assert cache.acquire(**request()) == cache.acquire(**request())
    cache.finish_material()
    assert len(journal.record["requests"]) == len(journal.record["outcomes"]) == 1
    assert len(effects.fetches) == len(effects.decodes) == 1


def test_missing_metadata_remains_explicit_and_new_metadata_cannot_replay_old_scope(tmp_path):
    _, _, result, reference = closed_attempt(tmp_path, forbidden_effects(), companions={})
    assert result is None
    outcome = next(iter(reference.read()["outcomes"].values()))
    assert outcome["phase"] == "metadata" and outcome["payload_sha256"] is None
    replay, _ = attempt(tmp_path, "replay", forbidden_effects(), replay=reference, companions={})
    replay.begin_material(materials(), [request()])
    assert replay.acquire(**request()) is None
    replay.finish_material()
    changed, _ = attempt(tmp_path, "changed", forbidden_effects(), replay=reference)
    with pytest.raises(ValueError, match="material or complete request set changed"):
        changed.begin_material(materials(), [request()])
    fresh, _ = attempt(tmp_path, "fresh", Effects())
    fresh.begin_material(materials(), [request()])
    assert fresh.acquire(**request()) is not None
    fresh.finish_material()


@pytest.mark.parametrize("damage", ["payload", "frame", "frame-manifest", "missing-frame"])
def test_broken_positive_replay_fails_without_refetch_decode_or_synthetic_negative(
    tmp_path, damage
):
    _, _, result, reference = closed_attempt(tmp_path, Effects())
    assert result is not None
    sample = result[0]
    cache_dir = tmp_path / "cache"
    if damage == "payload":
        (cache_dir / "playback" / f"{sample.payload_sha256}.mp4").write_bytes(b"corrupt")
    elif damage == "frame":
        (cache_dir / "frames" / f"{sample.frame_sha256}.jpg").write_bytes(b"corrupt")
    elif damage == "frame-manifest":
        next((cache_dir / "frames").glob("*.json")).write_text("{}")
    else:
        (cache_dir / "frames" / f"{sample.frame_sha256}.jpg").unlink()
    replay, journal = attempt(tmp_path, "replay", forbidden_effects(), replay=reference)
    replay.begin_material(materials(), [request()])
    with pytest.raises((OSError, ValueError)):
        replay.acquire(**request())
    assert replay.unavailable() == journal.record["outcomes"] == {}
    assert replay.metrics()["fetch_attempts"] == replay.metrics()["frame_decodes"] == 0
    with pytest.raises(ValueError, match="missing demanded outcomes"):
        replay.finish_material()


@pytest.mark.parametrize(
    "damage", ["missing-outcome", "unknown-reason", "wrong-phase", "wrong-midpoint", "wrong-source"]
)
def test_rehashed_invalid_artifact_is_rejected_not_treated_as_an_unavailable_verdict(
    tmp_path, damage
):
    _, _, _, reference = closed_attempt(
        tmp_path,
        Effects(
            payload=None if damage != "wrong-midpoint" and damage != "wrong-source" else b"payload"
        ),
    )
    record = reference.read()
    key = next(iter(record["outcomes"]))
    if damage == "missing-outcome":
        record["outcomes"] = {}
    elif damage == "unknown-reason":
        record["outcomes"][key]["reason"] = "family_only"
    elif damage == "wrong-phase":
        record["outcomes"][key]["phase"] = "metadata"
        record["outcomes"][key]["reason"] = "missing_captured_video_metadata"
    elif damage == "wrong-midpoint":
        record["outcomes"][key]["sample"]["timestamp"] = 0.5
    else:
        record["outcomes"][key]["sample"]["source_id"] = "other-video"
    payload = json.dumps(record).encode()
    path = tmp_path / "altered.private.json"
    path.write_bytes(payload)
    with pytest.raises(ValueError):
        AttachedOutcomeReplay(path, hashlib.sha256(payload).hexdigest()).read()


def test_journal_integrity_and_actual_failure_status_without_secret_exception_text(tmp_path):
    effects = Effects()

    def failed_fetch(_):
        raise httpx.HTTPStatusError(
            "private-token-must-not-be-recorded",
            request=httpx.Request("GET", "https://invalid.test"),
            response=httpx.Response(503),
        )

    effects.fetch = failed_fetch
    _, _, result, reference = closed_attempt(tmp_path, effects)
    assert result is None
    outcome = next(iter(reference.read()["outcomes"].values()))
    assert outcome["error_type"] == "HTTPStatusError" and outcome["http_status"] == 503
    assert "private-token" not in reference.path.read_text()
    reference.path.write_bytes(reference.path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="artifact hash mismatch"):
        reference.read()


def test_journal_copies_material_and_outcomes_and_rejects_changed_repeat(tmp_path):
    _, _, _, reference = closed_attempt(tmp_path, Effects(payload=None))
    prior = reference.read()
    journal = AttachedAttemptOutcomes(output=tmp_path / "copy", scope=prior["scope"])
    material, requests = copy.deepcopy(prior["material"]), copy.deepcopy(prior["requests"])
    journal.begin(material, requests)
    material["materials"] = []
    requests.clear()
    key, outcome = next(iter(prior["outcomes"].items()))
    journal.observed(key, outcome)
    outcome["http_status"] = 503
    with pytest.raises(ValueError, match="changed within one attempt"):
        journal.observed(key, outcome)
    journal.finish()
    assert journal.record["material"] == reference.read()["material"]
    assert journal.record["outcomes"][key]["http_status"] is None
