"""Keep grounded period relationships through editorial reductions, outside picture evidence."""

import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

from immich_memories.analysis.editorial_case import _adapt_production_cards
from immich_memories.analysis.editorial_contracts import EditorialCandidate
from immich_memories.analysis.editorial_episode_documents import (
    anchor_observations,
    factual_moment_rows,
)
from immich_memories.analysis.editorial_moment_wall import (
    MomentCardEvidence,
    ProductionMomentWallRenderer,
    RepresentativeEvidence,
)
from immich_memories.analysis.editorial_people import adapt_editorial_people
from immich_memories.analysis.editorial_person_period_facts import (
    person_period_facts,
    render_person_period_facts,
)
from immich_memories.analysis.editorial_wall_rows import _table_rows
from immich_memories.analysis.moment_cards import MomentCard
from immich_memories.analysis.selection_source_groups import EditorialGroup
from immich_memories.api.models import Person
from immich_memories.people.context import PersonPromptContext
from tests.editorial_story_fixtures import ControlledStoryJudge
from tests.test_editorial_duration_planner_integration import reading_cards, run, source


def period_source(tmp_path):
    captured = source(tmp_path, seconds=60)
    candidates = []
    for index, asset in enumerate(captured.assets.values()):
        if index >= 30:
            asset.file_created_at += timedelta(days=7)
        # Only an unsampled moment carries the person association. Neither the
        # representative descriptions nor the picture annotations identify Rowan.
        asset.people = [Person(id="partner-id", name="Rowan")] if index == 6 else []
        # Equal favourites keep both controlled events out of the quality reservoir
        # without the favourite-within-moment rule discarding their other pictures.
        asset.is_favorite = True
        candidates.append(
            EditorialCandidate(
                asset_id=asset.id,
                taken_at=asset.file_created_at,
                media_kind="photo",
                live_photo_stitch_member_ids=(),
                rendering_family_id=None,
                favourite=asset.is_favorite,
                source=asset,
                shippable_duration=0,
                grounded_annotations=(),
            )
        )
    groups = tuple(
        EditorialGroup(f"moving-moment-{i}", tuple(candidates[6 * i : 6 * (i + 1)]))
        for i in range(5)
    ) + (EditorialGroup("second-event-moment", tuple(candidates[30:])),)
    episodes = (
        EditorialGroup("moving-episode", tuple(candidates[:30])),
        EditorialGroup("second-event-episode", tuple(candidates[30:])),
    )
    cards = tuple(
        MomentCard(
            moment_id=group.group_id,
            episode_id=episodes[0 if index < 5 else 1].group_id,
            full_asset_ids=group.candidate_ids,
            selectable_asset_ids=group.candidate_ids,
            representative_asset_ids=group.candidate_ids[:1],
            text=f"Moving scene {index}",
            evidence=MomentCardEvidence(
                episode_meaning="Packing furniture and settling into a new home.",
                representatives=(
                    RepresentativeEvidence(
                        f"Scene {index}: people carry furniture through the doorway "
                        + "while other people move boxes into different rooms " * 2,
                        "Shows a grounded part of the activity.",
                    ),
                ),
                annotations=(("activity", "working"),),
            ),
        )
        for index, group in enumerate(groups)
    )
    context = PersonPromptContext(
        person_ids=("partner-id",),
        name="Rowan",
        role=None,
        tier="inner",
        birth_date=None,
        relationship="partner",
        relationship_source="confirmed",
        first_month="2020-05",
        onset="2020-05",
        relationship_current=True,
        owner_relationship_kinds=("partner-of",),
    )
    prepared = SimpleNamespace(
        moment_groups=groups,
        episode_groups=episodes,
        candidates=tuple(candidates),
    )
    adapted, _ = _adapt_production_cards(prepared, cards)
    wall = ProductionMomentWallRenderer(
        prepared,
        cards,
        adapt_editorial_people({"partner-id": context}),
    ).render(adapted)
    captured = replace(
        captured,
        wall_bytes=wall.text.encode(),
        moment_asset_ids={
            alias: group.candidate_ids for alias, group in zip(wall.aliases, groups, strict=True)
        },
        episode_readings=reading_cards(wall.aliases, cards),
    )
    return captured, wall.aliases


