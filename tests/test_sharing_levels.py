"""One level per film: just us, family (the default), shareable (#1325)."""

from __future__ import annotations

import pytest

from immich_memories.analysis import editorial_shareability as share


@pytest.mark.parametrize(
    ("verdict", "plays_in"),
    [
        ("share", {"just_us", "family", "shareable"}),
        ("family_only", {"just_us", "family"}),
        ("just_us", {"just_us"}),
        ("do_not_show", set()),
    ],
)
def test_each_level_plays_what_it_allows(verdict, plays_in):
    assert {level for level in share.LEVELS if share.allowed(verdict, level)} == plays_in


def test_the_strictest_verdict_still_wins():
    assert share.tighten("family_only", "just_us") == "just_us"
    assert share.tighten("just_us", "do_not_show") == "do_not_show"


# The caption reading, on the model tier.

from immich_memories.analysis.editorial_structure_audience import (  # noqa: E402
    AudienceBank,
    AudienceGate,
)
from tests.test_editorial_shareability_tiers import Annotation  # noqa: E402

UNIT = {"asset_id": "solo", "members": ["solo"]}


class Reader:
    """Answers the activity question with one finding."""

    def __init__(self, finding):
        self.finding = finding
        self.calls = []

    def ask(self, stage, prompt, max_tokens):
        self.calls.append({"stage": stage})
        return f'{{"finding": "{self.finding}", "why": "the caption says so"}}'


def gate(tmp_path, caption, finding, level, *, heads=(("nsfw_marqo", "no"),)):
    return AudienceGate(
        Reader(finding),
        audience=level,
        annotations={"solo": Annotation(caption, heads)},
        flag_rows={},
        lines={"solo": caption},
        bank_path=tmp_path / "shareability.private.json",
        library=AudienceBank(tmp_path / "audience.private.json", answerer="full|reader"),
    )


BATH = "A baby is bathing in a small tub."


@pytest.mark.parametrize("level", share.LEVELS)
def test_a_household_moment_the_caption_names_plays_only_in_a_just_us_film(tmp_path, level):
    verdict = gate(tmp_path, BATH, "bathing", level).verdict_of(UNIT)

    assert verdict == "just_us"
    assert share.allowed(verdict, level) == (level == share.JUST_US)


def test_what_is_not_a_household_moment_never_plays(tmp_path):
    caption = "An open passport lies on a table."
    verdict = gate(tmp_path, caption, "identifying_record", "just_us").verdict_of(UNIT)

    assert verdict == "do_not_show"


def test_a_detector_floor_under_a_household_moment_still_holds_it(tmp_path):
    held = gate(tmp_path, BATH, "bathing", "just_us", heads=(("nsfw_marqo", "yes"),))

    assert held.verdict_of(UNIT) == "just_us"


def test_a_household_hold_banked_before_levels_existed_plays_in_a_just_us_film(tmp_path):
    AudienceBank(tmp_path / "audience.private.json", answerer="full|reader").hold(
        "solo",
        {
            "verdict": "do_not_show",
            "finding": "private_activity",
            "policy": share.AUDIENCE_PROMPT_VERSION,
        },
    )

    assert gate(tmp_path, BATH, "bathing", "just_us").verdict_of(UNIT) == "just_us"


def test_an_older_refusal_the_current_reading_cannot_place_stays_refused(tmp_path):
    AudienceBank(tmp_path / "audience.private.json", answerer="full|reader").hold(
        "solo",
        {
            "verdict": "do_not_show",
            "finding": "private_activity",
            "policy": share.AUDIENCE_PROMPT_VERSION,
        },
    )

    assert gate(tmp_path, BATH, "none", "just_us").verdict_of(UNIT) == "do_not_show"


# Shareable on a NAS: clean evidence is a clearance when strict sharing is on.

from immich_memories.analysis.editorial_shareability_tiers import audience_check_for  # noqa: E402
from tests.test_editorial_shareability_tiers import RefusingJudge  # noqa: E402

