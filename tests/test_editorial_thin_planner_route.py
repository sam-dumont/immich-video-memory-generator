"""Which route a run takes: the polish when the period is catalogued, today's planner when not."""

from __future__ import annotations

import json
import re
from datetime import date

from immich_memories.analysis.editorial_rule_reader import RuleStructureReader
from immich_memories.analysis.editorial_structure_contract import StructurePlannerPorts
from immich_memories.analysis.editorial_structure_planner import plan_structure
from immich_memories.analysis.editorial_thin_layer import ThinPolish
from tests.editorial_film_fixtures import FilmJudge, film_source, home_days, trip_days

MAY = (date(2030, 5, 1), date(2030, 5, 31))
ACCOUNT = "A month of ordinary days and one week away."
# One shot of the draft, named by its own capture time: the second moment of the first day away.
JUNK = "2030-05-06T11"


class PolishJudge(FilmJudge):
    """The rules reader answers everything else; only the polish's own questions come here."""

    def answer(self, stage, prompt):
        if stage.startswith("thesis-fit-"):
            named = re.findall(rf"^(P\d+): .*{JUNK}", prompt, re.MULTILINE)
            return json.dumps({"weak": dict.fromkeys(named, "adds nothing")})
        if stage.startswith("story-pick-"):
            labels = re.findall(r"^(M\d{2}) \|", prompt, re.MULTILINE)
            return json.dumps({"keep": labels[:1]})
        if stage.startswith("shareability-batch-"):
            labels = re.findall(r"^(G\d{2}): ", prompt, re.MULTILINE)
            return json.dumps(
                {label: {"finding": "none", "why": "a family day"} for label in labels}
            )
        return super().answer(stage, prompt)


def film(tmp_path):
    days = [*home_days(date(2030, 5, 1), 4), *trip_days(date(2030, 5, 6), 6)]
    return film_source(tmp_path, days, seconds=60, span=MAY, pictures=2)


def run(source, judge, *, account):
    """The production planner, with the reader and the polish a thin run is given."""
    return plan_structure(
        source,
        StructurePlannerPorts(
            judge=judge,
            thumbnail_hash=lambda _asset: None,
            rules=RuleStructureReader(source),
            thin=ThinPolish(
                bank_dir=source.bank_dir,
                read_period=lambda _stories: (account, {}),
            ),
        ),
    ).plan


def audit_of(source):
    path = source.artifact_dir / "derived-decisions/thin-polish.private.json"
    return json.loads(path.read_text())


def test_a_catalogued_period_has_its_cut_voted_on_once(tmp_path):
    source = film(tmp_path)
    judge = PolishJudge()
    plan = run(source, judge, account=ACCOUNT)

    audit = audit_of(source)
    assert audit["ran"] is True
    assert audit["voted"] == audit["draft_shots"]
    assert any(call["stage"].startswith("thesis-fit-") for call in judge.calls)
    taken = [c["taken"] for c in plan["carriers"]]
    assert taken == sorted(taken)
    assert sum(c["seconds"] for c in plan["carriers"]) <= source.case.target_seconds


def test_a_period_with_no_account_falls_back_to_the_planner_and_asks_no_vote(tmp_path):
    source = film(tmp_path)
    judge = PolishJudge()
    plan = run(source, judge, account="")

    audit = audit_of(source)
    assert audit["ran"] is False
    assert audit["reason"] == "no catalogued account of this period"
    assert not any(call["stage"].startswith("thesis-fit-") for call in judge.calls)
    assert plan["carriers"]


def test_the_polish_takes_out_what_the_vote_named_and_leaves_the_rest_standing(tmp_path):
    source = film(tmp_path)
    plan = run(source, PolishJudge(), account=ACCOUNT)
    fallback = run(film(tmp_path / "plain"), PolishJudge(), account="")

    audit = audit_of(source)
    named = set(audit["removed_by_the_vote"])
    assert named
    assert not named & {c["asset_id"] for c in plan["carriers"]}
    # the rest of the draft is untouched: the polish removes, it does not re-plan
    assert named < {c["asset_id"] for c in fallback["carriers"]}


def test_a_batched_audience_asks_the_cut_in_batches_and_the_audit_counts_every_call(tmp_path):
    source = film(tmp_path)
    source.config.editorial.thin_batched_audience = True
    judge = PolishJudge()
    run(source, judge, account=ACCOUNT)

    stages = [call["stage"] for call in judge.calls]
    assert any(stage.startswith("shareability-batch-") for stage in stages)
    audit = audit_of(source)
    assert 0 < audit["calls"]["asked"] <= audit["calls"]["budget"]


