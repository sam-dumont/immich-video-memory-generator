"""What the audience reader is shown: the caption, the heads and the flags ingest banked."""

from __future__ import annotations

from immich_memories.analysis import editorial_shareability as share
from tests.test_editorial_audience_check import Annotation, Judge, evidence_of, finding


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


def test_no_member_carries_a_picture_observation_only_ingest_facts():
    unit = {"asset_id": "still", "members": ["still"], "video_ids": ["motion"]}
    evidence = evidence_of(
        unit,
        {"still": Annotation("A person at the seaside.", (("uncovered_person", "no"),))},
    )

    assert set(evidence["members"][0]) == {"member", "caption", "detectors", "flags"}


def test_a_clip_the_exposure_head_flagged_holds_its_still_whatever_the_reader_says():
    unit = {"asset_id": "still", "members": ["still"], "video_ids": ["motion"]}
    evidence = share.evidence_for_unit(
        unit,
        {"still": Annotation("A fully clothed family waves.")},
        {},
        {},
        companion_heads={"motion": {"nsfw_marqo": "yes"}},
    )

    result = share.check_audience(Judge([finding("none")]), evidence, "unit-1")

    assert result["verdict"] == "family_only"
