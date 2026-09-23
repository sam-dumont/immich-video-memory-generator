"""Exercise requested depth through the real planner with controlled editorial judgments."""

import json
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from immich_memories.analysis.annotation_lines import AssetAnnotationLine
from immich_memories.analysis.editorial_case import Case, _adapt_production_cards
from immich_memories.analysis.editorial_contracts import EditorialCandidate
from immich_memories.analysis.editorial_intent import build_editorial_intent
from immich_memories.analysis.editorial_moment_wall import (
    MomentCardEvidence,
    ProductionMomentWallRenderer,
    RepresentativeEvidence,
)
from immich_memories.analysis.editorial_people import adapt_editorial_people
from immich_memories.analysis.editorial_structure_contract import (
    EpisodeReadingCard,
    StructurePlannerPorts,
    StructurePlanningInput,
)
from immich_memories.analysis.editorial_structure_planner import plan_structure
from immich_memories.analysis.moment_cards import MomentCard
from immich_memories.analysis.selection_source_groups import EditorialGroup
from immich_memories.api.models import AssetType
from immich_memories.config_loader import Config
from immich_memories.timeperiod import DateRange
from tests.conftest import make_asset


def reading_cards(aliases, cards):
    """What the banked 90-minute episode reading hands each moment of a captured wall."""
    return {
        alias: EpisodeReadingCard(
            episode_id=card.episode_id,
            evidence_key=f"evidence-{card.episode_id}",
            what_happened=card.evidence.episode_meaning,
            representative_asset_ids=card.representative_asset_ids,
            cache_hit=False,
        )
        for alias, card in zip(aliases, cards, strict=True)
    }


def source(tmp_path, *, seconds, pictures=50, private_opening=False):
    start = datetime(2020, 5, 2, 8, tzinfo=UTC)
    candidates = []
    annotations = {}
    for index in range(pictures):
        asset = make_asset(
            f"picture-{index:03d}",
            duration=None,
            file_created_at=start + timedelta(minutes=10 * index),
        )
        asset.type = AssetType.IMAGE
        description = f"A clothed person carries furniture during moving step {index}."
        if private_opening and index == 0:
            description = "A person is bathing in a bathtub."
        annotations[asset.id] = AssetAnnotationLine(
            asset.id,
            f"{asset.file_created_at.isoformat()} | {description}",
            description=description,
            heads=(("nsfw_marqo", "no"),),
        )
        candidates.append(
            EditorialCandidate(
                asset_id=asset.id,
                taken_at=asset.file_created_at,
                media_kind="photo",
                live_photo_stitch_member_ids=(),
                rendering_family_id=None,
                favourite=False,
                source=asset,
                shippable_duration=0,
                grounded_annotations=(),
            )
        )
    group = EditorialGroup("moving-moment", tuple(candidates))
    episode = EditorialGroup("moving-episode", tuple(candidates))
    card = MomentCard(
        moment_id=group.group_id,
        episode_id=episode.group_id,
        full_asset_ids=group.candidate_ids,
        selectable_asset_ids=group.candidate_ids,
        representative_asset_ids=group.candidate_ids[:1],
        text="Moving home",
        evidence=MomentCardEvidence(
            episode_meaning="Packing furniture and moving into a new home.",
            representatives=(
                RepresentativeEvidence(
                    "People move their furniture into a new home.",
                    "Shows the move unfolding.",
                ),
            ),
            annotations=(("activity", "working"),),
        ),
    )
    prepared = SimpleNamespace(
        moment_groups=(group,),
        episode_groups=(episode,),
        candidates=tuple(candidates),
    )
    adapted, _ = _adapt_production_cards(prepared, (card,))
    wall = ProductionMomentWallRenderer(prepared, (card,), adapt_editorial_people({})).render(
        adapted
    )
    case = Case(
        "moving-month",
        "A month containing a move",
        "monthly_highlights",
        (DateRange(datetime(2020, 5, 1, tzinfo=UTC), datetime(2020, 5, 31, tzinfo=UTC)),),
        seconds,
        "Show the month through worthwhile events and their development.",
    )
    return StructurePlanningInput(
        case=case,
        intent=build_editorial_intent(case.product, case.ranges, brief=case.brief),
        # The model editor's fixture states the caption-fed tier: a blank install now
        # settles at no_captions, where the gate has no description to read.
        config=Config(editorial={"preparation": {"tier": "full"}}),
        wall_bytes=wall.text.encode(),
        moment_asset_ids={wall.aliases[0]: group.candidate_ids},
        assets={c.asset_id: c.source for c in candidates},
        annotations={key: row.text for key, row in annotations.items()},
        audience_annotations=annotations,
        gps={},
        pixel_facts={},
        shareability_flags={},
        motion_residuals={},
        lineage={},
        bank_dir=tmp_path / "banks",
        artifact_dir=tmp_path / f"plan-{seconds}",
        episode_readings=reading_cards(wall.aliases, (card,)),
    )


