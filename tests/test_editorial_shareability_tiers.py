"""Each preparation tier answers the audience question with the evidence it prepared."""

from __future__ import annotations

import pytest

from immich_memories.analysis import editorial_shareability as share
from immich_memories.analysis.editorial_shareability_tiers import (
    audience_check_for,
    rule_audience,
    withheld_audience,
)


class Annotation:
    def __init__(self, description, heads=()):
        self.description = description
        self.heads = heads


class RefusingJudge:
    """A reduced tier must not reach the reader at all."""

    calls: list[str] = []

    def ask(self, stage, prompt, max_tokens):
        pytest.fail("a reduced tier asked the reader for an audience verdict")


def evidence_of(heads=(), flags=None, description=""):
    return share.evidence_for_unit(
        {"asset_id": "solo", "members": ["solo"]},
        {"solo": Annotation(description, heads)},
        flags or {},
        {},
    )


def flag(name, source="owner", reason="looked at again"):
    return {"solo": (share.FlagRow("solo", name, reason, source),)}


def test_an_uncaptioned_picture_is_held_but_for_a_reason_that_names_the_gap():
    """Both tiers hold it, and the reduced one says which evidence it lacked.

    Eight of the findings that refuse a unit -- bathing, toileting, a medical
    procedure and the rest -- are named only by a written description. The heads
    cannot see them, so "no detector objected" is not a clearance and the rule gate
    never grants one. What it does add over the model check is a finding that says
    the description was missing rather than unreadable.
    """
    evidence = evidence_of(heads=(("nsfw_marqo", "no"), ("people", "one")))

    model = share.check_audience(RefusingJudge(), evidence, "unit-1")
    rules = rule_audience(RefusingJudge(), evidence, "unit-1")

    assert model["verdict"] == "family_only" and model["finding"] == "unavailable_evidence"
    assert rules["verdict"] == "family_only"
    assert rules["finding"] == "unread_private_activity"


def test_no_tier_without_descriptions_can_ever_clear_a_unit():
    """The gate may only tighten, so a tier that reads nothing grants nothing."""
    for heads in ((("nsfw_marqo", "no"),), (("nsfw_marqo", "no"), ("swim", "no"))):
        evidence = evidence_of(heads=heads)

        assert rule_audience(RefusingJudge(), evidence, "unit-1")["verdict"] != "share"
        assert withheld_audience(RefusingJudge(), evidence, "unit-1")["verdict"] != "share"


def test_a_detector_positive_holds_the_unit_to_the_family():
    evidence = evidence_of(heads=(("nsfw_marqo", "yes"),))

    assert rule_audience(RefusingJudge(), evidence, "unit-1")["verdict"] == "family_only"


def test_an_exposure_flag_holds_the_unit_even_with_every_head_clear():
    evidence = evidence_of(
        heads=(("nsfw_marqo", "no"),), flags=flag("review", source="exposure-pass")
    )

    result = rule_audience(RefusingJudge(), evidence, "unit-1")

    assert result["verdict"] == "family_only"
    assert result["finding"] == "exposure_evidence"


def test_a_child_in_swimwear_is_held_to_the_family():
    evidence = evidence_of(heads=(("swim", "yes"), ("children", "yes"), ("nsfw_marqo", "no")))

    result = rule_audience(RefusingJudge(), evidence, "unit-1")

    assert result["verdict"] == "family_only"
    assert result["finding"] == "children_in_swimwear"


def test_an_owner_review_flag_holds_the_unit_to_the_family():
    evidence = evidence_of(heads=(("nsfw_marqo", "no"),), flags=flag("review", reason="check me"))

    result = rule_audience(RefusingJudge(), evidence, "unit-1")

    assert result["verdict"] == "family_only"
    assert result["finding"] == "owner_review_flag"


def test_the_tier_with_no_detectors_never_says_share():
    """Nothing looked at the picture, so nothing may clear it."""
    clean = evidence_of(heads=(("nsfw_marqo", "no"),), description="A quiet afternoon.")

    result = withheld_audience(RefusingJudge(), clean, "unit-1")

    assert result["verdict"] == "family_only"
    assert result["finding"] == "no_content_evidence"
    assert share.allowed(result["verdict"], "family")
    assert not share.allowed(result["verdict"], "sendable")


def test_each_tier_gets_its_own_check_and_an_unknown_one_gets_the_strictest():
    assert audience_check_for("full") is share.check_audience
    assert audience_check_for("no_captions") is rule_audience
    assert audience_check_for("metadata_only") is withheld_audience
    assert audience_check_for("something-a-later-schema-adds") is withheld_audience


def test_a_sendable_export_is_refused_outright_when_nothing_looked_at_the_pictures(tmp_path):
    """Not "share, because nothing objected" -- the tier has no evidence to clear anything with."""
    from dataclasses import replace

    from immich_memories.config_loader import Config
    from tests.test_editorial_story_first_planner import make_source

    source = make_source(tmp_path)
    metadata_only = Config()
    metadata_only.editorial.preparation.tier = "metadata_only"

    assert replace(source, audience="sendable").audience == "sendable"
    with pytest.raises(ValueError, match="sendable export needs the detector evidence"):
        replace(source, audience="sendable", config=metadata_only)
    assert replace(source, audience="family", config=metadata_only).audience == "family"


def _plan_at_tier(tmp_path, tier):
    from dataclasses import replace

    from immich_memories.config_loader import Config
    from tests.test_editorial_duration_planner_integration import run
    from tests.test_editorial_story_first_planner import StoryJudge, make_source

    config = Config()
    config.editorial.preparation.tier = tier
    return run(replace(make_source(tmp_path / tier), config=config), StoryJudge())


def test_a_cut_with_no_models_and_no_captions_still_completes_and_shares_nothing(tmp_path):
    """The exit test: losing good pictures is fine, losing an occasion is not."""
    full = _plan_at_tier(tmp_path, "full")
    reduced = _plan_at_tier(tmp_path, "metadata_only")

    assert reduced["carriers"], "the reduced tier cut nothing at all"
    verdicts = reduced["shareability"]["verdicts"]
    assert verdicts and not [row for row in verdicts.values() if row["verdict"] == "share"]

    def occasions(plan):
        return {row["episode"] for row in plan["story"]["episodes"] if row["granted"]}

    assert occasions(full), "the full tier kept no occasion to compare against"
    assert occasions(reduced) >= occasions(full)


def test_a_cut_without_captions_keeps_the_verdicts_the_gate_still_has_evidence_for(tmp_path):
    reduced = _plan_at_tier(tmp_path, "no_captions")

    verdicts = reduced["shareability"]["verdicts"]
    assert reduced["carriers"] and verdicts
    assert {row["policy"] for row in verdicts.values()} == {
        "audience-rules-v1-detector-heads-and-flags"
    }
