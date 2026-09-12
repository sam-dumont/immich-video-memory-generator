"""What the audience reader is shown, and what a direct body observation stops."""

from __future__ import annotations

import pytest

from immich_memories.analysis import editorial_shareability as share
from tests.test_editorial_audience_check import Annotation, evidence_of, observed


def test_every_rendered_member_becomes_its_own_numbered_evidence_row():
    unit = {"asset_id": "still", "members": ["still", "second"], "video_ids": ["motion"]}
    annotations = {
        "still": Annotation("A child in a coat.", (("nsfw_marqo", "no"),)),
        "second": Annotation("The same child, closer."),
    }

    evidence = evidence_of(unit, annotations)

    assert [member["member"] for member in evidence["members"]] == ["p1", "p2"]
    assert evidence["members"][0]["caption"] == "The same child, closer."
    assert evidence["members"][1]["detectors"] == {"nsfw_marqo": "no"}
    assert evidence["version"] == share.AUDIENCE_CHECK_POLICY_VERSION


def test_a_legacy_line_becomes_evidence_without_forwarding_names_or_editorial_metadata():
    unit = {"asset_id": "solo", "members": ["solo"]}

    evidence = share.evidence_for_unit(
        unit,
        {},
        {},
        {"solo": "2020-01-01 | A person on a beach. | with Alex | nsfw_marqo=no, venue=outdoors"},
    )

    row = evidence["members"][0]
    assert row["caption"] == "A person on a beach."
    assert row["detectors"] == {"nsfw_marqo": "no", "venue": "outdoors"}


def test_an_uncaptioned_companion_contributes_a_warning_its_still_cannot_clear():
    unit = {"asset_id": "still", "members": ["still"], "video_ids": ["motion"]}
    annotations = {"still": Annotation("A person at the seaside.")}
    flags = {"motion": (share.FlagRow("motion", "review", "exposure=partial", "exposure"),)}

    evidence = evidence_of(unit, annotations, flags)

    assert [member["member"] for member in evidence["members"]] == ["p1"]
    assert evidence["companion_body_warnings"][0]["member"] == "v1"
    assert "not its clearance" in evidence["companion_body_warnings"][0]["scope"]


def test_an_owner_clearance_on_the_companion_itself_removes_its_warning():
    unit = {"asset_id": "still", "members": ["still"], "video_ids": ["motion"]}
    flags = {
        "motion": (
            share.FlagRow("motion", "review", "exposure=partial", "exposure"),
            share.FlagRow("motion", "cleared", "the owner looked", "owner"),
        )
    }

    evidence = evidence_of(unit, {"still": Annotation("A person at the seaside.")}, flags)

    assert "companion_body_warnings" not in evidence


def test_a_bound_positive_observation_stops_acquisition_without_clearing_the_rest():
    unit = {"asset_id": "first", "members": ["first", "second"], "kind": "live_photo"}
    records = {"first": observed("yes")}

    hold = share.terminal_body_hold(unit, records, "first")

    assert hold["verdict"] == "family_only"
    assert hold["finding"] == "nudity_shirtless_or_underwear"
    assert hold["acquisition_stop"]["witness_member"] == "p1"
    assert hold["acquisition_stop"]["unobserved_members"] == ["p2"]
    assert "no clearance of other members" in hold["acquisition_stop"]["evidence_scope"]


@pytest.mark.parametrize(
    "witness,records",
    [
        ("outsider", {"outsider": observed("yes")}),
        ("first", {"first": observed("no")}),
        ("first", {}),
    ],
)
def test_nothing_but_a_bound_positive_stops_acquisition(witness, records):
    unit = {"asset_id": "first", "members": ["first", "second"]}

    assert share.terminal_body_hold(unit, records, witness) is None


def test_the_request_identity_follows_the_evidence_and_the_policy_it_was_asked_under(monkeypatch):
    evidence = evidence_of(
        {"asset_id": "solo", "members": ["solo"]}, {"solo": Annotation("A person walking.")}
    )
    original = share.audience_check_key(evidence)

    other = evidence_of(
        {"asset_id": "solo", "members": ["solo"]}, {"solo": Annotation("A person running.")}
    )
    assert share.audience_check_key(other) != original

    monkeypatch.setattr(share, "AUDIENCE_PROMPT_VERSION", "future-audience-policy")
    assert share.audience_check_key(evidence) != original
