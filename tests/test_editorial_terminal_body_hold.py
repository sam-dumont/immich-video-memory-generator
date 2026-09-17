"""A bound positive may stop a rejected unit; surviving material still needs all facts."""

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from immich_memories.analysis import editorial_shareability as share
from immich_memories.analysis.editorial_picture_evidence import PictureEvidenceOverlay
from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
from immich_memories.analysis.editorial_structure_planner import plan_structure
from immich_memories.processing.live_material import LiveRenderMaterial, LiveSourceEntry
from tests.editorial_story_fixtures import ControlledStoryJudge
from tests.test_editorial_audience_evidence import Judge, activity
from tests.test_editorial_duration_planner_integration import semantic_plan, source
from tests.test_editorial_picture_evidence import annotation
from tests.test_editorial_picture_facts import FACTS, fake_transport, preview, provider
from tests.test_editorial_visual_body_audience import picture_record


def record(asset_id, state="no"):
    result = picture_record(state)
    result["identity"] = hashlib.sha256(asset_id.encode()).hexdigest()
    return result


def overlay_for(records):
    calls = []
    annotations = {key: annotation(key) for key in records}

    def observe(asset_id):
        calls.append(asset_id)
        return records[asset_id]

    return PictureEvidenceOverlay(
        annotations, {key: row.text for key, row in annotations.items()}, observe
    ), calls


def test_stop_conserves_actual_order_witness_membership_and_absent_tail_then_later_demands_it():
    overlay, calls = overlay_for(
        {"z": record("z"), "a": record("a", "yes"), "tail": record("tail")}
    )
    unit = {"asset_id": "z", "members": ["a", "z", "tail"], "video_ids": ["motion"]}
    witness = overlay.enrich(unit, stop_on_body_yes=True)
    assert witness == "a" and calls == ["z", "a"]
    assert "tail" not in overlay.records
    result = share.terminal_body_hold(unit, overlay.records, witness)
    assert result["verdict"] == "family_only" and result["activity"] is None
    assert result["finding"] == "nudity_shirtless_or_underwear"
    assert share.allowed(result["verdict"], "family")
    assert not share.allowed(result["verdict"], "sendable")
    assert result["acquisition_stop"]["witness_member"] == "p2"
    assert result["acquisition_stop"]["observed_members"] == ["p1", "p2"]
    assert result["acquisition_stop"]["unobserved_members"] == ["p3"]
    assert set(result["body_observations"]) == {"p1", "p2"}
    assert "missing_members" not in result  # No claimed complete caption or flag assessment.
    assert overlay.enrich(unit, stop_on_body_yes=True) == witness
    assert calls == ["z", "a"]  # A memoized witness still stops before the unread tail.
    assert overlay.enrich({"asset_id": "tail"}, stop_on_body_yes=True) is None
    assert calls == ["z", "a", "tail"]


@pytest.mark.parametrize("state", ["no", "unclear", "invalid", None])
def test_nonpositive_states_never_short_circuit(state):
    overlay, calls = overlay_for({"a": record("a", state), "b": record("b")})
    unit = {"asset_id": "a", "members": ["b"]}
    assert overlay.enrich(unit, stop_on_body_yes=True) is None
    assert calls == ["a", "b"]
    assert share.terminal_body_hold(unit, overlay.records, "a") is None


@pytest.mark.parametrize("change", ["legacy", "producer", "input", "status"])
def test_unbound_yes_never_short_circuits(change):
    first = record("a", "yes")
    if change == "legacy":
        first["producer"]["schema_version"] = "selected-picture-facts-schema-v1"
    elif change == "producer":
        first["producer"]["prompt_sha256"] = "f" * 64
    elif change == "input":
        first["input_sha256"] = "unbound"
    else:
        first["status"] = "unavailable"
    overlay, calls = overlay_for({"a": first, "b": record("b")})
    unit = {"asset_id": "a", "members": ["b"]}
    assert overlay.enrich(unit, stop_on_body_yes=True) is None
    assert calls == ["a", "b"]
    assert share.terminal_body_hold(unit, overlay.records, "a") is None


