"""The owner's own word on a picture: clear its hold, or never use it. Kept in the library's
annotation store, one decision per picture, and read by every tier."""

from __future__ import annotations

from immich_memories.analysis import editorial_shareability as share
from immich_memories.store import owner_decisions as owner


def excluded(store, *ids):
    return share.never_auto_ids(share.load_flags(store, ids))


def test_a_picture_the_owner_will_never_use_is_never_a_carrier(tmp_path):
    store = tmp_path / "annotations.sqlite"

    owner.decide(store, "p1", owner.NEVER_USE, via="cli")

    assert excluded(store, "p1", "p2") == {"p1"}


def test_a_new_decision_replaces_the_old_one(tmp_path):
    store = tmp_path / "annotations.sqlite"
    owner.decide(store, "p1", owner.NEVER_USE, via="cli")

    owner.decide(store, "p1", owner.CLEAR_HOLD, via="web")

    assert excluded(store, "p1") == set()
    assert owner.decisions(store) == {"p1": owner.CLEAR_HOLD}


def test_forgetting_a_decision_hands_the_picture_back_to_the_app(tmp_path):
    store = tmp_path / "annotations.sqlite"
    owner.decide(store, "p1", owner.NEVER_USE, via="cli")

    owner.forget(store, "p1")

    assert owner.decisions(store) == {}
    assert excluded(store, "p1") == set()


def test_a_live_photo_is_one_picture_its_clip_goes_with_its_still(tmp_path):
    store = tmp_path / "annotations.sqlite"

    written = owner.decide(store, "still", owner.CLEAR_HOLD, via="web", clip_id="clip")

    assert written == ("still", "clip")
    assert owner.decisions(store, ["clip"]) == {"clip": owner.CLEAR_HOLD}


def test_the_store_finds_the_clip_of_a_live_photo_it_banked(tmp_path):
    import sqlite3

    store = empty_store(tmp_path)
    with sqlite3.connect(store) as connection:
        connection.execute(
            "INSERT INTO assets (asset_id, live_photo_video_id) VALUES ('still', 'clip')"
        )

    assert owner.decide(store, "still", owner.NEVER_USE, via="cli") == ("still", "clip")


def test_a_library_with_no_store_yet_has_no_decisions(tmp_path):
    assert owner.decisions(tmp_path / "missing.sqlite") == {}


# The audience gate, on every preparation tier.

import pytest  # noqa: E402

from immich_memories.analysis.editorial_shareability_tiers import audience_check_for  # noqa: E402
from immich_memories.analysis.editorial_structure_audience import (  # noqa: E402
    AudienceBank,
    AudienceGate,
)
from tests.test_editorial_shareability_tiers import Annotation, RefusingJudge  # noqa: E402

UNIT = {"asset_id": "solo", "members": ["solo"]}
FLAGGED_BY_THE_HEAD = {"solo": Annotation("A person on a beach.", (("nsfw_marqo", "yes"),))}


def empty_store(tmp_path):
    store = tmp_path / "annotations.sqlite"
    owner.forget(store, "nobody")
    return store


def gate(tmp_path, store, *, tier="full", audience="shareable"):
    return AudienceGate(
        RefusingJudge(),
        audience=audience,
        annotations=FLAGGED_BY_THE_HEAD,
        flag_rows=share.load_flags(store, ["solo"]),
        lines={"solo": "A person on a beach."},
        bank_path=tmp_path / "shareability.private.json",
        library=AudienceBank(tmp_path / "audience.private.json", answerer=f"{tier}|reader"),
        check_audience=audience_check_for(tier),
    )


@pytest.mark.parametrize("tier", ["full", "no_captions", "metadata_only"])
def test_a_head_hold_stands_until_the_owner_clears_that_picture(tmp_path, tier):
    store = empty_store(tmp_path)
    assert gate(tmp_path / "before", store, tier=tier).verdict_of(UNIT) != "share"

    owner.decide(store, "solo", owner.CLEAR_HOLD, via="cli")
    verdict = gate(tmp_path / "after", store, tier=tier)

    assert verdict.verdict_of(UNIT) == "share"
    assert verdict.verdicts["solo"]["finding"] == "owner_cleared"


# Whole cuts: the model's route and the no-model draft.

from dataclasses import replace  # noqa: E402

