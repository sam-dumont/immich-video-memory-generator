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
    verdict = gate(tmp_path, "sendable").verdict_of(UNIT)

    assert not share.allowed(verdict, "sendable")


def test_turning_it_off_lets_the_text_clear_the_flag_as_before(tmp_path):
    verdict = gate(tmp_path, "sendable", strict=False).verdict_of(UNIT)

    assert verdict == "share"


def test_a_family_film_is_unchanged(tmp_path):
    strict = gate(tmp_path / "on", "family").verdict_of(UNIT)
    relaxed = gate(tmp_path / "off", "family", strict=False).verdict_of(UNIT)

    assert strict == relaxed == "share"


def test_the_strict_hold_is_not_banked_so_turning_it_off_later_restores_the_share(tmp_path):
    gate(tmp_path, "sendable").verdict_of(UNIT)

    assert gate(tmp_path, "sendable", strict=False).verdict_of(UNIT) == "share"


def test_an_unflagged_picture_is_shared_either_way(tmp_path):
    clean = AudienceGate(
        Judge(),
        audience="sendable",
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
    from tests.test_editorial_duration_planner_integration import source

    captured = replace(source(tmp_path, seconds=24, pictures=4), audience="sendable")
    captured.config.editorial.strict_sharing = strict
    marked = {
        "picture-000": (share.FlagRow("picture-000", "review", "exposure=partial", "exposure"),)
    }
    captured = replace(captured, shareability_flags=marked)

    class CoverageJudge(ControlledStoryJudge):
        # WHY: the text reader; it reads every flagged person as clothed, the answer strict
        # sharing must not let clear a shared film.
        def answer(self, stage, prompt):
            if "-exposure-" in stage:
                return '{"observations":{"p1":[["a person","clothing"]]}}'
            return super().answer(stage, prompt)

    return plan_structure(
        captured, StructurePlannerPorts(judge=CoverageJudge(), thumbnail_hash=lambda _: None)
    ).plan


def test_the_setting_reaches_a_shared_film_through_the_planner(tmp_path):
    strict = _film(tmp_path / "on", strict=True)
    relaxed = _film(tmp_path / "off", strict=False)

    assert strict["shareability"]["verdicts"]["picture-000"]["finding"] == "strict_sharing"
    assert "picture-000" not in {row["asset_id"] for row in strict["carriers"]}
    assert relaxed["shareability"]["verdicts"]["picture-000"]["verdict"] == "share"
    assert "picture-000" in {row["asset_id"] for row in relaxed["carriers"]}
