"""An audience verdict is read once per library, and a hold outlives every later answer.

Laya, not a judge, answers the sharing question now (#1212): the properties below are the
same ones the library bank always guaranteed, observed through what Laya was asked instead of
through a judge's call log.
"""

from dataclasses import replace
from types import SimpleNamespace

from immich_memories.analysis import editorial_shareability as share
from immich_memories.analysis.editorial_laya_reader import LayaReader
from immich_memories.analysis.editorial_structure_audience import AudienceBank
from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
from immich_memories.analysis.editorial_structure_planner import plan_structure
from tests.editorial_story_fixtures import ControlledStoryJudge
from tests.editorial_thin_fixtures import caption_laya
from tests.test_editorial_duration_planner_integration import source


def cut(captured, judge, name, *, laya=None):
    """One cut in its own attempt folder, beside the same library banks, as a new run makes."""
    return plan_structure(
        replace(captured, artifact_dir=captured.bank_dir.parent / name),
        StructurePlannerPorts(judge=judge, thumbnail_hash=lambda _: None, laya=laya),
    ).plan


def carried(plan):
    return {row["asset_id"] for row in plan["carriers"]}


def test_rules_cut_reads_laya_without_an_llm_or_a_polish_step(tmp_path):
    from immich_memories.analysis.editorial_rule_reader import NoModelJudge, RuleStructureReader

    captured = replace(
        source(tmp_path, seconds=60, private_opening=True),
        owner_required_asset_ids=("picture-000",),
    )
    laya = caption_laya()
    judge = NoModelJudge()

    plan = plan_structure(
        captured,
        StructurePlannerPorts(
            judge=judge,
            rules=RuleStructureReader(captured),
            laya=laya,
            thumbnail_hash=lambda _: None,
        ),
    ).plan

    assert any("bathtub" in caption for caption in laya.scorer.states)
    assert plan["shareability"]["verdicts"]["picture-000"]["verdict"] == "just_us"
    assert "picture-000" not in carried(plan)
    assert not judge.calls


def test_a_second_cut_over_the_same_pictures_reads_no_caption_it_already_answered(tmp_path):
    captured = source(tmp_path, seconds=60)
    first_laya, second_laya = caption_laya(), caption_laya()

    first_plan = cut(captured, ControlledStoryJudge(), "first-cut", laya=first_laya)
    second_plan = cut(captured, ControlledStoryJudge(), "second-cut", laya=second_laya)

    assert first_laya.scorer.calls, "the first cut had to read the captions"
    assert second_laya.scorer.calls == 0, "the library bank already answered every one"
    assert second_plan["carriers"] == first_plan["carriers"]


class _BorderlineScorer:
    """# WHY: replaces the trained checkpoint with fixed probabilities near the threshold."""

    def probabilities(self, states, question):
        return [
            [
                0.18 if name == "bathing" else 0.82 if name == "none" else 0.0
                for name in question["criteria"]
            ]
            for _ in states
        ]


def test_tightening_the_threshold_rechecks_a_cached_clearance(tmp_path):
    captured = replace(source(tmp_path, seconds=60, private_opening=True), audience="shareable")
    first = cut(
        captured,
        ControlledStoryJudge(),
        "first",
        laya=LayaReader(_BorderlineScorer(), threshold=0.2, checkpoint_id="test-checkpoint"),
    )
    assert "picture-000" in carried(first)

    second = cut(
        captured,
        ControlledStoryJudge(),
        "second",
        laya=LayaReader(_BorderlineScorer(), threshold=0.15, checkpoint_id="test-checkpoint"),
    )

    assert second["shareability"]["verdicts"]["picture-000"]["verdict"] == "just_us"
    assert "picture-000" not in carried(second)


def test_replacing_checkpoint_files_rechecks_a_cached_clearance(tmp_path, monkeypatch):
    import sys

    from immich_memories.analysis import editorial_laya_reader as laya
    from immich_memories.config_models_editorial import EditorialConfig

    archive = tmp_path / "laya.tar"
    archive.touch()
    checkpoint = tmp_path / "laya"
    checkpoint.mkdir()
    weights = checkpoint / "model.safetensors"
    weights.write_bytes(b"clear")

    class Scorer(_BorderlineScorer):
        # WHY: replaces only the MLX boundary; the real factory fingerprints the files.
        def __init__(self, path):
            self.reader = (
                _ClearingScorer()
                if (path / "model.safetensors").read_bytes() == b"clear"
                else _BorderlineScorer()
            )

        def probabilities(self, states, question):
            return self.reader.probabilities(states, question)

    # WHY: CI has no Apple runtime or trained model; use the checkpoint stand-in above.
    monkeypatch.setitem(sys.modules, "laya_mlx", SimpleNamespace())
    monkeypatch.setattr(laya, "MlxLayaScorer", Scorer)
    config = EditorialConfig(
        laya_audience=True, laya_checkpoint=str(archive), laya_audience_threshold=0.15
    )
    captured = replace(source(tmp_path, seconds=60, private_opening=True), audience="shareable")
    first = cut(captured, ControlledStoryJudge(), "first", laya=laya.laya_reader_for(config))
    assert "picture-000" in carried(first)
    weights.write_bytes(b"holds")

    second = cut(captured, ControlledStoryJudge(), "second", laya=laya.laya_reader_for(config))

    assert "picture-000" not in carried(second)


