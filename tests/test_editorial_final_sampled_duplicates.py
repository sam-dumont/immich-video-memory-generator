"""Direct displayed-member proof, bounded work and unchanged final source records."""

from copy import deepcopy

import pytest

from immich_memories.analysis.editorial_final_sampled_duplicates import (
    displayed_sample_members,
    reduce_final_sampled_duplicates,
)


def unit(asset_id, **changes):
    return {
        "asset_id": asset_id,
        "kind": "still",
        "members": [asset_id],
        "video_ids": [],
        "favourite": False,
        "taken": "2020-01-01T12:00:00+00:00",
        "event": "event-one",
        "seconds": 4.0,
    } | changes


def inputs(units):
    members = {member for row in units for member in displayed_sample_members(row)}
    return (
        {member: {"status": "available"} for member in members},
        dict.fromkeys(members, "0000000000000000"),
    )


def run(units, *, outcomes=None, hashes=None, records=None, **options):
    actual_records, actual_hashes = inputs(units)
    calls = []

    def confirm(left, right, _distance=None):
        calls.append((left, right))
        value = True if outcomes is None else outcomes.get(frozenset((left, right)), False)
        return {"same": value, "source_pair": [left, right], "evidence": "controlled pixels"}

    kept, audit = reduce_final_sampled_duplicates(
        units,
        picture_records=actual_records if records is None else records,
        preview_hashes=actual_hashes if hashes is None else hashes,
        confirm_relation=confirm,
        **options,
    )
    return kept, audit, calls


def test_cross_date_event_and_format_discovery_preserves_original_survivor_fields_and_order():
    original = [
        unit("video", kind="video", event="later-event", taken="2025-08-01T12:00:00+00:00"),
        unit("distinct", taken="2023-03-01T12:00:00+00:00"),
        unit("favorite", favourite=True, taken="2011-01-01T12:00:00+00:00"),
    ]
    before = deepcopy(original)
    outcomes = {frozenset(("video", "favorite")): True}
    kept, audit, calls = run(original, outcomes=outcomes)
    assert [row["asset_id"] for row in kept] == ["distinct", "favorite"]
    assert all(row is original[index] for row, index in zip(kept, [1, 2], strict=True))
    assert original == before
    assert audit["removals"][0]["keeper"] == "favorite"
    assert audit["removals"][0]["asset_id"] == "video"
    assert ("favorite", "video") in calls and not audit["incomplete"]


@pytest.mark.parametrize("kind", ["still", "photo", "live-still", "video"])
def test_unused_source_alternatives_and_video_fields_do_not_block_single_sample_display(kind):
    original = [
        unit("keep"),
        unit(
            "remove",
            kind=kind,
            members=["remove", "unused-one", "unused-two", "unused-three"],
            video_ids=["unused-video"],
        ),
    ]
    assert displayed_sample_members(original[1]) == ("remove",)
    kept, audit, calls = run(original)
    assert [row["asset_id"] for row in kept] == ["keep"]
    assert audit["displayed_members"]["remove"] == ("remove",)
    assert calls == [("keep", "remove")]
    assert "unused" not in repr(audit)


def test_live_motion_requires_all_displayed_video_material_to_be_observed():
    original = [unit("keep"), unit("remove", kind="live-motion", video_ids=["clip"])]
    records, hashes = inputs(original)
    records.pop("clip")
    kept, audit, calls = run(original, records=records, hashes=hashes)
    assert kept == original and calls == []
    assert audit["incomplete"] and "remove" in audit["unavailable"]
    assert audit["displayed_members"]["remove"] == ("clip",)


@pytest.mark.parametrize("missing_motion_declaration", [True, False])
def test_incomplete_or_unsupported_render_mode_cannot_be_removed(missing_motion_declaration):
    kind = "live-motion" if missing_motion_declaration else "future-unknown-kind"
    original = [unit("keep"), unit("remove", kind=kind)]
    kept, audit, calls = run(original)
    assert kept == original and not calls
    assert audit["incomplete"] and audit["unavailable"]["remove"]


def test_live_motion_removal_needs_direct_confirmation_for_every_displayed_companion():
    original = [
        unit("keep"),
        unit("remove", kind="live-motion", video_ids=["clip-one", "clip-two"]),
    ]
    kept, audit, calls = run(original)
    assert [row["asset_id"] for row in kept] == ["keep"]
    proof = audit["removals"][0]["direct_member_proof"]
    assert {row["remove_member"] for row in proof} == {"clip-one", "clip-two"}
    assert {row["keeper_member"] for row in proof} == {"keep"}
    assert len(calls) == 2


