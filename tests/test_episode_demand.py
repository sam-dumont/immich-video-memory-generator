"""A film reads the episodes of the stories it chose, and never the whole period."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from immich_memories.analysis.editorial_rule_episodes import RuleEpisodeReader
from immich_memories.analysis.episode_demand import DemandEpisodeReadings
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.analysis.selection_source_groups import project_episode_groups
from immich_memories.analysis.text_episode_reader import CachedTextEpisodeReader
from immich_memories.store.episode_readings import EpisodeReadingProducer, EpisodeReadingStore
from tests.conftest import make_asset
from tests.test_text_episode_reader import _AnnotationLines


class Lines(_AnnotationLines):
    """The same stand-in, with the contract the rules reader keys its producer on."""

    @property
    def contract(self):
        from immich_memories.analysis.annotation_lines import AnnotationContract

        return AnnotationContract("annotation-line-v1", self.producer_versions)


NOON = datetime(2024, 2, 1, 12, tzinfo=UTC)
ASSETS = ("day-one", "day-two", "day-three")


def prepared_month():
    """Three days, so the 90-minute grouping gives three canonical episodes."""
    return prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: tuple(
                make_asset(asset, file_created_at=NOON + timedelta(days=index))
                for index, asset in enumerate(ASSETS)
            )
        ),
    )


def answer(prompt: str) -> str:
    import json
    import re

    return json.dumps(
        {
            "schema_version": "episode-reading-text-v1",
            "episodes": [
                {
                    "episode": int(alias),
                    "what_happened": "Something happened.",
                    "representatives": [{"asset": 1, "reason": "the only frame"}],
                    "cull": [],
                }
                for alias in re.findall(r"^episode (\d+)$", prompt, re.MULTILINE)
            ],
        }
    )


def demand_for(tmp_path, asked):
    prepared = prepared_month()
    lines = Lines({asset: f"{asset} | one complete line" for asset in ASSETS})
    producer = EpisodeReadingProducer(
        model_id="a-model",
        prompt_version="episode-prompt-v3",
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:student-v1",),
    )
    store = EpisodeReadingStore(tmp_path / "annotations.sqlite")

    def text(_prepared):
        return CachedTextEpisodeReader(
            store=store,
            producer=producer,
            annotations=lines,
            requester=lambda prompt: (asked.append(prompt), answer(prompt))[1],
        )

    demand = DemandEpisodeReadings(
        lambda _prepared: RuleEpisodeReader(lines, by_quality=True), text
    )
    demand.remember(prepared)
    return demand, prepared


def test_the_draft_reads_the_whole_period_without_one_model_call(tmp_path):
    asked: list[str] = []
    demand, prepared = demand_for(tmp_path, asked)

    result = demand.read(project_episode_groups(prepared, prepared.candidate_ids))

    assert len(result.episodes) == 3
    assert all(episode.reading is not None for episode in result.episodes)
    assert result.actual_calls == 0
    assert asked == []


def test_only_the_episodes_of_the_stories_the_draft_chose_are_read(tmp_path):
    asked: list[str] = []
    demand, prepared = demand_for(tmp_path, asked)
    chosen = project_episode_groups(prepared, ("day-two",))[0].group.group_id

    readings = demand.readings_for(["day-two"])

    assert list(readings) == [chosen]
    assert demand.demanded == [chosen]
    assert len(asked) == 1
    assert "day-one" not in asked[0]
    assert "day-three" not in asked[0]


def test_a_second_demand_for_the_same_story_is_free(tmp_path):
    asked: list[str] = []
    demand, _prepared = demand_for(tmp_path, asked)
    demand.readings_for(["day-two"])
    paid = len(asked)

    again = demand.readings_for(["day-two"])

    assert len(asked) == paid
    assert list(again)


def test_a_picture_outside_this_corpus_demands_nothing(tmp_path):
    asked: list[str] = []
    demand, _prepared = demand_for(tmp_path, asked)

    assert demand.readings_for(["not-in-this-film"]) == {}
    assert asked == []


def one_story_over_three_days():
    """One story holding every day of the month, as a long month-at-home story does."""
    from types import SimpleNamespace

    story = SimpleNamespace(
        stories=[{"key": "S1", "title": "The month", "episodes": ["e1"], "gate": "remarkable"}],
        episodes=[SimpleNamespace(key="e1", moments=["m1", "m2", "m3"])],
        audit={"hints": {}},
    )
    moments = {"m1": ["day-one"], "m2": ["day-two"], "m3": ["day-three"]}
    return story, moments


def polish_reading_through(demand, tmp_path):
    from immich_memories.analysis.editorial_thin_layer import ThinPolish

    def read_period(chosen):
        demand.readings_for([asset for assets in chosen.values() for asset in assets])
        return "the month", {}

    return ThinPolish(bank_dir=tmp_path, read_period=read_period)


def test_a_cold_cut_reads_only_the_episodes_its_draft_put_a_shot_in(tmp_path):
    asked: list[str] = []
    demand, prepared = demand_for(tmp_path, asked)
    story, moments = one_story_over_three_days()
    drafted_episode = project_episode_groups(prepared, ("day-two",))[0].group.group_id

    polish_reading_through(demand, tmp_path).catalogue_of(
        story, moments, drafted=[{"asset_id": "day-two"}]
    )

    assert demand.demanded == [drafted_episode]
    assert len(asked) == 1
    assert "day-one" not in asked[0]
    assert "day-three" not in asked[0]


def test_a_second_cut_over_the_same_draft_asks_nothing_new(tmp_path):
    asked: list[str] = []
    story, moments = one_story_over_three_days()
    drafted = [{"asset_id": "day-two"}, {"asset_id": "day-three"}]
    cold, _prepared = demand_for(tmp_path, asked)
    polish_reading_through(cold, tmp_path).catalogue_of(story, moments, drafted=drafted)
    paid = len(asked)

    warm, _prepared = demand_for(tmp_path, asked)
    polish_reading_through(warm, tmp_path).catalogue_of(story, moments, drafted=drafted)

    assert paid > 0
    assert len(asked) == paid


def test_a_cut_with_no_draft_reads_nothing(tmp_path):
    asked: list[str] = []
    demand, _prepared = demand_for(tmp_path, asked)
    story, moments = one_story_over_three_days()

    polish_reading_through(demand, tmp_path).catalogue_of(story, moments, drafted=[])

    assert demand.demanded == []
    assert asked == []


def test_only_a_model_run_with_a_catalogued_period_reads_on_demand():
    from immich_memories.analysis.episode_demand import demand_reader_factory

    rules, text = object(), object()

    assert demand_reader_factory(rules, text, mode="rules", on_demand=True) == (rules, None)
    assert demand_reader_factory(rules, text, mode="model", on_demand=False) == (text, None)
    factory, demand = demand_reader_factory(rules, text, mode="model", on_demand=True)
    prepared = prepared_month()
    assert factory(prepared) is demand
    assert demand is not None
    assert demand.readings_for(["not-in-this-film"]) == {}