def test_a_banked_hold_is_not_lifted_by_a_later_cut_that_would_clear_it(tmp_path):
    # The first cut reads a bath and refuses it for a shareable film. The second reads the same
    # picture as ordinary furniture moving: new evidence, a new reading, and a clear answer.
    held = replace(source(tmp_path, seconds=60, private_opening=True), audience="shareable")
    cleared = replace(source(tmp_path, seconds=60), audience="shareable")
    elsewhere = replace(cleared, bank_dir=tmp_path / "other-library" / "banks")

    assert "picture-000" not in carried(
        cut(held, ControlledStoryJudge(), "first-cut", laya=caption_laya())
    )
    assert "picture-000" in carried(
        cut(elsewhere, ControlledStoryJudge(), "fresh-library", laya=caption_laya())
    )
    later = cut(cleared, ControlledStoryJudge(), "second-cut", laya=caption_laya())

    assert "picture-000" not in carried(later)
    assert later["shareability"]["verdicts"]["picture-000"]["verdict"] != "share"


def test_an_answer_another_reader_gave_is_asked_again(tmp_path):
    """The per-evidence-key cache is scoped to who answered: Laya and the rules reader at the
    same evidence key never read each other's row, so switching readers reads fresh."""
    evidence = share.evidence_for_unit(
        {"asset_id": "picture-000", "members": ["picture-000"]},
        {},
        {},
        {"picture-000": "2020-05-02T08:00:00 | A person is bathing in a bathtub."},
    )
    key = share.audience_check_key(evidence)
    path = tmp_path / "bank.json"
    laya_bank = AudienceBank(path, answerer="full|laya")
    laya_bank.keep(
        key, {"parsed": True, "verdict": "just_us", "finding": "private_activity", "activity": {}}
    )

    rules_bank = AudienceBank(path, answerer="full|rules")

    assert rules_bank.answer(key) is None, "a bank written for one reader answers only for that one"


def bump_audience_prompt(monkeypatch):
    # WHY: a release that rewrites the audience prompt is the event under test; no fixture ships two.
    monkeypatch.setattr(share, "AUDIENCE_PROMPT_VERSION", share.AUDIENCE_PROMPT_VERSION + "-next")


class _ClearingScorer:
    """# WHY: replaces Laya (an MLX checkpoint, not installed in CI) with a stand-in for a
    checkpoint retrained under a newer prompt: it reads every caption as ordinary."""

    def probabilities(self, states, question):
        names = list(question["criteria"])
        return [[1.0 if name == "none" else 0.0 for name in names] for _ in states]


def test_a_text_hold_from_an_older_audience_prompt_is_asked_again_and_can_clear(
    tmp_path, monkeypatch
):
    held = replace(source(tmp_path, seconds=60, private_opening=True), audience="shareable")
    assert "picture-000" not in carried(
        cut(held, ControlledStoryJudge(), "first-cut", laya=caption_laya())
    )
    bump_audience_prompt(monkeypatch)
    clearing = LayaReader(_ClearingScorer(), threshold=0.186, checkpoint_id="test-checkpoint")

    later = cut(held, ControlledStoryJudge(), "second-cut", laya=clearing)

    assert "picture-000" in carried(later), "the new prompt is asked again and clears it"
    again = cut(
        held,
        ControlledStoryJudge(),
        "third-cut",
        laya=LayaReader(_ClearingScorer(), threshold=0.186, checkpoint_id="test-checkpoint"),
    )
    assert "picture-000" in carried(again), "the new answer replaced the old hold"


def test_a_body_hold_an_older_library_banked_stays(tmp_path, monkeypatch):
    """A film-time body observation once cast permanent holds. Pictures are no longer read at
    film time, so nothing casts a new one, and nothing lifts the ones already banked."""
    from immich_memories.analysis.editorial_structure_audience import (
        AUDIENCE_BANK_NAME,
        AudienceBank,
    )

    held = replace(source(tmp_path, seconds=60), audience="shareable")
    AudienceBank(held.bank_dir.parent / AUDIENCE_BANK_NAME, answerer="older").hold(
        "picture-000",
        {
            "verdict": "family_only",
            "parsed": True,
            "finding": "nudity_shirtless_or_underwear",
            "policy": "audience-evidence-v16",
        },
    )
    bump_audience_prompt(monkeypatch)

    later = cut(held, ControlledStoryJudge(), "second-cut")

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
        audience="shareable",
        annotations={},
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
