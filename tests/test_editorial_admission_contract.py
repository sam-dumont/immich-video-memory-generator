"""Physical output limits do not reclassify unchanged event evidence."""

from dataclasses import replace

import pytest

from immich_memories.analysis.editorial_intent import IntentPartition, build_editorial_intent
from tests.editorial_story_fixtures import ControlledStoryJudge
from tests.test_editorial_duration_planner_integration import run
from tests.test_editorial_on_this_day_year_limit import make_source, ranges


def worthy_calls(judge):
    return [(c["stage"], c["prompt"]) for c in judge.calls if c["stage"].startswith("worthy-")]


def test_otd_admission_renders_original_occurrences_without_final_year_limit():
    windows = ranges((2032, 2030, 2030))
    intent = build_editorial_intent("on_this_day", windows, brief="Annual memory")
    legacy = replace(
        intent,
        partitions=tuple(
            IntentPartition(
                f"occurrence-{i}-{r.start.date().isoformat()}", r.start.date(), r.end.date(), True
            )
            for i, r in enumerate(sorted(windows, key=lambda r: r.start), 1)
        ),
        max_carriers_per_partition=None,
    )
    assert intent.admission_prompt_block(windows) == legacy.prompt_block()
    assert "selection limit:" not in intent.admission_prompt_block(windows)
    assert "at most 1 selected carrier per calendar year" in intent.prompt_block()
    assert [p.key for p in intent.partitions] == ["year-2030", "year-2032"]
    assert intent.identity() != legacy.identity()


def test_real_planner_budget_only_changes_leave_both_admission_ballots_and_tiers_equal(tmp_path):
    source = make_source(tmp_path / "limited")
    limited_judge = ControlledStoryJudge()
    limited = run(source, limited_judge)
    unlimited_source = replace(
        source,
        intent=replace(source.intent, max_carriers_per_partition=None),
        bank_dir=tmp_path / "unlimited" / "banks",
        artifact_dir=tmp_path / "unlimited" / "plan",
    )
    unlimited_judge = ControlledStoryJudge()
    unlimited = run(unlimited_source, unlimited_judge)
    assert worthy_calls(limited_judge) == worthy_calls(unlimited_judge)
    assert {stage.rsplit("-", 1)[-1] for stage, _ in worthy_calls(limited_judge)} == {
        "source",
        "hashed",
    }
    assert limited["tiers"] == unlimited["tiers"]
    assert limited["contract_key"] != unlimited["contract_key"]
    assert len(limited["carriers"]) == 3
    assert all(b == 1 for b in limited["intent_report"]["coverage"].values())


def test_real_planner_changed_observed_evidence_changes_admission_requests(tmp_path):
    source = make_source(tmp_path / "first")
    first = ControlledStoryJudge()
    run(source, first)
    changed_wall = source.wall_bytes.replace(b"People play games.", b"People race boats.")
    assert changed_wall != source.wall_bytes
    changed = replace(
        source,
        wall_bytes=changed_wall,
        bank_dir=tmp_path / "changed" / "banks",
        artifact_dir=tmp_path / "changed" / "plan",
    )
    second = ControlledStoryJudge()
    run(changed, second)
    assert worthy_calls(first) != worthy_calls(second)


def test_other_products_keep_their_exact_full_admission_contract():
    for product in (
        "then_and_now",
        "holiday",
        "person_spotlight",
        "person_lifetime",
        "person_birthday",
        "multi_person",
        "trip",
        "monthly_highlights",
        "year_in_review",
        "special_day",
        "album",
        "custom",
    ):
        windows = ranges((2030, 2032))
        intent = build_editorial_intent(
            product, windows, brief="The requested memory", people=("Person",)
        )
        assert intent.admission_prompt_block(windows) == intent.prompt_block()


def test_otd_admission_requires_the_actual_occurrence_scope():
    intent = build_editorial_intent("on_this_day", ranges((2030,)), brief="Annual memory")
    with pytest.raises(ValueError, match="captured occurrence"):
        intent.admission_prompt_block(())