class EditorialJudge:
    """Every offered picture is useful; only the production mechanics can shorten this film."""

    def __init__(self, bank=None, *, require_hits=False):
        self.calls = []
        self.bank = {} if bank is None else bank
        self.require_hits = require_hits

    def ask(self, stage, prompt, max_tokens=260, *, accepts=None, **options):
        # `accepts` is the caller's own contract check, not part of what was asked.
        key = (prompt, max_tokens, tuple(sorted(options.items())))
        hit = key in self.bank
        if self.require_hits and not hit:
            pytest.fail(f"exact replay changed the {stage} request")
        if not hit:
            self.bank[key] = self.answer(stage, prompt)
        self.calls.append({"stage": stage, "prompt": prompt, "cache_hit": hit})
        return self.bank[key]

    @staticmethod
    def answer(stage, prompt):
        if stage.startswith("worthy-"):
            result = {"worthy": {"F01": "Moving home is a meaningful life change"}}
        elif stage.startswith("threads-"):
            result = {
                "claim1": "Moving home",
                "anchors1": ["F01"],
                "claim2": "",
                "anchors2": [],
                "claim3": "",
                "anchors3": [],
            }
        elif stage.startswith("synthesis"):
            result = {
                "threads": [{"claim": "Moving home", "anchors": ["F01"]}],
                "one_offs": [],
                "left_out": [],
            }
        elif stage == "structure":
            result = {
                "chapters": [
                    {
                        "chapter": "B01",
                        "share": 100,
                        "show": "Let the move unfold through its stages",
                    }
                ]
            }
        elif stage.startswith("ladder-"):
            labels = re.findall(r"^(U\d+|V\d+):", prompt, re.MULTILINE)
            assert labels, stage
            if labels[0].startswith("U"):
                limit = int(re.search(r"Then list up to (\d+)", prompt)[1])
                result = {
                    "primary": labels[0],
                    "why": "Opens the moving sequence",
                    "contributions": [
                        {"kind": "action", "unit": label, "why": "Shows a distinct moving step"}
                        for label in labels[1 : 1 + limit]
                    ],
                }
            else:
                limit = int(re.search(r"Name up to (\d+) MORE", prompt)[1])
                result = {
                    "contributions": [
                        {"kind": "action", "unit": label, "why": "Shows a distinct moving step"}
                        for label in labels[:limit]
                    ]
                }
        elif stage.startswith("shareability-"):
            result = (
                {"finding": "bathing", "why": "A person is bathing"}
                if "A person is bathing in a bathtub." in prompt
                else {"finding": "none", "why": "Ordinary clothed activity"}
            )
        elif stage.startswith(("review-funded-event", "review-whole-film")):
            result = {"schema_version": "sequence-cleanup-v1", "cut": []}
        elif stage.startswith("reference-entailment"):
            schema = re.search(r'"schema_version"\s*:\s*"([^"]+)"', prompt)[1]
            evidence = {
                "participants": [{"label": "clothed person", "quote": "A clothed person"}],
                "action": {"label": "carries furniture", "quote": "carries furniture"},
            }
            result = {
                "schema_version": schema,
                "pair_id": "P01",
                "remove": evidence,
                "reference": evidence,
                "same_people": True,
                "same_action": True,
                "interchangeable": True,
                "reason": "The same person and moving action are shown",
            }
        elif stage.startswith("assembly-contributions"):
            aliases = list(dict.fromkeys(re.findall(r'"alias": "(R\d+)"', prompt)))
            result = {
                "schema_version": "assembly-contributions-v1",
                "decisions": [
                    {
                        "asset_id": alias,
                        "decision": "add",
                        "reason": "Develops another grounded part of the move",
                    }
                    for alias in aliases
                ],
            }
        else:
            pytest.fail(f"unexpected editorial stage {stage}")
        return json.dumps(result)