def test_terminal_identity_binds_all_material_companion_and_witness_inputs():
    unit = {"asset_id": "a", "members": ["b"], "video_ids": ["v"], "kind": "live-motion"}
    records = {"a": record("a", "yes")}
    baseline = share.terminal_body_hold(unit, records, "a")
    key = share.audience_check_key(baseline)
    for changed in [
        dict(unit, members=["c"]),
        dict(unit, video_ids=["w"]),
        dict(unit, kind="live-still"),
    ]:
        assert share.audience_check_key(share.terminal_body_hold(changed, records, "a")) != key
    for field in ["identity", "input_sha256", "image_sha256"]:
        changed = deepcopy(records)
        changed["a"][field] = "f" * 64
        assert share.audience_check_key(share.terminal_body_hold(unit, changed, "a")) != key
    assert share.terminal_body_hold(dict(unit, asset_id="elsewhere"), records, "a") is None
    assert share.terminal_body_hold(unit, {"v": record("v", "yes")}, "v") is None


def test_default_and_captioned_companion_keep_full_enrichment_and_legacy_policy():
    records = {"a": record("a", "yes"), "b": record("b")}
    unit = {"asset_id": "a", "members": ["b"], "video_ids": ["v"]}
    for captured_annotation in (True, False):
        overlay, calls = overlay_for(records)
        if captured_annotation:
            overlay.annotations["v"] = annotation("v", "A clothed person carries furniture.")
        else:
            overlay._lines = {**overlay._lines, "v": "A clothed person carries furniture."}
        assert overlay.enrich(unit, stop_on_body_yes=True) is None
        assert calls == ["a", "b"]
        judge = Judge(activity())
        result = share.check_audience(
            judge,
            share.evidence_for_unit(
                unit, overlay.annotations, {}, overlay._lines, picture_records=overlay.records
            ),
            "legacy",
        )
        assert result["verdict"] == "share" and len(judge.calls) == 1
    overlay, calls = overlay_for(records)
    assert overlay.enrich(unit) is None and calls == ["a", "b"]


def test_all_no_survivor_evidence_and_activity_request_are_exactly_unchanged():
    records = {key: record(key) for key in ("a", "b", "c")}
    results = []
    for stop in (False, True):
        overlay, calls = overlay_for(records)
        unit = {"asset_id": "a", "members": ["b", "c"]}
        assert overlay.enrich(unit, stop_on_body_yes=stop) is None
        assert calls == ["a", "b", "c"]
        evidence = share.evidence_for_unit(
            unit, overlay.annotations, {}, {}, picture_records=overlay.records
        )
        judge = Judge(activity())
        verdict = share.check_audience(judge, evidence, "same")
        results.append((evidence, verdict, judge.calls, unit))
    assert results[0] == results[1]


def test_actual_planner_rejected_burst_stops_surviving_burst_is_complete_and_warm_is_exact(
    tmp_path, monkeypatch
):
    captured = source(tmp_path, seconds=60, pictures=28)
    first = tuple(f"picture-{i:03d}" for i in range(3))
    second = tuple(f"picture-{i:03d}" for i in range(3, 6))
    renderings = {}
    for members in (first, second):
        material = LiveRenderMaterial(
            tuple(
                LiveSourceEntry(
                    key, f"motion-{key}", captured.assets[key].file_created_at.timestamp(), 0, 1
                )
                for key in members
            )
        )
        rendering = SimpleNamespace(
            still_ids=material.still_ids,
            video_ids=material.video_ids,
            trim_points=material.trim_points,
            beats_a_still=False,
            may_play=False,
            duration_seconds=material.duration_seconds,
            material=material,
        )
        renderings.update(dict.fromkeys(members, rendering))
    monkeypatch.setattr(
        "immich_memories.analysis.editorial_structure_material.motion_renderings",
        lambda *_, **__: renderings,
    )
    original_enrich = PictureEvidenceOverlay.enrich

    def run(export, baseline=False, bank=None, observed_bank=None):
        calls = []
        stored = {} if observed_bank is None else observed_bank

        def observe(asset_id):
            calls.append(asset_id)
            if asset_id not in stored:
                assert observed_bank is None, "warm image observation missed its exact record"
                stored[asset_id] = record(asset_id, "yes" if asset_id == first[0] else "no")
            return stored[asset_id]

        judge = ControlledStoryJudge(bank, require_hits=bank is not None)
        if baseline:
            monkeypatch.setattr(
                PictureEvidenceOverlay,
                "enrich",
                lambda self, unit, **_: original_enrich(self, unit),
            )
        else:
            monkeypatch.setattr(PictureEvidenceOverlay, "enrich", original_enrich)
        plan = plan_structure(
            replace(captured, audience=export),
            StructurePlannerPorts(
                judge=judge,
                thumbnail_hash=lambda _: None,
                rank=lambda _query, documents: dict.fromkeys(range(len(documents)), 1.0),
                reranker_identity={"endpoint": "test://local", "model": "controlled"},
                observe_picture=observe,
            ),
        ).plan
        return plan, calls, judge, stored

    baseline, baseline_calls, baseline_judge, _ = run("sendable", baseline=True)
    candidate, calls, judge, records = run("sendable")
    assert baseline["carriers"] == candidate["carriers"]
    assert baseline_judge.bank == judge.bank  # Same actual editorial and audience requests.
    assert set(first).issubset(baseline_calls) and set(first[1:]).isdisjoint(calls)
    assert set(second).issubset(calls)
    assert len(calls) == len(set(calls))
    assert set(first).isdisjoint(
        {member for row in candidate["carriers"] for member in row["members"]}
    )
    assert all(
        set(row["members"]).issubset(candidate["picture_facts"]) for row in candidate["carriers"]
    )
    warm, warm_calls, warm_judge, _ = run("sendable", bank=judge.bank, observed_bank=records)
    assert calls == warm_calls and all(call["cache_hit"] for call in warm_judge.calls)
    assert semantic_plan(candidate) == semantic_plan(warm)
    family, family_calls, _, _ = run("family")
    assert set(first).issubset(family_calls)
    assert first[0] in {row["asset_id"] for row in family["carriers"]}
    assert captured.audience == "family" and all(
        "picture observations" not in line for line in captured.annotations.values()
    )