def test_one_negative_displayed_member_keeps_the_complete_motion_unit():
    original = [
        unit("keep"),
        unit("remove", kind="live-motion", video_ids=["clip-one", "clip-two"]),
    ]
    kept, audit, calls = run(original, outcomes={frozenset(("keep", "clip-one")): True})
    assert kept == original and not audit["removals"]
    assert len(calls) == 2
    assert audit["nominations"][0]["status"] == "sampled_different"


def test_hash_nomination_for_one_member_cannot_hide_an_unmatched_displayed_member():
    original = [
        unit("keep"),
        unit("remove", kind="live-motion", video_ids=["sample", "extra"]),
    ]
    _records, hashes = inputs(original)
    hashes["extra"] = "ffffffffffffffff"
    kept, audit, calls = run(original, hashes=hashes)
    assert kept == original and not audit["removals"]
    assert audit["nominations"][0]["status"] == "unmatched_displayed_member"
    assert calls == [("keep", "sample")]


def test_direct_relation_is_not_transitive_and_keepers_never_get_removed_later():
    original = [unit("a"), unit("b"), unit("c")]
    positives = {frozenset(("a", "b")): True, frozenset(("b", "c")): True}
    kept, audit, calls = run(original, outcomes=positives)
    assert [row["asset_id"] for row in kept] == ["a", "c"]
    assert calls == [("a", "b"), ("a", "c")]
    assert audit["removals"][0]["asset_id"] == "b"
    assert audit["removals"][0]["keeper"] == "a"
    assert all(row["keeper"] in {unit["asset_id"] for unit in kept} for row in audit["removals"])


def test_actual_favorite_precedes_objective_quality_without_changing_metadata():
    original = [unit("high-quality"), unit("actual-favorite", favourite=True)]
    before = deepcopy(original)
    kept, audit, _calls = run(original, objective_quality={"high-quality": 1, "actual-favorite": 0})
    assert [row["asset_id"] for row in kept] == ["actual-favorite"]
    assert original == before and not audit["protected_conflicts"]


def test_existing_quality_then_capture_time_choose_keeper_without_model_preference():
    original = [
        unit("early", taken="2020-01-01T12:00:00+00:00"),
        unit("better", taken="2024-01-01T12:00:00+00:00"),
    ]
    kept, _audit, _calls = run(original, objective_quality={"early": 0.2, "better": 0.8})
    assert [row["asset_id"] for row in kept] == ["better"]
    kept, _audit, _calls = run(original, objective_quality={"early": 0.8, "better": 0.8})
    assert [row["asset_id"] for row in kept] == ["early"]


def test_protected_exact_members_remain_and_conflicting_protected_duplicates_are_reported():
    original = [unit("a", kind="live-motion", video_ids=["protected-member"]), unit("b")]
    kept, audit, _calls = run(original, protected_asset_ids=["protected-member", "b"])
    assert kept == original and not audit["removals"]
    assert audit["incomplete"]
    assert audit["protected_conflicts"] == [
        {"asset_id": "b", "keeper": "a", "reason": "exact_protected_material_retained"}
    ]


def test_protected_nonfavorite_does_not_absorb_actual_favorite():
    original = [unit("protected"), unit("favorite", favourite=True)]
    before = deepcopy(original)
    kept, audit, _calls = run(original, protected_asset_ids=["protected"])
    assert kept == original == before
    assert audit["protected_conflicts"][0]["reason"] == "favorite_not_replaced_by_nonfavorite"
    assert audit["incomplete"]


def test_live_primary_protection_survives_when_only_companion_video_is_displayed():
    original = [unit("keep"), unit("protected", kind="live-motion", video_ids=["clip"])]
    assert displayed_sample_members(original[1]) == ("clip",)
    kept, audit, calls = run(original, protected_asset_ids=["protected"])
    assert [row["asset_id"] for row in kept] == ["protected"]
    assert audit["removals"][0]["keeper"] == "protected"
    assert calls == [("clip", "keep")]


@pytest.mark.parametrize("videos", [None, [], "clip", [""], [None], ["clip", "clip"]])
def test_invalid_live_motion_declaration_is_unavailable_without_falling_back_to_still(videos):
    original = [unit("keep"), unit("remove", kind="live-motion", video_ids=videos)]
    assert displayed_sample_members(original[1]) == ()
    kept, audit, calls = run(original)
    assert kept == original and not calls and audit["incomplete"]
    assert audit["unavailable"]["remove"] == [
        {"reason": "live_motion_has_no_valid_declared_video_material"}
    ]