def run(source, judge):
    return plan_structure(
        source,
        StructurePlannerPorts(
            judge=judge,
            thumbnail_hash=lambda _: None,
            rank=lambda _query, documents: dict.fromkeys(range(len(documents)), 1.0),
            reranker_identity={"endpoint": "test://local", "model": "controlled-ranker"},
        ),
    ).plan


def asked_again(calls):
    """What a warm run over the same library asks: everything except the audience questions,
    which the library's audience bank answers before any request is made."""
    return [(c["stage"], c["prompt"]) for c in calls if not c["stage"].startswith("shareability-")]


def semantic_plan(plan):
    story = plan.get("story")
    if isinstance(story, dict) and isinstance(story.get("calls"), dict):
        # How many month pages were answered fresh is cache state, not a decision: a warm
        # replay asks the same questions and reads the same answers out of the bank.
        plan = {
            **plan,
            "story": {
                **story,
                "calls": {
                    key: value
                    for key, value in story["calls"].items()
                    if key != "story_pages_fresh"
                },
            },
        }
    share = plan.get("shareability")
    if isinstance(share, dict) and "judgment_requests" in share:
        # How many audience questions a run paid for is bank state too: the library bank
        # answers a warm replay's audience questions before any request is made.
        plan = {
            **plan,
            "shareability": {k: v for k, v in share.items() if k != "judgment_requests"},
        }
    telemetry = {
        "calls",
        "calls_by_stage",
        "llm_metrics",
        "reranker_metrics",
        "reranker_calls",
        "motion_metrics",
        "thumbnail_metrics",
        "picture_facts_metrics",
    }
    return {key: value for key, value in plan.items() if key not in telemetry}


def test_longer_requested_film_can_deepen_one_event_with_sufficient_approved_material(tmp_path):
    from tests.editorial_story_fixtures import ControlledStoryJudge

    short = run(source(tmp_path / "short", seconds=60), ControlledStoryJudge())
    long = run(source(tmp_path / "long", seconds=180), ControlledStoryJudge())
    assert 0 < len(short["carriers"]) < len(long["carriers"]) <= 50
    assert short["content_seconds"] < long["content_seconds"] <= long["content_cap_seconds"]
    assert len({row["asset_id"] for row in long["carriers"]}) == len(long["carriers"])
    assert long["duration_realization"]["status"] == "near_target"
    assert len({row["event"] for row in long["carriers"]}) == 1


def test_story_depth_replays_exact_requests_and_decisions(tmp_path):
    from tests.editorial_story_fixtures import ControlledStoryJudge

    cold_source = source(tmp_path, seconds=120)
    cold_judge = ControlledStoryJudge()
    cold = run(cold_source, cold_judge)
    warm_source = source(tmp_path, seconds=120)
    warm_judge = ControlledStoryJudge(cold_judge.bank, require_hits=True)
    warm = run(warm_source, warm_judge)
    assert semantic_plan(warm) == semantic_plan(cold)
    assert all(call["cache_hit"] for call in warm_judge.calls)


def test_private_initial_choice_is_replaced_by_grounded_depth_without_claiming_it_survived(
    tmp_path,
):
    from dataclasses import replace

    from tests.editorial_story_fixtures import ControlledStoryJudge

    judge = ControlledStoryJudge()
    captured = replace(source(tmp_path, seconds=60, private_opening=True), audience="sendable")
    plan = run(captured, judge)
    assert plan["carriers"]
    assert all(c["asset_id"] != "picture-000" for c in plan["carriers"])
    assert [row["asset_id"] for row in plan["shareability"]["tightened"]] == ["picture-000"]
    stood_in = {row["to"] for row in plan["shareability"]["substituted"]}
    assert stood_in and stood_in <= {c["asset_id"] for c in plan["carriers"]}
    # The story may know the occasion, but the shipped material never claims a
    # refused picture survived or borrows its description for another carrier.
    # A picture that stood in for a refused one describes itself, like any other.
    assert all("moving step" in row["line"] for row in plan["carriers"])
    assert all(
        "bathtub" not in row["line"] and "bathing" not in row["line"] for row in plan["carriers"]
    )