def test_native_picture_provider_exact_warm_reuses_witness_without_requesting_tail(
    tmp_path, monkeypatch
):
    transport = fake_transport(monkeypatch, raw=json.dumps({**FACTS, "uncovered_person": "yes"}))
    annotations = {key: annotation(key) for key in ("first", "tail")}
    lines = {key: row.text for key, row in annotations.items()}
    unit = {"asset_id": "first", "members": ["tail"]}
    reads = []

    def image_bytes(asset_id):
        reads.append(asset_id)
        return preview((80, 120, 160))

    reader = provider(tmp_path, read=image_bytes)
    try:
        cold = PictureEvidenceOverlay(annotations, lines, reader.observe)
        witness = cold.enrich(dict(unit), stop_on_body_yes=True)
        verdict = share.terminal_body_hold(unit, cold.records, witness)
    finally:
        reader.close()
    assert reads == ["first"] and len(transport) == 1
    assert set(cold.records) == {"first"}

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("warm body observation must not use transport")

    monkeypatch.setattr("immich_memories.analysis.editorial_gateway.query_llm", forbidden)
    warm_reader = provider(tmp_path, read=image_bytes)
    try:
        warm = PictureEvidenceOverlay(annotations, lines, warm_reader.observe)
        assert warm.enrich(dict(unit), stop_on_body_yes=True) == witness
        assert share.terminal_body_hold(unit, warm.records, witness) == verdict
        assert warm_reader.metrics()["inference_calls"] == 0
        assert warm_reader.metrics()["http_attempts"] == 0
    finally:
        warm_reader.close()
    assert reads == ["first", "first"] and set(warm.records) == {"first"}


@pytest.mark.parametrize("state", ["legacy", "invalid"])
def test_known_mixed_material_keeps_full_legacy_assessment_before_later_yes(state):
    first = record("first", "no")
    if state == "legacy":
        first["producer"]["schema_version"] = "selected-picture-facts-schema-v1"
    else:
        first["facts"]["uncovered_person"] = "bad"
    records = {"first": first, "positive": record("positive", "yes"), "tail": record("tail")}
    overlay, calls = overlay_for(records)
    unit = {"asset_id": "first", "members": ["positive", "tail"]}
    assert overlay.enrich(unit, stop_on_body_yes=True) is None
    assert calls == ["first", "positive", "tail"]
    evidence = share.evidence_for_unit(
        unit, overlay.annotations, {}, {}, picture_records=overlay.records
    )
    judge = Judge(activity())
    result = share.check_audience(judge, evidence, "same")
    assert result["finding"] == ("none" if state == "legacy" else "invalid_body_observation")
    assert result["verdict"] == ("share" if state == "legacy" else "family_only")
    assert result["activity"]["finding"] == "none"
    assert [call["stage"] for call in judge.calls] == ["same-activity"]
    assert share.allowed(result["verdict"], "family")
    assert share.allowed(result["verdict"], "sendable") is (state == "legacy")
