"""With `strict_sharing` on, anything a detector or an exposure flag marked stays out of a
film shared outside the family, whatever the reader's text says. Family films are unchanged."""

from __future__ import annotations

from immich_memories.analysis import editorial_shareability as share
from immich_memories.analysis.editorial_structure_audience import AudienceBank, AudienceGate
from immich_memories.config_models_editorial import EditorialConfig
from tests.test_editorial_shareability_tiers import Annotation, ClearingReader

UNIT = {"asset_id": "solo", "members": ["solo"]}
# An exposure flag the caption coverage review clears: "a family" in "clothing".
FLAGGED = {"solo": (share.FlagRow("solo", "review", "exposure=partial", "exposure"),)}


class Judge(ClearingReader):
    calls: list = []


def gate(tmp_path, audience, *, strict=None):
    options = {} if strict is None else {"strict_sharing": strict}
    return AudienceGate(
        Judge(),
        audience=audience,
        annotations={"solo": Annotation("A family waves in a garden.")},
        flag_rows=FLAGGED,
        lines={"solo": "A family waves in a garden."},
        bank_path=tmp_path / "shareability.private.json",
        library=AudienceBank(tmp_path / "audience.private.json", answerer="full|model-a"),
        **options,
    )


def test_strict_sharing_is_on_by_default():
    assert EditorialConfig().strict_sharing is True


def test_a_flagged_picture_the_text_clears_stays_out_of_a_shared_film(tmp_path):
    verdict = gate(tmp_path, "shareable").verdict_of(UNIT)

    assert not share.allowed(verdict, "shareable")


def test_turning_it_off_lets_the_text_clear_the_flag_as_before(tmp_path):
    verdict = gate(tmp_path, "shareable", strict=False).verdict_of(UNIT)

    assert verdict == "share"


def test_a_family_film_is_unchanged(tmp_path):
    strict = gate(tmp_path / "on", "family").verdict_of(UNIT)
    relaxed = gate(tmp_path / "off", "family", strict=False).verdict_of(UNIT)

    assert strict == relaxed == "share"


def test_the_strict_hold_is_not_banked_so_turning_it_off_later_restores_the_share(tmp_path):
    gate(tmp_path, "shareable").verdict_of(UNIT)

    assert gate(tmp_path, "shareable", strict=False).verdict_of(UNIT) == "share"


def test_an_unflagged_picture_is_shared_either_way(tmp_path):
    clean = AudienceGate(
        Judge(),
        audience="shareable",
        annotations={"solo": Annotation("A landscape at sunset.")},
        flag_rows={},
        lines={"solo": "A landscape at sunset."},
        bank_path=tmp_path / "shareability.private.json",
        library=AudienceBank(tmp_path / "audience.private.json", answerer="full|model-a"),
    )

    assert clean.verdict_of(UNIT) == "share"


def _film(tmp_path, *, strict):
    from dataclasses import replace

    from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
    from immich_memories.analysis.editorial_structure_planner import plan_structure
    from tests.editorial_story_fixtures import ControlledStoryJudge
    from tests.editorial_thin_fixtures import caption_laya
    from tests.test_editorial_duration_planner_integration import source

    captured = replace(source(tmp_path, seconds=24, pictures=4), audience="shareable")
    captured.config.editorial.strict_sharing = strict
    marked = {
        "picture-000": (share.FlagRow("picture-000", "review", "exposure=partial", "exposure"),)
    }
    captured = replace(captured, shareability_flags=marked)
    judge = ControlledStoryJudge()
    plan = plan_structure(
        captured,
        StructurePlannerPorts(judge=judge, thumbnail_hash=lambda _: None, laya=caption_laya()),
    ).plan
    return plan, judge


def test_a_flagged_picture_stays_out_of_a_shared_film_and_no_llm_is_asked(tmp_path):
    """No caption can clear an exposure flag any more: nothing sends it to a reader."""
    for strict in (True, False):
        plan, judge = _film(tmp_path / str(strict), strict=strict)

        assert plan["shareability"]["verdicts"]["picture-000"]["verdict"] != "share"
        assert "picture-000" not in {row["asset_id"] for row in plan["carriers"]}
        assert not [c for c in judge.calls if c["stage"].startswith("shareability-")]