def test_a_period_that_cannot_be_read_ships_the_no_model_cut_and_says_so(tmp_path, caplog):
    """The polish could not run, so the film is exactly the NAS cut, and the record says why."""
    from immich_memories.analysis.editorial_thin_layer import PeriodUnread

    def unreadable(_stories):
        # WHY: stands in for the reader boundary failing twice; the retry has its own test.
        raise PeriodUnread("could not read an account of 2030-05: the reader is down")

    source = film(tmp_path)
    with caplog.at_level("WARNING"):
        plan = plan_structure(
            source,
            StructurePlannerPorts(
                judge=PolishJudge(),
                thumbnail_hash=lambda _asset: None,
                rules=RuleStructureReader(source),
                thin=ThinPolish(bank_dir=source.bank_dir, read_period=unreadable),
            ),
        ).plan
    nas = film(tmp_path / "nas")
    nas_plan = plan_structure(
        nas,
        StructurePlannerPorts(
            judge=PolishJudge(), thumbnail_hash=lambda _asset: None, rules=RuleStructureReader(nas)
        ),
    ).plan

    audit = audit_of(source)
    assert audit["ran"] is False
    assert "the reader is down" in audit["reason"]
    assert [r.message for r in caplog.records if "polish did not run" in r.message]
    assert [c["asset_id"] for c in plan["carriers"]] == [
        c["asset_id"] for c in nas_plan["carriers"]
    ]
    assert (source.artifact_dir / "derived-decisions/unvouched-filler.private.json").is_file()


class NamesEverything(PolishJudge):
    """# WHY: a thesis-fit vote that names every shot in both orders, the worst the model can do."""

    def answer(self, stage, prompt):
        if stage.startswith("thesis-fit-"):
            named = re.findall(r"^(P\d+): ", prompt, re.MULTILINE)
            return json.dumps({"weak": dict.fromkeys(named, "adds nothing")})
        return super().answer(stage, prompt)


def test_a_film_that_promises_every_partition_a_voice_keeps_it_through_the_polish(tmp_path):
    """Lifetime films (09-24): the vote took a year's only shot, and both shots of another."""
    from dataclasses import replace

    from immich_memories.analysis.editorial_intent import IntentPartition

    weeks = tuple(
        IntentPartition(f"week-{n}", date(2030, 5, 1 + 7 * n), date(2030, 5, 7 + 7 * n), True)
        for n in range(4)
    )
    source = film(tmp_path)
    source = replace(
        source, intent=replace(source.intent, partitions=weeks, voice_per_partition=True)
    )

    def week_of(asset: str) -> str | None:
        part = source.intent.partition_for(source.assets[asset].file_created_at.date())
        return part.key if part else None

    plan = run(source, NamesEverything(), account=ACCOUNT)

    audit = audit_of(source)
    voiced_by_the_draft = {week_of(asset) for asset in audit["verdicts"]} - {None}
    assert voiced_by_the_draft <= {week_of(c["asset_id"]) for c in plan["carriers"]}
    assert any("of week-" in verdict["held_by"] for verdict in audit["verdicts"].values())
    assert any(c.get("review_stage") == "thin-polish" for c in plan["cut_carriers"])


class NamesOneClip(PolishJudge):
    """# WHY: a thesis-fit vote that names one drafted clip in both orders."""

    def answer(self, stage, prompt):
        if stage.startswith("thesis-fit-"):
            named = re.findall(r"^(P\d+): .*2030-05-07T11", prompt, re.MULTILINE)
            return json.dumps({"weak": dict.fromkeys(named, "adds nothing")})
        return super().answer(stage, prompt)


def test_a_shot_the_vote_removes_is_refilled_inside_the_films_real_length(tmp_path):
    """Feb 2024 and the 2024 year (09-24): the rules draft filled the length the render timing
    gives it, the polish measured its room against a rougher reserve the draft had already
    passed, and so it removed a shot and never opened a seat to refill it."""
    from dataclasses import replace

    from immich_memories.api.models import AssetType
    from immich_memories.processing.editorial_timing import build_editorial_timing_policy

    source = film(tmp_path)
    source = replace(
        source,
        assets={
            key: asset.model_copy(update={"type": AssetType.VIDEO, "duration_seconds": 12.0})
            for key, asset in source.assets.items()
        },
    )
    source = replace(
        source,
        render_timing=build_editorial_timing_policy(
            config=source.config,
            target_seconds=60,
            memory_type=source.case.product,
            transition="crossfade",
        ),
    )

    plan = run(source, NamesOneClip(), account=ACCOUNT)

    audit = audit_of(source)
    assert len(audit["removed_by_the_vote"]) == 1
    assert [slot["outcome"] for slot in audit["slots"]] == ["seated"]
    assert len(plan["carriers"]) == audit["draft_shots"]
    # the polish measured the film against the length finishing holds it to
    assert audit["content_cap"] == plan["content_cap_seconds"]
