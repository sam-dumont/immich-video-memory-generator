"""One model-chosen carrier per calendar year, before funding and through completion."""

import json
import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from immich_memories.analysis.annotation_lines import AssetAnnotationLine
from immich_memories.analysis.editorial_case import Case, _adapt_production_cards
from immich_memories.analysis.editorial_contracts import EditorialCandidate
from immich_memories.analysis.editorial_intent import build_editorial_intent
from immich_memories.analysis.editorial_intent_validation import CarrierView, validate_intent
from immich_memories.analysis.editorial_moment_wall import (
    MomentCardEvidence,
    ProductionMomentWallRenderer,
    RepresentativeEvidence,
)
from immich_memories.analysis.editorial_people import adapt_editorial_people
from immich_memories.analysis.editorial_structure_budget import partition_budgets
from immich_memories.analysis.editorial_structure_contract import StructurePlanningInput
from immich_memories.analysis.moment_cards import MomentCard
from immich_memories.analysis.selection_source import EditorialGroup
from immich_memories.api.models import AssetType
from immich_memories.config_loader import Config
from immich_memories.timeperiod import DateRange
from tests.conftest import make_asset
from tests.test_editorial_duration_planner_integration import EditorialJudge, run, semantic_plan


def ranges(years):
    return tuple(
        DateRange(datetime(y, 5, 2, tzinfo=UTC), datetime(y, 5, 2, 23, 59, 59, tzinfo=UTC))
        for y in years
    )


def make_source(tmp_path, *, years=(2030, 2031, 2032), product="on_this_day"):
    groups, episodes, cards, candidates, annotations = [], [], [], [], {}
    for year in years:
        for event in range(2):
            local = []
            for picture in range(3):
                taken = datetime(year, 5, 2, 8 + 8 * event, tzinfo=UTC) + timedelta(
                    minutes=10 * picture
                )
                asset = make_asset(
                    f"y{year}-e{event}-p{picture}", duration=None, file_created_at=taken
                )
                asset.type = AssetType.IMAGE
                description = f"A clothed person plays a game during distinct round {picture}."
                annotations[asset.id] = AssetAnnotationLine(
                    asset.id,
                    f"{taken.isoformat()} | {description} | activity=playing",
                    description=description,
                    heads=(("nsfw_marqo", "no"),),
                )
                local.append(
                    EditorialCandidate(
                        asset_id=asset.id,
                        taken_at=taken,
                        media_kind="photo",
                        live_photo_stitch_member_ids=(),
                        rendering_family_id=None,
                        favourite=False,
                        source=asset,
                        proposed_segment=None,
                        shippable_duration=0,
                        grounded_annotations=(),
                    )
                )
            group = EditorialGroup(f"moment-{year}-{event}", tuple(local))
            episode = EditorialGroup(f"episode-{year}-{event}", tuple(local))
            groups.append(group)
            episodes.append(episode)
            candidates.extend(local)
            cards.append(
                MomentCard(
                    moment_id=group.group_id,
                    episode_id=episode.group_id,
                    full_asset_ids=group.candidate_ids,
                    selectable_asset_ids=group.candidate_ids,
                    representative_asset_ids=group.candidate_ids[:1],
                    representative_reasons=("Shows an occasion.",),
                    text="Playing games",
                    evidence=MomentCardEvidence(
                        episode_meaning="People play games.",
                        representatives=(
                            RepresentativeEvidence("People play games.", "Shows an occasion."),
                        ),
                        annotations=(("activity", "working"),),
                    ),
                )
            )
    prepared = SimpleNamespace(
        moment_groups=tuple(groups), episode_groups=tuple(episodes), candidates=tuple(candidates)
    )
    adapted, _ = _adapt_production_cards(prepared, tuple(cards))
    wall = ProductionMomentWallRenderer(prepared, tuple(cards), adapt_editorial_people({})).render(
        adapted
    )
    case = Case(
        "annual-occurrences",
        "The same day through time",
        product,
        ranges(years),
        90,
        "Show worthwhile occasions across the years.",
    )
    return StructurePlanningInput(
        case=case,
        intent=build_editorial_intent(product, case.ranges, brief=case.brief),
        config=Config(),
        wall_bytes=wall.text.encode(),
        moment_asset_ids={
            alias: group.candidate_ids for alias, group in zip(wall.aliases, groups, strict=True)
        },
        assets={c.asset_id: c.source for c in candidates},
        annotations={k: v.text for k, v in annotations.items()},
        audience_annotations=annotations,
        gps={},
        pixel_facts={},
        shareability_flags={},
        motion_residuals={},
        period_reading={"thesis": "Different occasions across the years"},
        lineage={},
        bank_dir=tmp_path / "banks",
        artifact_dir=tmp_path / "plan",
    )