from immich_memories.analysis.editorial_structure_contract import (  # noqa: E402
    StructurePlannerPorts,
)
from immich_memories.analysis.editorial_structure_planner import plan_structure  # noqa: E402
from tests.editorial_story_fixtures import ControlledStoryJudge  # noqa: E402
from tests.test_editorial_duration_planner_integration import source  # noqa: E402


def with_owner(captured, store):
    return replace(captured, shareability_flags=share.load_flags(store, captured.assets))


def carried(captured, name, *, rules=False, laya=None):
    from immich_memories.analysis.editorial_rule_reader import NoModelJudge, RuleStructureReader

    ports = (
        StructurePlannerPorts(
            judge=NoModelJudge(),
            thumbnail_hash=lambda _: None,
            rules=RuleStructureReader(captured),
        )
        if rules
        else StructurePlannerPorts(
            judge=ControlledStoryJudge(), thumbnail_hash=lambda _: None, laya=laya
        )
    )
    plan = plan_structure(
        replace(captured, artifact_dir=captured.bank_dir.parent / name), ports
    ).plan
    return [row["asset_id"] for row in plan["carriers"]]


def test_a_picture_a_caption_read_refused_plays_once_the_owner_clears_it(tmp_path):
    from tests.editorial_thin_fixtures import caption_laya

    store = empty_store(tmp_path)
    captured = source(tmp_path, seconds=60, private_opening=True)
    assert "picture-000" not in carried(captured, "before", laya=caption_laya())

    owner.decide(store, "picture-000", owner.CLEAR_HOLD, via="web")

    assert "picture-000" in carried(with_owner(captured, store), "after", laya=caption_laya())


@pytest.mark.parametrize("rules", [False, True], ids=["model", "no-model"])
def test_a_picture_the_owner_will_never_use_leaves_every_cut(tmp_path, rules):
    store = empty_store(tmp_path)
    captured = source(tmp_path, seconds=60)
    first = carried(captured, "before", rules=rules)

    owner.decide(store, first[0], owner.NEVER_USE, via="web")
    again = carried(with_owner(captured, store), "after", rules=rules)

    assert first[0] not in again
    assert again, "the rest of the film still plays"


def test_the_no_model_draft_does_not_carry_a_banked_refusal_past_the_owner(tmp_path):
    from immich_memories.analysis.editorial_rule_banked_facts import open_banked_facts
    from immich_memories.analysis.editorial_structure_audience import AUDIENCE_BANK_NAME

    store = empty_store(tmp_path)
    bank_dir = tmp_path / "structure-banks" / "case"
    AudienceBank(bank_dir.parent / AUDIENCE_BANK_NAME, answerer="full|reader").hold(
        "held", {"verdict": "do_not_show", "finding": "exposure_evidence", "policy": "heads"}
    )

    def refused():
        return open_banked_facts(
            bank_dir=bank_dir,
            attempts_dir=None,
            store_path=store,
            audience="family",
            episode_cards={},
        ).refused_for_audience("held")

    assert refused()
    owner.decide(store, "held", owner.CLEAR_HOLD, via="cli")
    assert not refused()


def test_a_cleared_picture_stands_in_a_film_shared_outside_the_family(tmp_path):
    from immich_memories.analysis.editorial_rule_reader import RuleStructureReader

    store = empty_store(tmp_path)
    captured = source(tmp_path, seconds=60)
    line = captured.audience_annotations["picture-001"]
    flagged = replace(
        captured,
        audience="shareable",
        audience_annotations=captured.audience_annotations
        | {"picture-001": replace(line, heads=(("nsfw_marqo", "yes"),))},
    )
    assert RuleStructureReader(flagged).standing("picture-001") == 0

    owner.decide(store, "picture-001", owner.CLEAR_HOLD, via="cli")

    assert RuleStructureReader(with_owner(flagged, store)).standing("picture-001") > 0


def test_a_decision_is_not_a_fact_about_the_picture_so_no_reader_sees_it(tmp_path):
    """The editorial line feeds every reading and its evidence key. The owner's decision acts
    on the gate and the material, so a clearance neither re-asks a reading nor reaches a
    prompt."""
    from immich_memories.store.asset_annotations import AssetAnnotationFactRepository

    store = empty_store(tmp_path)
    owner.decide(store, "p1", owner.NEVER_USE, via="cli")
    repository = AssetAnnotationFactRepository(
        store, description_model="m", head_versions={}, pixel_producer_key="px"
    )

    (facts,) = repository.facts_for(("p1",)).facts

    assert facts.flags == ()
