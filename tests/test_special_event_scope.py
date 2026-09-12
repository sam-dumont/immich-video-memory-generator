"""An event admission is explicit evidence bound to exact membership, never inferred."""

from __future__ import annotations

import hashlib
import json

import pytest

from immich_memories.analysis.special_event_scope import (
    SpecialEventAdmission,
    read_special_event_admission,
    select_source_members,
    validate_special_event_scope,
)
from tests.conftest import make_asset, make_clip


def event_id_for(members):
    digest = hashlib.sha256(json.dumps(sorted(members), separators=(",", ":")).encode()).hexdigest()
    return f"special-event-{digest[:24]}", digest


def admission(members=("one", "two"), **changes):
    event_id, digest = event_id_for(members)
    fields = {
        "origin": "catalogue",
        "event_id": event_id,
        "membership_sha256": digest,
        "evidence_sha256": "b" * 64,
        "evidence_ref": "catalogue-row-3",
    }
    return SpecialEventAdmission(**(fields | changes))


def test_an_empty_scope_stays_empty_rather_than_expanding_to_a_whole_day():
    assert validate_special_event_scope(None, []) == ()


def test_membership_travels_with_the_identity_it_hashes_to():
    members = ("one", "two")
    event_id, _ = event_id_for(members)

    assert validate_special_event_scope(event_id, members) == members


@pytest.mark.parametrize(
    "event_id,members,product,match",
    [
        ("special-event-x", "one", "special_day", "sequence of asset IDs"),
        ("special-event-x", ["one"], "year", "special_day product"),
        (None, ["one"], "special_day", "must travel together"),
        ("  ", ["one"], "special_day", "must travel together"),
        ("special-event-x", [], "special_day", "must travel together"),
        ("special-event-x", ["one", ""], "special_day", "nonblank asset IDs"),
        ("special-event-x", ["one", "one"], "special_day", "unique"),
        ("special-event-x", ["one", "two"], "special_day", "does not match its canonical"),
    ],
)
def test_an_incomplete_or_invented_scope_is_refused(event_id, members, product, match):
    with pytest.raises(ValueError, match=match):
        validate_special_event_scope(event_id, members, product=product)


def test_an_admission_binds_the_exact_scope_that_was_selected():
    members = ("one", "two")
    event_id, _ = event_id_for(members)

    admission(members).validate_scope(event_id, members, product="special_day")


def test_an_admission_refuses_a_scope_it_does_not_bind():
    other_id, _ = event_id_for(("one", "three"))

    with pytest.raises(ValueError, match="does not bind"):
        admission().validate_scope(other_id, ("one", "three"), product="special_day")


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"origin": "guessed_from_the_name"}, "unknown special event admission origin"),
        ({"evidence_sha256": "short"}, "full SHA256 evidence identities"),
        ({"evidence_sha256": "z" * 64}, "full SHA256 evidence identities"),
        ({"event_id": "special-event-invented"}, "does not match its membership"),
        ({"evidence_ref": "   "}, "existing evidence reference"),
    ],
)
def test_an_admission_without_complete_evidence_never_exists(changes, match):
    with pytest.raises(ValueError, match=match):
        admission(**changes)


def test_a_catalogue_row_becomes_an_admission_that_still_carries_the_row_identity():
    members = ["two", "one"]
    event_id, digest = event_id_for(members)
    record = {"event_id": event_id, "asset_ids": members, "name": "an outing"}

    admitted = SpecialEventAdmission.from_catalogue_record(record, evidence_ref="catalogue-row-3")

    assert admitted.origin == "catalogue"
    assert admitted.membership_sha256 == digest
    assert admitted.evidence_ref == "catalogue-row-3"
    assert admitted.as_record()["event_id"] == event_id
    assert (
        SpecialEventAdmission.from_catalogue_record(
            record | {"name": "another outing"}, evidence_ref="catalogue-row-3"
        ).evidence_sha256
        != admitted.evidence_sha256
    )


def test_a_day_only_catalogue_row_has_no_exact_event_admission():
    with pytest.raises(ValueError, match="no exact event admission"):
        SpecialEventAdmission.from_catalogue_record({}, evidence_ref="catalogue-row-3")


def test_a_missing_admission_stays_provisional_and_a_complete_record_is_read_back():
    assert read_special_event_admission(None) is None

    existing = admission()
    assert read_special_event_admission(existing) is existing
    assert read_special_event_admission(existing.as_record()) == existing


@pytest.mark.parametrize(
    "value",
    [
        {"origin": "catalogue"},
        dict(admission().as_record(), extra="unexpected"),
        dict(admission().as_record(), evidence_ref=3),
        "special-event-3",
    ],
)
def test_a_partial_admission_record_is_refused_rather_than_completed(value):
    with pytest.raises(ValueError, match="complete explicit evidence record"):
        read_special_event_admission(value)


def test_membership_selects_visual_sources_and_keeps_a_stills_live_photo_link():
    still = make_asset("still")
    still.live_photo_video_id = "motion"
    other = make_asset("other")
    clip = make_clip("footage", duration=8)

    selected = select_source_members([still, other, clip], ["still", "footage"])

    assert [source.asset.id if hasattr(source, "asset") else source.id for source in selected] == [
        "still",
        "footage",
    ]
    assert selected[0].live_photo_video_id == "motion"


def test_no_membership_at_all_keeps_every_requested_source():
    sources = [make_asset("one"), make_asset("two")]

    assert select_source_members(sources, None) == tuple(sources)