class AnnualJudge(EditorialJudge):
    def __init__(self, *args, empty_funding=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.empty_funding = empty_funding

    def answer(self, stage, prompt):
        if stage == "structure":
            labels = list(dict.fromkeys(re.findall(r"^B\d{2}", prompt, re.MULTILINE)))
            return json.dumps(
                {
                    "chapters": [
                        {
                            "chapter": a,
                            "share": 100 if i == 0 else 0,
                            "show": "Choose the strongest occasion in each year",
                        }
                        for i, a in enumerate(labels)
                    ]
                }
            )
        if stage == "structure-review":
            return '{"remove":[],"merge":[],"missing":""}'
        if stage.startswith("worthy-"):
            labels = list(dict.fromkeys(re.findall(r"F\d{2}", prompt)))
            return json.dumps({"worthy": dict.fromkeys(labels, "A meaningful occasion")})
        if stage.startswith(("threads-", "synthesis")):
            if stage.startswith("threads-"):
                labels = re.findall(r"^(F\d{2}):", prompt, re.MULTILINE)
                self.known_labels = labels
                return json.dumps(
                    {
                        "claim1": "Playing games together",
                        "anchors1": labels,
                        "claim2": "",
                        "anchors2": [],
                        "claim3": "",
                        "anchors3": [],
                    }
                )
            return json.dumps(
                {
                    "threads": [{"claim": "Playing games together", "anchors": self.known_labels}],
                    "one_offs": [],
                    "left_out": [],
                }
            )
        if stage.startswith("event-funding-"):
            events = json.JSONDecoder().raw_decode(
                prompt.split("AVAILABLE HAPPENINGS", 1)[1].split("\n", 1)[1]
            )[0]
            if self.empty_funding:
                return '{"events":[]}'
            return json.dumps(
                {
                    "events": [
                        {
                            "anchor": events[-1]["anchor"],
                            "pictures": 1,
                            "purpose": "Choose the later occasion for this year",
                        }
                    ]
                }
            )
        if stage.startswith("ladder-") and "completion-page" not in stage and "-page" not in stage:
            labels = re.findall(r"^(U\d+):", prompt, re.MULTILINE)
            return json.dumps(
                {
                    "primary": labels[-1],
                    "why": "The final game round is clearest",
                    "contributions": [],
                }
            )
        return super().answer(stage, prompt)


def test_calendar_year_groups_repeated_windows_and_limits_budget_without_spilling():
    policy = build_editorial_intent("on_this_day", ranges((2030, 2030, 2032)), brief="This day")
    assert [p.key for p in policy.partitions] == ["year-2030", "year-2032"]
    assert policy.max_carriers_per_partition == 1
    assert "at most 1 selected carrier per calendar year" in policy.prompt_block()
    assert partition_budgets(
        ["year-2030", "year-2031", "year-2032"],
        {"year-2030": 300, "year-2031": 0, "year-2032": 4},
        20,
        max_per_partition=policy.max_carriers_per_partition,
    ) == {
        "year-2030": 1,
        "year-2031": 0,
        "year-2032": 1,
    }


def test_model_chooses_event_and_picture_before_acquisition_and_warm_is_exact(tmp_path):
    from tests.editorial_story_fixtures import AnnualStoryJudge

    source = make_source(tmp_path)
    judge = AnnualStoryJudge()
    plan = run(source, judge)
    assert [c["asset_id"] for c in plan["carriers"]] == [f"y{y}-e1-p2" for y in (2030, 2031, 2032)]
    assert plan["intent_report"]["coverage"] == dict.fromkeys(
        (f"year-{y}" for y in (2030, 2031, 2032)), 1
    )
    assert sum(row["granted"] for row in plan["story"]["episodes"]) == 3
    assert len([c for c in judge.calls if c["stage"].startswith("moment-inventory")]) == 3
    assert len([c for c in judge.calls if c["stage"].startswith("story-pick-")]) >= 3
    # Equal descriptions may reuse an audience verdict; no unused event is read.
    assert 1 <= len([c for c in judge.calls if c["stage"].startswith("shareability-")]) <= 3
    assert not any(c["stage"].startswith("assembly-contributions") for c in judge.calls)
    assert plan["intent_report"]["violations"] == []
    warm_judge = AnnualStoryJudge(judge.bank, require_hits=True)
    warm = run(source, warm_judge)
    assert semantic_plan(warm) == semantic_plan(plan)
    assert all(c["cache_hit"] for c in warm_judge.calls)


def test_single_eligible_year_is_still_capped_and_insufficiency_is_reported(tmp_path):
    from tests.editorial_story_fixtures import AnnualStoryJudge

    plan = run(make_source(tmp_path, years=(2030,)), AnnualStoryJudge())
    assert len(plan["carriers"]) == 1
    assert plan["intent_report"]["coverage"] == {"year-2030": 1}
    assert plan["intent_report"]["status"] == "insufficient_material"


def test_validator_catches_excess_across_families_and_reports_missing_year():
    policy = build_editorial_intent("on_this_day", ranges((2030, 2031)), brief="This day")
    report = validate_intent(
        policy,
        carriers=[
            CarrierView("a", date(2030, 5, 2), "morning", 4),
            CarrierView("b", date(2030, 5, 2), "evening", 4),
        ],
        evidence_partitions={"year-2030", "year-2031"},
        requested_seconds=90,
    )
    assert report.status == "structural_violation"
    assert {v.code for v in report.violations} == {"partition_carrier_limit", "uncovered_partition"}


def test_incompatible_prior_fails_before_any_model_choice_instead_of_arbitrarily_dropping(tmp_path):
    source = make_source(tmp_path)
    prior = {
        "carriers": [
            {"asset_id": str(i), "event": f"E{i}", "taken": "2030-05-02T08:00:00+00:00"}
            for i in range(2)
        ]
    }
    judge = AnnualJudge()
    with pytest.raises(ValueError, match="prior plan exceeds"):
        run(replace(source, prior_plan=prior), judge)
    assert judge.calls == []


def test_holiday_and_other_products_keep_unlimited_partition_depth():
    for product in ("holiday", "year_in_review", "monthly_highlights", "person_spotlight"):
        policy = build_editorial_intent(product, ranges((2030, 2031)), brief="Original intent")
        assert policy.max_carriers_per_partition is None
        assert "selection limit:" not in policy.prompt_block()
    assert partition_budgets(["a", "b"], {"a": 300, "b": 4}, 20) == {"a": 16, "b": 4}