CLEAN = (("nsfw_marqo", "no"), ("uncovered_person", "no"), ("doc_docling", "photograph"))


def nas_verdict(heads=CLEAN, flags=None, *, strict=True, clip=None):
    unit = {"asset_id": "solo", "members": ["solo"]} | ({"video_ids": ["clip"]} if clip else {})
    evidence = share.evidence_for_unit(
        unit,
        {"solo": Annotation("", heads)},
        flags or {},
        {"solo": ""},
        companion_heads={"clip": dict(clip)} if clip else None,
    )
    check = audience_check_for("no_captions", strict_sharing=strict)
    return check(RefusingJudge(), evidence, "unit-1")


def test_a_picture_every_head_read_as_clean_is_shareable_on_a_nas():
    result = nas_verdict()

    assert result["verdict"] == "share"
    assert result["finding"] == "clean_evidence"


@pytest.mark.parametrize(
    "heads",
    [
        (("nsfw_marqo", "yes"),),
        (("nsfw_marqo", "no"), ("uncovered_person", "yes")),
        (("nsfw_marqo", "no"), ("doc_docling", "table")),
        (("nsfw_marqo", "no"), ("venue", "bedroom")),
        (("uncovered_person", "no"),),  # the nudity detector never read it
        (),
    ],
    ids=["nudity", "uncovered", "document", "private-venue", "unread", "nothing"],
)
def test_anything_flagged_or_unread_stays_in_the_family(heads):
    assert nas_verdict(heads)["verdict"] == "family_only"


def test_any_flag_on_the_picture_keeps_it_in_the_family():
    flags = {"solo": (share.FlagRow("solo", "review", "", "exposure-pass"),)}

    assert nas_verdict(flags=flags)["verdict"] == "family_only"


def test_a_flagged_or_unread_live_clip_keeps_its_still_in_the_family():
    assert nas_verdict(clip={"nsfw_marqo": "yes"})["verdict"] == "family_only"
    assert nas_verdict(clip={"uncovered_person": "no"})["verdict"] == "family_only"
    assert nas_verdict(clip={"nsfw_marqo": "no"})["verdict"] == "share"


def test_without_strict_sharing_a_nas_clears_nothing():
    assert nas_verdict(strict=False)["verdict"] == "family_only"


# Whole cuts.

from dataclasses import replace  # noqa: E402

from immich_memories.analysis.editorial_structure_contract import (  # noqa: E402
    StructurePlannerPorts,
)
from immich_memories.analysis.editorial_structure_planner import plan_structure  # noqa: E402
from immich_memories.config_loader import Config  # noqa: E402
from tests.editorial_story_fixtures import ControlledStoryJudge  # noqa: E402
from tests.editorial_thin_fixtures import caption_laya  # noqa: E402
from tests.test_editorial_duration_planner_integration import source  # noqa: E402


def nas_cut(captured, level, name="cut"):
    from immich_memories.analysis.editorial_rule_reader import NoModelJudge, RuleStructureReader

    nas = replace(
        captured,
        audience=level,
        config=Config(editorial={"preparation": {"tier": "no_captions"}, "reader": "rules"}),
        artifact_dir=captured.bank_dir.parent / f"{name}-{level}",
    )
    ports = StructurePlannerPorts(
        judge=NoModelJudge(), thumbnail_hash=lambda _: None, rules=RuleStructureReader(nas)
    )
    return plan_structure(nas, ports).plan


def shots(plan):
    return [row["asset_id"] for row in plan["carriers"]]


def flag_nudity(captured, asset_id):
    line = captured.audience_annotations[asset_id]
    return replace(
        captured,
        audience_annotations=captured.audience_annotations
        | {asset_id: replace(line, heads=(("nsfw_marqo", "yes"),))},
    )


