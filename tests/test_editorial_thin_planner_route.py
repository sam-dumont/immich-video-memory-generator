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
            rank=lambda _query, documents: dict.fromkeys(range(len(documents)), 1.0),
            reranker_identity={"endpoint": "test://local", "model": "controlled-ranker"},
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