@pytest.mark.parametrize("kind", ["still", "live-still", "video"])
def test_material_matches_actual_product_projector_for_single_source_modes(kind):
    from immich_memories.analysis.editorial_source_route import project_source_rendering
    from immich_memories.config_loader import Config
    from tests.conftest import make_clip
    from tests.test_editorial_source_route import demand, photo

    primary = make_clip("primary", duration=8) if kind == "video" else photo("primary")
    _, candidates = demand(
        [primary, photo("alternate-one"), photo("alternate-two"), photo("alternate-three")]
    )
    row = unit(
        "primary",
        kind=kind,
        members=["primary", "alternate-one", "alternate-two", "alternate-three"],
        video_ids=["unused-video"],
    )
    rendered = project_source_rendering(
        [row],
        candidates,
        config=Config(),
        include_live_photos=True,
    )
    assert tuple(selection.asset_id for selection in rendered.plan.selections) == ("primary",)
    selected = next(item.clip for item in rendered.candidates if item.clip.asset.id == "primary")
    assert not selected.live_burst_video_ids
    assert displayed_sample_members(row) == ("primary",)


def test_live_sample_material_matches_actual_product_expanded_manifest():
    from datetime import timedelta

    from immich_memories.analysis.editorial_source_route import project_source_rendering
    from immich_memories.analysis.motion_rendering import motion_renderings
    from immich_memories.config_loader import Config
    from tests.test_editorial_source_route import demand, photo

    first = photo("first", live="clip-one")
    second = photo("second", at=first.file_created_at + timedelta(seconds=1), live="clip-two")
    _, candidates = demand([first, second])
    manifest = motion_renderings([first, second], Config())["first"]
    row = unit(
        "first",
        kind="live-motion",
        members=["first", "second"],
        video_ids=list(manifest.video_ids),
        trim_points=[list(pair) for pair in manifest.trim_points],
    )
    rendered = project_source_rendering(
        [row],
        candidates,
        config=Config(),
        include_live_photos=True,
    )
    selected = next(item.clip for item in rendered.candidates if item.clip.asset.id == "first")
    assert (
        displayed_sample_members(row)
        == tuple(selected.live_burst_video_ids)
        == ("clip-one", "clip-two")
    )


@pytest.mark.parametrize(
    "value,expected_status", [(False, "sampled_different"), (None, "relation_unavailable")]
)
def test_negative_or_unavailable_relation_is_not_retried(value, expected_status):
    original = [unit("a"), unit("b")]
    kept, audit, calls = run(original, outcomes={frozenset(("a", "b")): value})
    assert kept == original and calls == [("a", "b")]
    assert audit["nominations"][0]["status"] == expected_status
    assert audit["incomplete"] is (value is None)


def test_default_work_budget_is_two_n_and_unresolved_pairs_are_kept():
    original = [unit(chr(97 + i)) for i in range(6)]
    kept, audit, calls = run(original, outcomes={})
    assert kept == original
    assert len(calls) == audit["relation_checks"] == audit["relation_check_limit"] == 12
    assert audit["incomplete"] and audit["unresolved_nominations"] == 3
    assert len(set(calls)) == len(calls)


def test_zero_work_budget_discovers_but_never_calls_or_removes():
    original = [unit("a"), unit("b")]
    kept, audit, calls = run(original, max_relation_checks=0)
    assert kept == original and calls == [] and audit["incomplete"]
    assert audit["nominations"][0]["status"] == "work_limit"


@pytest.mark.parametrize("bad_hash", [None, "0" * 15, "g" * 16, "0" * 17])
def test_missing_or_invalid_hash_cannot_be_a_cut_verdict(bad_hash):
    original = [unit("a"), unit("b")]
    kept, audit, calls = run(original, hashes={"a": "0" * 16, "b": bad_hash})
    assert kept == original and calls == [] and audit["incomplete"]
    assert audit["nominations"] == []


def test_existing_hash_threshold_is_inclusive_and_only_nominates():
    original = [unit("a"), unit("b")]
    kept, audit, calls = run(original, hashes={"a": "0" * 16, "b": "00000000000003FF"}, outcomes={})
    assert kept == original and calls == [("a", "b")]
    assert audit["nominations"][0]["nominated_edges"][0]["distance"] == 10
    kept, audit, calls = run(original, hashes={"a": "0" * 16, "b": "00000000000007ff"})
    assert kept == original and calls == [] and not audit["nominations"]


def test_a_hash_nominated_pair_carries_its_distance_and_a_description_one_does_not():
    original = [unit("a"), unit("b"), unit("c")]
    asked = []

    # WHY: behind this callback sits the image gateway; one call is one real comparison.
    def confirm(left, right, distance=None):
        asked.append((left, right, distance))
        return {"same": False, "source_pair": [left, right]}

    shown = "Two cyclists carry a red ladder along the canal towpath together"
    reduce_final_sampled_duplicates(
        original,
        picture_records={
            "a": {"status": "available", "description": shown},
            "b": {"status": "available", "description": shown},
            "c": {"status": "available"},
        },
        # b is nominated on its own description alone; only c is hash-close to a.
        preview_hashes={"a": "0" * 16, "b": "f" * 16, "c": "00000000000003ff"},
        confirm_relation=confirm,
    )
    assert sorted(asked) == [("a", "b", None), ("a", "c", 10)]


