"""Story-first selection runs inside the real planner and skips the beat/ladder machinery."""

import json
import re
from dataclasses import replace
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
    StructurePlanningInput,
)
from immich_memories.analysis.moment_cards import MomentCard
from immich_memories.analysis.selection_source_groups import EditorialGroup
from immich_memories.api.models import AssetType
from immich_memories.config_loader import Config
from immich_memories.timeperiod import DateRange
from tests.conftest import make_asset
from tests.test_editorial_duration_planner_integration import run
from tests.test_editorial_on_this_day_year_limit import AnnualJudge

SKIPPED_STAGES = ("threads", "synthesis", "structure", "ladder", "review", "assembly")


def make_source(tmp_path, *, seconds=60, occasions=4, pictures=3):
    """One month of separate canal walks, each its own capture moment."""
    start = datetime(2030, 5, 2, 8, tzinfo=UTC)
    groups, episodes, cards, candidates, annotations = [], [], [], [], {}
    for occasion in range(occasions):
        local = []
        for picture in range(pictures):
            taken = start + timedelta(days=occasion, minutes=17 * picture)
            asset = make_asset(f"o{occasion}-p{picture}", duration=None, file_created_at=taken)
            asset.type = AssetType.IMAGE
            description = (
                f"A clothed person walks along the canal on outing {occasion}, view {picture}."
            )
            annotations[asset.id] = AssetAnnotationLine(
                asset.id,
                f"{taken.isoformat()} | {description} | activity=walking",
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
        group = EditorialGroup(f"moment-{occasion}", tuple(local))
        episode = EditorialGroup(f"episode-{occasion}", tuple(local))
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
                text="Walking by the canal",
                evidence=MomentCardEvidence(
                    episode_meaning="People walk along the canal.",
                    representatives=(
                        RepresentativeEvidence("People walk along the canal.", "Shows the outing."),
                    ),
                    annotations=(("activity", "walking"),),
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
        "canal-month",
        "A month of walks",
        "monthly_highlights",
        (DateRange(datetime(2030, 5, 1, tzinfo=UTC), datetime(2030, 5, 31, tzinfo=UTC)),),
        seconds,
        "Show the month through its worthwhile outings.",
    )
    return StructurePlanningInput(
        case=case,
        intent=build_editorial_intent(case.product, case.ranges, brief=case.brief),
        config=Config(),
        wall_bytes=wall.text.encode(),
        moment_asset_ids={
            alias: group.candidate_ids for alias, group in zip(wall.aliases, groups, strict=True)
        },
        assets={c.asset_id: c.source for c in candidates},
        annotations={key: row.text for key, row in annotations.items()},
        audience_annotations=annotations,
        gps={},
        pixel_facts={},
        shareability_flags={},
        motion_residuals={},
        lineage={},
        bank_dir=tmp_path / "banks",
        artifact_dir=tmp_path / "plan",
        episode_readings={
            alias: EpisodeReadingCard(
                episode_id=card.episode_id,
                evidence_key=f"evidence-{card.episode_id}",
                what_happened=(
                    f"A walk along the canal on outing {index}, from the first view to the last."
                ),
                representative_asset_ids=card.representative_asset_ids,
                cache_hit=False,
            )
            for index, (alias, card) in enumerate(zip(wall.aliases, cards, strict=True))
        },
    )


class StoryJudge(AnnualJudge):
    """Places every offered fragment in two episodes and reads one moment per source."""

    def answer(self, stage, prompt):
        if stage.startswith("story-episodes"):
            known, new = prompt.split("NEW FRAGMENTS TO PLACE", 1)
            offered = re.findall(r'"reading": "([^"]+)"', new)
            open_ids = list(dict.fromkeys(re.findall(r'"id": "(S\d{4})"', known)))
            fresh = list(re.search(r"Number new episodes (S\d{4}), (S\d{4})", prompt).groups())
            ids = (open_ids + [i for i in fresh if i not in open_ids])[:2]
            return json.dumps(
                {
                    "fragments": [
                        {"reading": reading, "episode": ids[index % len(ids)]}
                        for index, reading in enumerate(offered)
                    ],
                    "new_episodes": [
                        {
                            "id": key,
                            "title": f"Canal outing {key}",
                            "account": "People walk along the canal.",
                            "role": "central" if position == 0 else "supporting",
                        }
                        for position, key in enumerate(ids)
                        if key not in open_ids
                    ],
                }
            )
        if stage.startswith("story-understanding"):
            keys = list(dict.fromkeys(re.findall(r'"episode": "(S\d{4})"', prompt)))
            return json.dumps(
                {
                    "thesis": "A month of separate canal outings.",
                    "about": [],
                    "stories": [
                        {
                            "title": f"Outing {key}",
                            "episodes": [key],
                            "purpose": "Carries part of the month",
                        }
                        for index, key in enumerate(keys)
                    ],
                    "uncertainties": [],
                }
            )
        if stage.startswith("story-weighing"):
            keys = re.findall(r"^(K\d{2}) \|", prompt, re.MULTILINE)
            # WHY: confirm only the reading's nominated center, independently of row order.
            # ControlledStoryJudge supplies one; the ordinary canal month supplies none.
            nomination = prompt.split("THE READING SAYS THIS MEMORY IS ABOUT: ", 1)[1].split(
                "\n", 1
            )[0]
            about = re.findall(r"K\d{2}", nomination)[:1]
            return json.dumps(
                {
                    "about": about,
                    "weights": {key: "minor" for key in keys if key not in about},
                    "join": [],
                    "retitle": {},
                }
            )
        if stage.startswith("story-pick-"):
            count = int(re.search(r"gets (\d+) picture", prompt).group(1))
            labels = re.findall(r"^(M\d{2}) \|", prompt, re.MULTILINE)
            return json.dumps({"keep": labels[:count]})
        if stage.startswith("standing-"):
            return json.dumps({"weak": {}})  # every canal picture stands
        if stage.startswith("moment-inventory"):
            sources = re.findall(r'"source": "(U\d+)"', prompt.split("NEW SOURCES", 1)[1])
            return json.dumps(
                {
                    "moments": [
                        {
                            "same_as": None,
                            "sources": [source],
                            "primary": source,
                            "content": f"A distinct view of the canal, {source}",
                        }
                        for source in sources
                    ]
                }
            )
        return super().answer(stage, prompt)


def test_story_first_selects_one_picture_per_depicted_moment_without_beats_or_ladders(tmp_path):
    judge = StoryJudge()
    plan = run(make_source(tmp_path), judge)

    assert plan["schema_version"] == "structure-plan-v88-story-first"
    assert plan["story"]["thesis"] == "A month of separate canal outings."
    assert plan["carriers"], "story-first selected nothing"
    taken = [carrier["taken"] for carrier in plan["carriers"]]
    assert taken == sorted(taken)
    depicted = [carrier["depicted_moment"] for carrier in plan["carriers"]]
    assert len(set(depicted)) == len(depicted)
    assert len({carrier["asset_id"] for carrier in plan["carriers"]}) == len(depicted)

    chapters = plan["chapters"]
    assert len(chapters) == len([row for row in plan["story"]["episodes"] if row["granted"]])
    assert {carrier["chapter"] for carrier in plan["carriers"]} <= set(range(1, len(chapters) + 1))
    for carrier in plan["carriers"]:
        assert chapters[carrier["chapter"] - 1]["beat"] in carrier["why"]

    asked = {call["stage"] for call in judge.calls}
    assert not [stage for stage in asked if stage.startswith(SKIPPED_STAGES)], sorted(asked)
    assert any(stage.startswith("story-episodes") for stage in asked)
    assert any(stage.startswith("moment-inventory") for stage in asked)
    assert any(stage.startswith("worthy-") for stage in asked), (
        "the memory-worthy gate must run first"
    )
    assert any(stage.startswith("standing-") for stage in asked), (
        "every carrier is asked to stand by itself"
    )
    assert all(
        "Proposed picture" not in call["prompt"]
        for call in judge.calls
        if call["stage"].startswith("story-pick-")
    ), "the pick reads the inventory; pictures are observed for the cut, not for every choice"
    assert all(carrier["story_weight"] in {"dominant", "minor"} for carrier in plan["carriers"])

    families = plan["calls_by_stage"]
    assert set(families) <= {
        "period",
        "episodes",
        "episode-skim",
        "worthy",
        "story-episodes",
        "story-understanding",
        "story-weighing",
        "story-pick",
        "moment-inventory",
        "standing",
        "shareability",
    }, sorted(families)
    assert sum(row["asked"] for row in families.values()) == len(plan["calls"])

    assert plan["intent_report"]["status"] in {
        "ok",
        "insufficient_material",
        "structural_violation",
        "planning_incomplete",
    }
    assert plan["content_seconds"] <= plan["target_seconds"]


def _inventory_offers(judge):
    """Per story, what its moment inventory was shown: source aliases, capture groups, timestamps."""
    offers = {}
    for row in judge.calls:
        if not row["stage"].startswith("moment-inventory"):
            continue
        page = row["prompt"].split("NEW SOURCES", 1)[1]
        offer = offers.setdefault(row["stage"].rsplit("-", 1)[0], {})
        for field, pattern in (
            ("sources", r'"source": "(U\d+)"'),
            ("groups", r'"capture_group": "(G\d+)"'),
            ("taken", r'"taken": "([^"]+)"'),
        ):
            offer.setdefault(field, set()).update(re.findall(pattern, page))
    return offers


def test_inventory_reads_only_the_shortlisted_capture_groups(tmp_path):
    """Two eight-outing stories, two slots each: the inventory reads the six capture groups the
    grant can still reach, not all eight."""
    captured = make_source(tmp_path, seconds=16, occasions=16, pictures=5)
    judge = StoryJudge()
    plan = run(captured, judge)

    scope = json.loads(
        next(captured.artifact_dir.rglob("story-inventory-scope.private.json")).read_text()
    )
    assert len(scope) == 2
    for row in scope.values():
        assert row["groups_offered"] == 8
        assert row["groups_shortlisted"] == 6
        assert row["units_inventoried"] == 30
        assert row["skipped_for_favourites"] is False

    offers = _inventory_offers(judge)
    assert len(offers) == 2
    for offer in offers.values():
        assert len(offer["groups"]) == 6
        assert len(offer["sources"]) <= 30
    # every carrier comes from a group the inventory actually read
    read = set().union(*(offer["taken"] for offer in offers.values()))
    assert {row["taken"] for row in plan["carriers"]} <= read
    assert plan["calls_by_stage"]["moment-inventory"]["asked"] == 4


def test_a_story_the_favourites_already_fill_is_not_inventoried(tmp_path):
    """One slot, one starred outing: the pick is settled, so nothing is read for that story."""
    captured = make_source(tmp_path, seconds=8, occasions=4, pictures=3)
    captured = replace(
        captured,
        assets={
            key: asset.model_copy(update={"is_favorite": key == "o0-p1"})
            for key, asset in captured.assets.items()
        },
    )
    judge = StoryJudge()
    plan = run(captured, judge)

    scope = json.loads(
        next(captured.artifact_dir.rglob("story-inventory-scope.private.json")).read_text()
    )
    assert sorted(row["skipped_for_favourites"] for row in scope.values()) == [False, True]

    offers = _inventory_offers(judge)
    assert len(offers) == 1  # only the story without a favourite is read
    starred_days = ("2030-05-02", "2030-05-04")  # the starred story's two outings
    assert not [
        taken for taken in next(iter(offers.values()))["taken"] if taken.startswith(starred_days)
    ]
    assert "o0-p1" in {row["asset_id"] for row in plan["carriers"]}


@pytest.mark.parametrize("old_switch", [None, "0", "1"])
def test_default_is_story_first_and_old_switch_cannot_restore_legacy(
    tmp_path, monkeypatch, old_switch
):
    if old_switch is None:
        monkeypatch.delenv("IMMICH_MEMORIES_EDITORIAL_STORY_FIRST", raising=False)
    else:
        monkeypatch.setenv("IMMICH_MEMORIES_EDITORIAL_STORY_FIRST", old_switch)
    judge = StoryJudge()
    plan = run(make_source(tmp_path), judge)
    assert any(call["stage"].startswith("worthy-") for call in judge.calls)
    assert plan["schema_version"] == "structure-plan-v88-story-first"
    assert "story" in plan
    assert not any(call["stage"].startswith(SKIPPED_STAGES) for call in judge.calls)


def test_weights_fund_stories_not_days():
    """A dominant ten-day holiday competes as one corpus; minors get one or two; none is never funded."""
    from immich_memories.analysis.editorial_story_slots import allocate_slots

    stories = [
        {"key": "holiday", "weight": "dominant"},
        {"key": "birthday", "weight": "major"},
        {"key": "cafe", "weight": "minor"},
        {"key": "walk", "weight": "minor"},
        {"key": "kitchen", "weight": "glimpse"},
        {"key": "routine", "weight": "none"},
    ]
    capacity = {"holiday": 40, "birthday": 9, "cafe": 3, "walk": 1, "kitchen": 5, "routine": 30}
    granted = allocate_slots(stories, 12, capacity)
    assert granted["holiday"] == 6 and granted["birthday"] == 3
    assert granted["cafe"] == 2 and granted["walk"] == 1
    assert granted["kitchen"] == 0 and granted["routine"] == 0  # the glimpse waits; routine never
    assert sum(granted.values()) == 12
    # more duration deepens the weighed stories before it admits anything new
    longer = allocate_slots(stories, 36, capacity)
    assert longer["holiday"] >= 18 and longer["birthday"] == 9 and longer["kitchen"] == 1
    assert longer["routine"] == 0  # routine is never funded, however long the film
    assert sum(longer.values()) == 36


def test_a_thin_story_cannot_take_more_than_it_holds():
    from immich_memories.analysis.editorial_story_slots import allocate_slots

    granted = allocate_slots(
        [{"key": "birth", "weight": "dominant"}, {"key": "cafe", "weight": "minor"}],
        12,
        {"birth": 4, "cafe": 1},
    )
    assert granted == {"birth": 4, "cafe": 1}  # the film is shorter, never refilled with variants


@pytest.mark.parametrize("already", [{}, {"stay": 2, "visit": 1}])
def test_major_presence_precedes_dominant_depth_in_a_crowded_short_film(already):
    from immich_memories.analysis.editorial_story_slots import allocate_slots

    stories = [{"key": "stay", "weight": "dominant"}] + [
        {"key": key, "weight": "major"} for key in ("visit", "outing", "celebration", "discovery")
    ]
    capacity = dict.fromkeys((s["key"] for s in stories), 8)
    granted = allocate_slots(stories, 6 - sum(already.values()), capacity, already=already)
    total = {s["key"]: granted[s["key"]] + already.get(s["key"], 0) for s in stories}
    assert total == {"stay": 2, "visit": 1, "outing": 1, "celebration": 1, "discovery": 1}


def test_covering_majors_still_respects_partition_admission():
    from immich_memories.analysis.editorial_story_slots import allocate_partition_slots

    stories = [{"key": "stay", "weight": "dominant"}, {"key": "outing", "weight": "major"}]
    choices = {"stay": {"year-a": [object()] * 5}, "outing": {"year-a": [object()]}}
    grants, partitions = allocate_partition_slots(stories, 4, choices, limit=2, used={"year-a": 1})
    assert grants == {"stay": 1, "outing": 0}
    assert sum(p.get("year-a", 0) for p in partitions.values()) == 1


def test_story_part_names_the_type_and_never_a_case():
    from immich_memories.analysis.editorial_intent import _STORY_PART_DEFAULT, _STORY_PARTS

    for product, text in _STORY_PARTS.items():
        assert "episode" in text.lower() and "central" in text.lower()
        assert not any(ch.isdigit() for ch in text), product  # no dates, no counts of a real case
    assert "episode" in _STORY_PART_DEFAULT


def test_standing_gate_is_reject_only_and_scores_by_how_often_a_picture_is_named_weak():
    from immich_memories.analysis.editorial_block_votes import judge_standing

    class Judge:
        def __init__(self):
            self.calls = []

        def ask(self, stage, prompt, max_tokens=0):
            self.calls.append(stage)
            weak = {"P02": "a lone object"}
            if stage.endswith("hashed"):
                weak["P03"] = "blurry"
            return json.dumps({"weak": weak})

    lines = {
        "a": "2030-05-02 | people at a picnic",
        "b": "2030-05-02 | a parked bicycle",
        "c": "2030-05-03 | a hat",
    }
    scores = judge_standing(
        Judge(), pictures=["a", "b", "c"], line_of=lines.get, contract="c", period_label="p"
    )
    assert {k: n for k, (n, _why) in scores.items()} == {"a": 2, "b": 0, "c": 1}


def test_timing_trim_drops_the_lightest_stories_extra_pictures_first_and_refits_the_budget():
    from immich_memories.analysis.editorial_story_planner import trim_to_timing_budget

    carriers = [
        {
            "asset_id": "d1",
            "story_episode": "K1",
            "story_weight": "dominant",
            "taken": "2025-04-18T10:00",
        },
        {
            "asset_id": "d2",
            "story_episode": "K1",
            "story_weight": "dominant",
            "taken": "2025-04-19T10:00",
        },
        {
            "asset_id": "m1",
            "story_episode": "K2",
            "story_weight": "minor",
            "taken": "2024-04-01T10:00",
        },
        {
            "asset_id": "m2",
            "story_episode": "K2",
            "story_weight": "minor",
            "taken": "2024-04-02T10:00",
        },
        {
            "asset_id": "g1",
            "story_episode": "K3",
            "story_weight": "glimpse",
            "taken": "2023-04-05T10:00",
        },
    ]

    def budget(cs):  # a divider per distinct month among the selection, out of a 20 s budget
        months = {c["taken"][:7] for c in cs}
        return 20.0 - 2.0 * len(months)

    kept, dropped = trim_to_timing_budget(carriers, budget, 3.5)
    # five pictures need 17.5 s against a 14 s budget (three months): the glimpse goes first (added
    # last), which also removes its month's divider, and then the budget fits
    assert [c["asset_id"] for c in dropped] == ["g1"]
    assert [c["asset_id"] for c in kept] == ["d1", "d2", "m1", "m2"]
    assert len(kept) * 3.5 <= budget(kept)
    _, dropped2 = trim_to_timing_budget(
        carriers, lambda cs: 20.0 - 4.0 * len({c["taken"][:7] for c in cs}), 3.5
    )
    assert [c["asset_id"] for c in dropped2] == [
        "g1",
        "m2",
    ]  # the glimpse, then the minor's extra picture; then it fits


class CompanyReplacementJudge(StoryJudge):
    """Prefer two familiar views; let the production company rule improve them."""

    def __init__(self, *, weak=False):
        super().__init__()
        self.weak = weak

    def answer(self, stage, prompt):
        if stage.startswith("story-weighing"):
            keys = re.findall(r"^(K\d{2}) \|", prompt, re.MULTILINE)
            return json.dumps({"about": [], "weights": dict.fromkeys(keys, "major")})
        if stage.startswith("story-pick-"):
            # WHY: both reading orders agree on the same moments before the company rule.
            labels = sorted(re.findall(r"^(M\d{2}) \|", prompt, re.MULTILINE))
            return json.dumps({"keep": labels[:2]})
        if stage.startswith("standing-") and self.weak:
            rows = re.findall(r"^(P\d+): (.*)$", prompt, re.MULTILINE)
            return json.dumps(
                {"weak": {label: "An object on its own" for label, row in rows if "bowl" in row}}
            )
        return super().answer(stage, prompt)


@pytest.mark.parametrize("weak", [False, True])
def test_company_improvement_only_takes_a_fresh_relation_that_stands(tmp_path, weak):
    """The real story, inventory and standing pipeline; only the standing votes differ."""
    from immich_memories.analysis.editorial_story_planner import select_story_first

    relations = ["parent", "parent", "grandparent", "parent", "parent"]
    source = make_source(tmp_path, seconds=7, occasions=1, pictures=len(relations))
    assets = list(source.assets)
    alias = next(iter(source.moment_asset_ids))
    units = [
        {
            "asset_id": asset_id,
            "taken": source.assets[asset_id].file_created_at.isoformat(),
            "moment": alias,
            "kind": "photo",
            "favourite": False,
        }
        for asset_id in assets
    ]
    lines = {
        asset_id: f"{source.annotations[asset_id]} | with Relative ({relation})"
        for asset_id, relation in zip(assets, relations, strict=True)
    }
    held = assets[2]
    # WHY: the planner reads life from the picture's own text. The one fresh relation sits on a
    # lone object, so the two standing orders decide it; a picture with life inside a major
    # story stands whatever those orders say.
    lines[held] = (
        f"{units[2]['taken']} | A ceramic bowl sits alone on a table. | activity=none"
        f" | with Relative ({relations[2]})"
    )
    records = {}
    selection = select_story_first(
        judge=CompanyReplacementJudge(weak=weak),
        tables={},
        aliases=[alias],
        factual_rows_fn=lambda _tables, _aliases: [
            {"moment_id": alias, "taken": units[0]["taken"], "places": "Canal"}
        ],
        moment_assets=source.moment_asset_ids,
        lines=lines,
        contract=source.intent.story_prompt_block(),
        event_units={"outing": units},
        family_of_moment={alias: "outing"},
        anchor_label={"outing": "F01"},
        label_line=lambda unit: lines[unit["asset_id"]],
        quality=lambda _asset: 1.0,
        life=lambda asset_id: asset_id != held,
        target_seconds=7,
        seconds_per_slot=3.5,
        record=lambda name, value: records.update({name: value}),
        family_tier={"outing": 0},
    )

    pick = next(value for key, value in records.items() if key.startswith("story-pick-"))
    chosen = [carrier["asset_id"] for carrier in selection.carriers]
    if weak:
        # Later familiar views make a lost choice observable: a refill would choose their midpoint.
        assert chosen == assets[:2]
        assert pick["company_replacements"] == []
    else:
        assert chosen == [assets[0], held]
        assert [row["new_relations"] for row in pick["company_replacements"]] == [["grandparent"]]
    assert selection.calls["selection_passes"] == 1


def test_the_audience_reads_the_finished_cut_not_every_candidate(tmp_path):
    class RefusingJudge(StoryJudge):
        """Holds back one named picture whenever the audience gate reads it."""

        def answer(self, stage, prompt):
            if stage.startswith("shareability-") and "outing 1, view 2" in prompt:
                return json.dumps({"finding": "bathing", "why": "A person is bathing"})
            return super().answer(stage, prompt)

    plan = run(replace(make_source(tmp_path), audience="sendable"), RefusingJudge())

    refused = [row["asset_id"] for row in plan["shareability"]["tightened"]]
    assert refused == ["o1-p2"]
    assert "o1-p2" not in {carrier["asset_id"] for carrier in plan["carriers"]}
    assert plan["calls_by_stage"]["shareability"]["asked"] <= len(plan["carriers"]) + len(refused)


def test_each_moment_carries_its_episodes_banked_meaning_and_representatives():
    """The wall truncates the episode reading to 96 characters; planning gets the whole one."""
    from immich_memories.analysis.editorial_structure_source import episode_reading_cards
    from immich_memories.analysis.selection_source_groups import EditorialGroupProjection
    from immich_memories.analysis.text_episode_reader import (
        EpisodeEditorialEvidence,
        TextEpisodeReadResult,
    )
    from immich_memories.store.episode_readings import (
        BankedEpisodeReading,
        EpisodeReadingIdentity,
        EpisodeRepresentative,
    )

    candidates = tuple(
        EditorialCandidate(
            asset_id=f"a{index}",
            taken_at=datetime(2030, 5, 2, 8 + index, tzinfo=UTC),
            media_kind="photo",
            live_photo_stitch_member_ids=(),
            rendering_family_id=None,
            favourite=False,
            source=make_asset(f"a{index}", duration=None),
            proposed_segment=None,
            shippable_duration=0,
            grounded_annotations=(),
        )
        for index in range(3)
    )
    group = EditorialGroup("episode-read", candidates)
    identity = EpisodeReadingIdentity("episode-read", "producer", "evidence-key")
    reading = BankedEpisodeReading(
        identity,
        group.candidate_ids,
        "A long afternoon at the canal that the wall can only show the first 96 characters of.",
        (EpisodeRepresentative("a1", "Shows the outing"),),
        (),
    )
    episodes = TextEpisodeReadResult(
        (
            EpisodeEditorialEvidence(
                EditorialGroupProjection(group, group.candidate_ids), identity, reading, True, None
            ),
        ),
        SimpleNamespace(),
        (),
        0,
    )
    cards = (
        MomentCard(
            moment_id="moment-read",
            episode_id="episode-read",
            full_asset_ids=group.candidate_ids,
            selectable_asset_ids=group.candidate_ids,
            representative_asset_ids=("a0",),
            text="Walking by the canal",
            evidence=MomentCardEvidence(
                episode_meaning="A long afternoon at the canal.",
                representatives=(RepresentativeEvidence("A view.", "Shows it."),),
                annotations=(),
            ),
        ),
        MomentCard(
            moment_id="moment-unread",
            episode_id="episode-unread",
            full_asset_ids=("a2",),
            selectable_asset_ids=("a2",),
            representative_asset_ids=("a2",),
            text="Later",
            evidence=MomentCardEvidence(
                episode_meaning="Later that day.",
                representatives=(RepresentativeEvidence("A view.", "Shows it."),),
                annotations=(),
            ),
        ),
    )

    carried = episode_reading_cards(episodes, cards, ("M001", "M002"))

    assert carried["M001"].what_happened == reading.what_happened
    assert carried["M001"].representative_asset_ids == ("a1",)
    assert (carried["M001"].episode_id, carried["M001"].evidence_key) == (
        "episode-read",
        "evidence-key",
    )
    assert carried["M001"].cache_hit is True
    unread = carried["M002"]
    assert (unread.what_happened, unread.evidence_key, unread.cache_hit) == ("", "", False)
    assert unread.representative_asset_ids == ("a2",)


def test_the_story_read_sees_the_banked_episode_meaning_and_representatives(tmp_path):
    """The wall row truncates the episode reading to 96 characters; the story read gets it whole."""
    judge = StoryJudge()
    run(make_source(tmp_path), judge)

    pages = [call["prompt"] for call in judge.calls if call["stage"].startswith("story-episodes")]
    assert pages
    assert all(
        "A walk along the canal on outing 0, from the first view to the last." in page
        for page in pages[:1]
    )
    assert not any("People walk along the canal." in page for page in pages)
