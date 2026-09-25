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


def evidence_of(heads=(), flags=None, description="", line=""):
    return share.evidence_for_unit(
        {"asset_id": "solo", "members": ["solo"]},
        {"solo": Annotation(description, heads)},
        flags or {},
        {"solo": line},
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
    for heads in ((("nsfw_marqo", "no"),), (("nsfw_marqo", "no"), ("children", "no"))):
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


def test_the_retired_swim_head_no_longer_raises_a_swimwear_hold():
    """Measured against a typed reader it flagged 551 pictures where 22 held swimwear."""
    evidence = evidence_of(heads=(("swim", "yes"), ("children", "yes"), ("nsfw_marqo", "no")))

    result = rule_audience(RefusingJudge(), evidence, "unit-1")

    assert result["verdict"] == "family_only"
    assert result["finding"] == "unread_private_activity"


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
    assert not share.allowed(result["verdict"], "shareable")


def test_each_tier_gets_its_own_check_and_an_unknown_one_gets_the_strictest():
    assert audience_check_for("full") is share.check_audience
    assert audience_check_for("no_captions") is rule_audience
    assert audience_check_for("metadata_only") is withheld_audience
    assert audience_check_for("something-a-later-schema-adds") is withheld_audience


def test_a_shareable_export_is_refused_outright_when_nothing_looked_at_the_pictures(tmp_path):
    """Not "share, because nothing objected" -- the tier has no evidence to clear anything with."""
    from dataclasses import replace

    from immich_memories.config_loader import Config
    from tests.test_editorial_story_first_planner import make_source

    source = make_source(tmp_path)
    metadata_only = Config()
    metadata_only.editorial.preparation.tier = "metadata_only"

    assert replace(source, audience="shareable").audience == "shareable"
    with pytest.raises(ValueError, match="shareable export needs the detector evidence"):
        replace(source, audience="shareable", config=metadata_only)
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


def _chain_evidence(heads=(("nsfw_marqo", "no"),), description="", size=6, flagged=3):
    from immich_memories.analysis.editorial_exposure_chains import ChainHold

    return share.evidence_for_unit(
        {"asset_id": "solo", "members": ["solo"]},
        {"solo": Annotation(description, heads)},
        {},
        {"solo": ""},
        chains={"solo": ChainHold(size=size, flagged=flagged, swept_in=True)},
    )


class ClearingJudge:
    """A reader that finds nothing, so only a floor under it can hold the unit."""

    calls: list[str] = []

    def ask(self, _stage, _prompt, max_tokens):
        return '{"finding":"none","why":"a family kitchen"}'


def test_a_capture_swept_in_by_its_run_is_held_with_the_run_in_its_evidence():
    evidence = _chain_evidence()

    result = rule_audience(RefusingJudge(), evidence, "unit-1")

    assert result["verdict"] == "family_only"
    assert result["finding"] == "exposure_chain"
    assert result["exposure_chain"]["chain_size"] == 6
    assert result["exposure_chain"]["chain_flagged"] == 3


def test_the_reader_cannot_clear_a_capture_its_run_holds():
    """The reader sees one unit's captions; it cannot see the three minutes around it."""
    evidence = _chain_evidence(description="A family kitchen at breakfast.")

    result = share.check_audience(ClearingJudge(), evidence, "unit-1")

    assert result["verdict"] == "family_only"
    assert result["finding"] == "exposure_chain"


def test_a_unit_in_no_run_reads_exactly_as_it_did_before_there_were_runs():
    evidence = share.evidence_for_unit(
        {"asset_id": "solo", "members": ["solo"]},
        {"solo": Annotation("A family kitchen at breakfast.", (("nsfw_marqo", "no"),))},
        {},
        {"solo": ""},
    )

    assert "exposure_chain" not in evidence
    assert share.check_audience(ClearingJudge(), evidence, "unit-1")["verdict"] == "share"


def _with_clip(clip_heads):
    """A clean, clothed still whose attached clip carries only what the detectors banked."""
    return share.evidence_for_unit(
        {"asset_id": "still", "members": ["still"], "video_ids": ["clip"]},
        {"still": Annotation("A fully clothed family waves.", (("nsfw_marqo", "no"),))},
        {},
        {},
        companion_heads={"clip": clip_heads},
    )


class ClearingReader(ClearingJudge):
    """Finds nothing in the captions and reads every person in them as clothed."""

    def ask(self, stage, prompt, max_tokens):
        if "exposure" in stage:
            return '{"observations":{"p1":[["a family","clothing"]]}}'
        return super().ask(stage, prompt, max_tokens)


def test_a_clean_still_is_held_by_its_flagged_clip_on_every_tier():
    """A clip has no caption and nothing selects it; its detector row is all there is."""
    evidence = _with_clip({"nsfw_marqo": "yes"})

    assert evidence["companion_detectors"] == [{"nsfw_marqo": "yes"}]
    rules = rule_audience(RefusingJudge(), evidence, "unit-1")
    assert rules["verdict"] == "family_only" and rules["finding"] == "exposure_evidence"
    # The reader clears every person the captions describe -- and the captions describe
    # the still, so the clip is still unaccounted for.
    model = share.check_audience(ClearingReader(), evidence, "unit-1")
    assert model["verdict"] == "family_only" and model["finding"] == "clip_exposure"


def test_a_clean_clip_leaves_its_still_exactly_where_it_was():
    evidence = _with_clip({"nsfw_marqo": "no"})

    assert share.check_audience(ClearingJudge(), evidence, "unit-1")["verdict"] == "share"


def test_a_clip_nothing_has_read_reads_as_it_always_did():
    """An empty bank for the clip is the evidence every Live Photo carried before."""
    evidence = _with_clip({})

    assert evidence["companion_detectors"] == []
    assert share.check_audience(ClearingJudge(), evidence, "unit-1")["verdict"] == "share"


def test_a_clips_detector_rows_are_read_at_the_version_this_run_reads(tmp_path):
    import sqlite3

    store = tmp_path / "annotations.sqlite"
    with sqlite3.connect(store) as connection:
        connection.execute(
            "CREATE TABLE head_facts (asset_id TEXT, head TEXT, version TEXT, label TEXT, "
            "confidence REAL, encoder_key TEXT, decided_at TEXT)"
        )
        connection.executemany(
            "INSERT INTO head_facts VALUES (?, ?, ?, ?, 0.9, 'k', 't')",
            [
                ("clip", "nsfw_marqo", "det-v3", "yes"),
                # A row from the one-preview read no longer answers for the clip.
                ("other", "nsfw_marqo", "det-v2", "yes"),
                # Not an audience head: the gate never asks it.
                ("clip", "aesthetic", "det-v3", "high"),
            ],
        )
    connection.close()

    heads = share.load_detector_heads(
        store, ["clip", "other", "unread"], {"nsfw_marqo": "det-v3", "aesthetic": "det-v3"}
    )

    assert heads == {"clip": {"nsfw_marqo": "yes"}}


def test_a_clip_the_nsfw_head_held_stays_held_when_the_model_reports_no_uncovered_person():
    """A model reading only adds holds: the owner prefers a false positive to a miss."""
    evidence = _with_clip({"nsfw_marqo": "yes"})
    evidence["companion_body_warnings"][0]["body_observation"] = {"uncovered_person": "no"}

    model = share.check_audience(ClearingReader(), evidence, "unit-1")

    assert model["verdict"] == "family_only" and model["finding"] == "clip_exposure"


def test_a_still_the_nsfw_head_held_stays_held_when_the_captions_clear_every_person():
    evidence = share.evidence_for_unit(
        {"asset_id": "still", "members": ["still"]},
        {"still": Annotation("A fully clothed family waves.", (("nsfw_marqo", "yes"),))},
        {},
        {},
    )

    model = share.check_audience(ClearingReader(), evidence, "unit-1")

    assert model["verdict"] == "family_only" and model["finding"] == "exposure_evidence"


def test_the_owner_clearing_a_picture_on_the_pool_page_is_what_lifts_its_detector_hold():
    evidence = share.evidence_for_unit(
        {"asset_id": "still", "members": ["still"]},
        {"still": Annotation("A fully clothed family waves.", (("nsfw_marqo", "yes"),))},
        {"still": [share.FlagRow("still", "cleared", "owner reviewed", "owner")]},
        {},
    )

    assert share.check_audience(ClearingReader(), evidence, "unit-1")["verdict"] == "share"