def test_a_shareable_nas_film_plays_the_clean_pictures_and_leaves_the_flagged_one(tmp_path):
    family_first = shots(nas_cut(source(tmp_path, seconds=60), "family"))
    flagged = flag_nudity(source(tmp_path, seconds=60), family_first[0])

    family = shots(nas_cut(flagged, "family", "again"))
    shared = nas_cut(flagged, "shareable")

    assert family_first[0] in family
    assert family_first[0] not in shots(shared)
    assert shots(shared), "the clean pictures still make a film"
    assert shared["shareability"]["audience"] == "shareable"


def test_a_just_us_film_plays_the_bath_a_family_film_leaves_out(tmp_path):
    captured = source(tmp_path, seconds=60, private_opening=True)

    def cut(level):
        plan = plan_structure(
            replace(captured, audience=level, artifact_dir=captured.bank_dir.parent / level),
            StructurePlannerPorts(
                judge=ControlledStoryJudge(), thumbnail_hash=lambda _: None, laya=caption_laya()
            ),
        ).plan
        return shots(plan)

    assert "picture-000" not in cut("family")
    assert "picture-000" in cut("just_us")


def test_the_gpu_tier_holds_the_bath_with_laya_and_asks_no_llm(tmp_path):
    """The rules reader with captions and Laya: the light models do the job, no LLM is asked."""
    from immich_memories.analysis.editorial_rule_reader import NoModelJudge, RuleStructureReader

    gpu = replace(
        source(tmp_path, seconds=12, pictures=2, private_opening=True),
        audience="family",
        config=Config(tier="gpu"),
    )
    laya = caption_laya()
    judge = NoModelJudge()
    plan = plan_structure(
        gpu,
        StructurePlannerPorts(
            judge=judge, thumbnail_hash=lambda _: None, rules=RuleStructureReader(gpu), laya=laya
        ),
    ).plan

    assert "picture-000" not in shots(plan)
    assert plan["shareability"]["verdicts"]["picture-000"]["finding"] == "private_activity"
    assert laya.scorer.states, "Laya read the captions"
    assert judge.calls == []


def test_the_full_tier_asks_the_llm_no_sharing_question_even_where_laya_cannot_answer(tmp_path):
    """A flagged picture never reaches Laya and a bath does; neither goes to the reader."""
    captured = flag_nudity(source(tmp_path, seconds=60, private_opening=True), "picture-001")
    judge = ControlledStoryJudge()
    plan = plan_structure(
        replace(captured, audience="shareable"),
        StructurePlannerPorts(judge=judge, thumbnail_hash=lambda _: None, laya=caption_laya()),
    ).plan

    assert not {"picture-000", "picture-001"} & set(shots(plan))
    assert not [c for c in judge.calls if c["stage"].startswith("shareability-")]


# Choosing the level.


def test_the_config_default_is_family_and_the_flag_values_name_the_levels():
    assert Config().defaults.sharing == "family"
    assert [share.level_of(v) for v in ("just-us", "family", "shareable")] == list(share.LEVELS)
    with pytest.raises(ValueError, match="just-us, family or shareable"):
        share.level_of("everyone")


def test_a_shareable_film_is_refused_before_the_cut_only_where_no_detector_ran():
    from immich_memories.analysis.editorial_shareability_tiers import sharing_refusal

    def refusal(tier, level):
        config = Config(defaults={"sharing": level})
        # An internal reduced-evidence fixture, not a second user-configurable tier.
        config.editorial.preparation.tier = tier
        return sharing_refusal(config)

    assert refusal("no_captions", "shareable") is None
    assert refusal("full", "shareable") is None
    assert refusal("metadata_only", "family") is None
    assert "no_captions" in refusal("metadata_only", "shareable")