def test_replay_reuses_stable_relations_without_changing_semantic_audit():
    original = [unit("a"), unit("b"), unit("c")]
    records, hashes = inputs(original)
    bank, new_calls = {}, []

    def cached_relation(left, right, _distance=None):
        key = (left, right)
        if key not in bank:
            new_calls.append(key)
            bank[key] = {"same": key == ("a", "b"), "source_pair": list(key)}
        return deepcopy(bank[key])

    def replay():
        return reduce_final_sampled_duplicates(
            original,
            picture_records=records,
            preview_hashes=hashes,
            confirm_relation=cached_relation,
        )

    first = replay()
    cold_calls = len(new_calls)
    second = replay()
    assert first == second and len(new_calls) == cold_calls == 2


def test_identical_material_member_uses_direct_source_identity_without_extra_comparison():
    original = [unit("a", kind="live-motion", video_ids=["b"]), unit("b", kind="video")]
    kept, audit, calls = run(original)
    assert [row["asset_id"] for row in kept] == ["a"]
    assert calls == []
    assert (
        audit["removals"][0]["direct_member_proof"][0]["relation"]["basis"]
        == "identical_source_material_member"
    )


def test_empty_film_is_complete_with_zero_work():
    kept, audit, calls = run([])
    assert kept == calls == [] and not audit["incomplete"]
    assert audit["relation_checks"] == audit["relation_check_limit"] == 0


def test_standalone_video_with_own_id_declared_still_requires_one_sample_only():
    original = [unit("keep"), unit("remove", kind="video", video_ids=["remove"])]
    assert displayed_sample_members(original[1]) == ("remove",)
    kept, audit, calls = run(original)
    assert [row["asset_id"] for row in kept] == ["keep"]
    assert calls == [("keep", "remove")]
    assert len(audit["removals"][0]["direct_member_proof"]) == 1


@pytest.mark.parametrize("same", [True, False])
def test_far_hash_own_description_nomination_still_needs_positive_pixels(same):
    original = [unit("a"), unit("b", kind="video", taken="2025-08-01T12:00:00+00:00")]
    records, _hashes = inputs(original)
    for record in records.values():
        record["description"] = "Four people stand together smiling toward the camera indoors."
    hashes = {"a": "0000000000000000", "b": "0000000007ffffff"}
    kept, audit, calls = run(
        original,
        records=records,
        hashes=hashes,
        outcomes={frozenset(("a", "b")): same},
    )
    assert len(kept) == (1 if same else 2)
    assert calls == [("a", "b")]
    edge = audit["nominations"][0]["nominated_edges"][0]
    assert edge["distance"] == 27
    assert edge["signals"] == ["own-description"]
    assert audit["maximum_hash_distance"] == 10
    if same:
        assert audit["removals"][0]["direct_member_proof"][0]["relation"]["same"] is True
    else:
        assert not audit["removals"]


@pytest.mark.parametrize("own_text", [None, "", "the a of"])
def test_empty_own_description_never_borrows_original_caption_for_nomination(own_text):
    original = [unit("a"), unit("b")]
    records, _hashes = inputs(original)
    for record in records.values():
        record.update(
            description=own_text,
            original_caption="Four people stand together smiling toward the camera indoors.",
            original_line="Four people stand together smiling toward the camera indoors.",
        )
    kept, audit, calls = run(original, records=records, hashes={"a": "0" * 16, "b": "f" * 16})
    assert kept == original and calls == [] and audit["nominations"] == []


def test_far_hash_distinct_own_descriptions_do_not_nominate():
    original = [unit("a"), unit("b")]
    records, _hashes = inputs(original)
    records["a"]["description"] = "Cyclists racing through mountain roads."
    records["b"]["description"] = "Friends sitting around dinner table laughing."
    kept, audit, calls = run(original, records=records, hashes={"a": "0" * 16, "b": "f" * 16})
    assert kept == original and not calls and not audit["nominations"]


def test_far_hash_compound_member_needs_its_own_direct_pixel_confirmation():
    original = [unit("a"), unit("b", kind="live-motion", video_ids=["sample", "extra"])]
    records, hashes = inputs(original)
    for record in records.values():
        record["description"] = "People posing together smiling toward the camera indoors."
    hashes["sample"] = hashes["extra"] = "ffffffffffffffff"
    kept, audit, calls = run(
        original,
        records=records,
        hashes=hashes,
        outcomes={frozenset(("a", "sample")): True},
    )
    assert kept == original and calls == [("a", "sample"), ("a", "extra")]
    assert audit["nominations"][0]["status"] == "sampled_different"
    assert not audit["removals"]