class PeriodJudge(ControlledStoryJudge):
    """Keep the two fixture occasions separate and control only their judgments."""

    def __init__(self, *, only_competing_event=False):
        super().__init__()
        self.only_competing_event = only_competing_event

    def answer(self, stage, prompt):
        raw = super().answer(stage, prompt)
        if stage.startswith("story-episodes"):
            result = json.loads(raw)
            for fragment in result["fragments"]:
                # the period's second event is the page's second episode row
                fragment["episode"] = "S0001" if fragment["reading"] == "r1" else "S0002"
            for episode in result["new_episodes"]:
                episode["role"] = (
                    "incidental"
                    if self.only_competing_event and episode["id"] == "S0001"
                    else "central"
                )
            return json.dumps(result)
        if stage.startswith("story-understanding") and self.only_competing_event:
            result = json.loads(raw)
            result["about"] = ["S0002"]
            return json.dumps(result)
        if stage.startswith("story-weighing") and self.only_competing_event:
            return '{"about":["K02"],"weights":{"K01":"none"}}'
        return raw


def test_unsampled_person_period_facts_reach_editorial_stages_but_not_picture_evidence(tmp_path):
    captured, aliases = period_source(tmp_path)
    tables = _table_rows(captured.wall_bytes.decode().splitlines())
    rows = {row["moment_id"]: row for row in factual_moment_rows(tables, aliases)}
    sampled = anchor_observations(aliases[:5], rows)
    assert "Scene 1:" not in sampled and "Rowan (partner)" in sampled
    assert len(sampled) > 170  # Global synthesis used to retain only this scene prefix.
    expected = render_person_period_facts(person_period_facts(tables, aliases[:5]))
    assert "Rowan" in expected and "2020-05" in expected and "confirmed" in expected
    assert not person_period_facts(tables, aliases[5:])

    judge = PeriodJudge()
    plan = run(captured, judge)
    assert plan["carriers"]
    assert set(plan["person_period_facts"]) == {"F01"}
    assert tuple(plan["person_period_facts"]["F01"][0]["grounding_moment_ids"]) == (aliases[1],)
    worthy = [row["prompt"] for row in judge.calls if row["stage"].startswith("worthy-")]
    assert not worthy
    story_pages = [
        row["prompt"] for row in judge.calls if row["stage"].startswith("story-episodes")
    ]
    assert story_pages
    first_page = next(prompt for prompt in story_pages if "Rowan" in prompt)
    offered = json.loads(first_page.split("EPISODES TO PLACE (", 1)[1].split(")\n", 1)[1])
    assert len(offered) == 2  # one row per canonical episode of the period
    grounded = [row for row in offered if "Rowan" in row["people"]]
    assert len(grounded) == 1
    grounded_people = grounded[0]["people"]
    assert "relationship=partner" in grounded_people and "source=confirmed" in grounded_people
    assert "first=2020-05" in grounded_people
    for prefix in ("shareability-",):
        prompts = [row["prompt"] for row in judge.calls if row["stage"].startswith(prefix)]
        assert prompts, prefix
        assert all(
            "Rowan" not in prompt
            and "first_library_month" not in prompt
            and "sustained_onset_month" not in prompt
            for prompt in prompts
        ), prefix


def test_person_period_context_does_not_force_its_event_into_the_film(tmp_path):
    captured, _ = period_source(tmp_path)
    # The old fixture starred every picture to avoid a retired quality reservoir;
    # this assertion isolates the relationship context from owner favourites.
    captured = replace(
        captured,
        assets={
            key: asset.model_copy(update={"is_favorite": False})
            for key, asset in captured.assets.items()
        },
    )
    judge = PeriodJudge(only_competing_event=True)
    plan = run(captured, judge)
    worthy = [row["prompt"] for row in judge.calls if row["stage"].startswith("worthy-")]
    assert not worthy
    assert set(plan["person_period_facts"]) == {"F01"}
    assert plan["carriers"]
    assert all(int(c["asset_id"].rsplit("-", 1)[1]) >= 30 for c in plan["carriers"])