def test_the_cli_run_carries_its_level_to_the_editor():
    from datetime import datetime

    from immich_memories.cli._editorial_context import build_editorial_context
    from immich_memories.cli._run_inputs import ResolvedRunInputs
    from immich_memories.timeperiod import DateRange

    config = Config(defaults={"sharing": "just-us"})
    resolved = ResolvedRunInputs.from_arguments(
        include_photos=False,
        photo_assets=None,
        dry_run=False,
        automation_attempt_id=None,
        upload_to_immich=False,
        config=config,
        person_names=[],
        music=None,
        memory_preset_params={},
    )

    context = build_editorial_context(
        resolved=resolved,
        config=config,
        memory_type="monthly_highlights",
        memory_key=None,
        output_stem="june",
        assets=[],
        date_range=DateRange(datetime(2024, 6, 1), datetime(2024, 6, 30)),
        date_ranges=None,
        duration=60.0,
        transition="smart",
        title_override=None,
        person_names=[],
        accept_any_provenance=False,
    )

    assert context.audience == "just_us"


# Clearing a hold for a level (#1325, point 4).

from immich_memories.store import owner_decisions as owner  # noqa: E402

HELD = (("nsfw_marqo", "yes"),)


def owner_gate(tmp_path, store, level, caption="A person on a beach.", finding="none"):
    return AudienceGate(
        Reader(finding),
        audience=level,
        annotations={"solo": Annotation(caption, HELD)},
        flag_rows=share.load_flags(store, ["solo"]),
        lines={"solo": caption},
        bank_path=tmp_path / f"{level}-shareability.private.json",
        library=AudienceBank(tmp_path / "audience.private.json", answerer="full|reader"),
    )


@pytest.mark.parametrize(
    ("cleared_for", "verdict"),
    [("anyone", "share"), ("family", "family_only"), ("just-us", "just_us")],
)
def test_a_hold_cleared_for_a_level_plays_up_to_that_level(tmp_path, cleared_for, verdict):
    store = tmp_path / "annotations.sqlite"
    owner.decide(store, "solo", owner.clearance_for(cleared_for), via="cli")

    plays = {
        level
        for level in share.LEVELS
        if share.allowed(owner_gate(tmp_path, store, level).verdict_of(UNIT), level)
    }

    assert owner_gate(tmp_path, store, "family").verdict_of(UNIT) == verdict
    assert plays == {level for level in share.LEVELS if share.allowed(verdict, level)}


def test_clearing_for_the_family_lifts_a_caption_hold_the_reader_keeps_casting(tmp_path):
    store = tmp_path / "annotations.sqlite"
    owner.decide(store, "solo", owner.clearance_for("family"), via="web")

    verdict = owner_gate(tmp_path, store, "family", BATH, "bathing").verdict_of(UNIT)

    assert verdict == "family_only"


def test_a_burst_the_owner_cleared_at_two_levels_takes_the_stricter(tmp_path):
    store = tmp_path / "annotations.sqlite"
    owner.decide(store, "a", owner.clearance_for("anyone"), via="cli")
    owner.decide(store, "b", owner.clearance_for("just-us"), via="cli")
    flags = share.load_flags(store, ["a", "b"])

    assert share.owner_verdict({"asset_id": "a", "members": ["a", "b"]}, flags) == "just_us"
    assert share.owner_verdict({"asset_id": "a", "members": ["a", "c"]}, flags) is None


def test_the_no_model_draft_lifts_a_banked_refusal_only_where_the_clearance_reaches(tmp_path):
    from immich_memories.analysis.editorial_rule_banked_facts import open_banked_facts
    from immich_memories.analysis.editorial_structure_audience import AUDIENCE_BANK_NAME

    store = tmp_path / "annotations.sqlite"
    bank_dir = tmp_path / "structure-banks" / "case"
    AudienceBank(bank_dir.parent / AUDIENCE_BANK_NAME, answerer="full|reader").hold(
        "held", {"verdict": "family_only", "finding": "exposure_evidence", "policy": "heads"}
    )
    owner.decide(store, "held", owner.clearance_for("family"), via="cli")

    def refused(level):
        return open_banked_facts(
            bank_dir=bank_dir,
            attempts_dir=None,
            store_path=store,
            audience=level,
            episode_cards={},
        ).refused_for_audience("held")

    assert not refused("family")
    assert refused("shareable")
