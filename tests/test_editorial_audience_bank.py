"""An audience verdict is asked once per library, and a hold outlives every later answer."""

from dataclasses import replace

from immich_memories.analysis import editorial_shareability as share
from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
from immich_memories.analysis.editorial_structure_planner import plan_structure
from immich_memories.config_loader import Config
from tests.editorial_story_fixtures import ControlledStoryJudge
from tests.test_editorial_duration_planner_integration import source
from tests.test_editorial_terminal_body_hold import record


def cut(captured, judge, name, observe=None):
    """One cut in its own attempt folder, beside the same library banks, as a new run makes."""
    return plan_structure(
        replace(captured, artifact_dir=captured.bank_dir.parent / name),
        StructurePlannerPorts(
            judge=judge,
            thumbnail_hash=lambda _: None,
            rank=lambda _query, documents: dict.fromkeys(range(len(documents)), 1.0),
            reranker_identity={"endpoint": "test://local", "model": "controlled-ranker"},
            observe_picture=observe,
        ),
    ).plan


def audience_questions(judge):
    return [row["stage"] for row in judge.calls if row["stage"].startswith("shareability-")]


def test_a_second_cut_over_the_same_pictures_asks_no_audience_question_it_answered(tmp_path):
    captured = source(tmp_path, seconds=60)
    first, second = ControlledStoryJudge(), ControlledStoryJudge()

    first_plan = cut(captured, first, "first-cut")
    second_plan = cut(captured, second, "second-cut")

    assert audience_questions(first), "the first cut had to ask"
    assert audience_questions(second) == []
    assert second_plan["carriers"] == first_plan["carriers"]


def carried(plan):
    return {row["asset_id"] for row in plan["carriers"]}


def test_a_banked_hold_is_not_lifted_by_a_later_cut_that_would_clear_it(tmp_path):
    # The first cut reads a bath and refuses it for a sendable film. The second reads the same
    # picture as ordinary furniture moving: new evidence, a new question, and a clear answer.
    held = replace(source(tmp_path, seconds=60, private_opening=True), audience="sendable")
    cleared = replace(source(tmp_path, seconds=60), audience="sendable")
    elsewhere = replace(cleared, bank_dir=tmp_path / "other-library" / "banks")

    assert "picture-000" not in carried(cut(held, ControlledStoryJudge(), "first-cut"))
    assert "picture-000" in carried(cut(elsewhere, ControlledStoryJudge(), "fresh-library"))
    later = cut(cleared, ControlledStoryJudge(), "second-cut")

    assert "picture-000" not in carried(later)
    assert later["shareability"]["verdicts"]["picture-000"]["verdict"] != "share"


def test_an_answer_another_reader_gave_is_asked_again(tmp_path):
    captured = source(tmp_path, seconds=60)
    other_reader = replace(
        captured,
        config=Config(llm={"model": "another-reader"}, editorial={"preparation": {"tier": "full"}}),
    )
    cut(captured, ControlledStoryJudge(), "first-cut")
    judge = ControlledStoryJudge()

    cut(other_reader, judge, "second-cut")

    assert audience_questions(judge), "a bank written for one reader answers only for that one"


class ClearingJudge(ControlledStoryJudge):
    """A newer audience prompt that reads every picture as ordinary."""

    def answer(self, stage, prompt):
        if stage.startswith("shareability-"):
            return '{"finding": "none", "why": "Ordinary clothed activity"}'
        return super().answer(stage, prompt)


def bump_audience_prompt(monkeypatch):
    # WHY: a release that rewrites the audience prompt is the event under test; no fixture ships two.
    monkeypatch.setattr(share, "AUDIENCE_PROMPT_VERSION", share.AUDIENCE_PROMPT_VERSION + "-next")


def body_observer(uncovered):
    """Direct body observations, the witness a terminal hold is cast on."""
    return lambda asset_id: record(asset_id, "yes" if asset_id in uncovered else "no")


def test_a_text_model_hold_from_an_older_audience_prompt_is_asked_again_and_can_clear(
    tmp_path, monkeypatch
):
    held = replace(source(tmp_path, seconds=60, private_opening=True), audience="sendable")
    assert "picture-000" not in carried(cut(held, ControlledStoryJudge(), "first-cut"))
    bump_audience_prompt(monkeypatch)
    judge = ClearingJudge()

    later = cut(held, judge, "second-cut")

    assert audience_questions(judge), "the new prompt asks again"
    assert "picture-000" in carried(later)
    again = cut(held, ControlledStoryJudge(), "third-cut")
    assert "picture-000" in carried(again), "the new answer replaced the old hold"


def test_a_body_hold_from_an_older_audience_prompt_stays(tmp_path, monkeypatch):
    held = replace(source(tmp_path, seconds=60), audience="sendable")
    first = cut(held, ControlledStoryJudge(), "first-cut", observe=body_observer({"picture-000"}))
    assert first["shareability"]["verdicts"]["picture-000"]["verdict"] != "share"
    bump_audience_prompt(monkeypatch)

    later = cut(held, ClearingJudge(), "second-cut", observe=body_observer(set()))

    assert "picture-000" not in carried(later)


def _head_flagged_still():
    from tests.test_editorial_shareability_tiers import Annotation

    return share.evidence_for_unit(
        {"asset_id": "still", "members": ["still"]},
        {"still": Annotation("A fully clothed family waves.", (("nsfw_marqo", "yes"),))},
        {},
        {},
    )


def test_a_banked_clearance_of_a_detector_hold_is_not_served(tmp_path):
    """An answer banked before the ruling cleared a flagged still; the hold stands over it."""
    from types import SimpleNamespace

    from immich_memories.analysis.editorial_structure_audience import AudienceBank, AudienceGate

    evidence = _head_flagged_still()
    library = AudienceBank(tmp_path / "bank.json", answerer="reader")
    library.keep(
        share.audience_check_key(evidence), {"parsed": True, "verdict": "share", "finding": "none"}
    )
    gate = AudienceGate(
        SimpleNamespace(calls=[]),
        audience="sendable",
        picture_evidence=None,
        flag_rows={},
        lines={},
        bank_path=tmp_path / "shareability.json",
        library=library,
    )

    _key, answered = gate.check(evidence)

    assert answered["verdict"] == "family_only" and answered["finding"] == "exposure_evidence"


def test_a_detector_hold_is_permanent_across_audience_prompts(tmp_path, monkeypatch):
    from immich_memories.analysis.editorial_structure_audience import AudienceBank
    from tests.test_editorial_shareability_tiers import ClearingReader

    held = share.check_audience(ClearingReader(), _head_flagged_still(), "unit-1")
    AudienceBank(tmp_path / "bank.json", answerer="reader").hold("still", held)
    bump_audience_prompt(monkeypatch)

    standing = AudienceBank(tmp_path / "bank.json", answerer="reader").held("still")

    assert standing is not None and standing["verdict"] == "family_only"
    assert standing["finding"] == "exposure_evidence"
