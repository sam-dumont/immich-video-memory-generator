"""An episode reading may only name what a fact line of that episode names."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.analysis.selection_source_groups import project_episode_groups
from immich_memories.analysis.text_episode_answers import _EpisodeRequestScope
from immich_memories.api.album_service import AlbumRef
from immich_memories.store.episode_readings import (
    BankedEpisodeReading,
    EpisodeReadingIdentity,
    EpisodeReadingProducer,
    EpisodeReadingStore,
    EpisodeRepresentative,
)
from tests.conftest import make_asset


def _scope(assets: tuple[str, ...]) -> _EpisodeRequestScope:
    return _EpisodeRequestScope(
        identity=EpisodeReadingIdentity(group_id="e1", producer_key="p", evidence_key="e"),
        full_asset_ids=assets,
        page_asset_ids=assets,
        page_number=1,
        page_count=1,
    )


def test_the_prompt_says_written_text_names_only_the_thing_it_is_written_on() -> None:
    from immich_memories.analysis.text_episode_prompt import EpisodePromptFacts, episode_prompt

    prompt = episode_prompt(
        (_scope(("a1",)),),
        EpisodePromptFacts(lines={"a1": "a crowd outside a building with a stage and banners"}),
    )

    assert "banner" in prompt
    assert "never names the day, the place or the event" in prompt


def test_the_prompt_carries_the_albums_that_hold_the_episode() -> None:
    from immich_memories.analysis.text_episode_prompt import EpisodePromptFacts, episode_prompt

    prompt = episode_prompt(
        (_scope(("a1", "a2")),),
        EpisodePromptFacts(
            lines={"a1": "a crowd outside a building", "a2": "a stage at dusk"},
            album_names=lambda asset_ids: ("Summer Festival 2022",) if "a2" in asset_ids else (),
        ),
    )

    assert "  Albums: Summer Festival 2022\n" in prompt


def test_an_episode_no_album_holds_carries_no_album_line() -> None:
    from immich_memories.analysis.text_episode_prompt import EpisodePromptFacts, episode_prompt

    prompt = episode_prompt(
        (_scope(("a1",)),),
        EpisodePromptFacts(lines={"a1": "a crowd outside a building"}, album_names=lambda _ids: ()),
    )

    assert "Albums:" not in prompt


def _producer(prompt_version: str) -> EpisodeReadingProducer:
    return EpisodeReadingProducer(
        model_id="a-reader",
        prompt_version=prompt_version,
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:student-v1",),
    )


def test_a_reading_banked_under_the_previous_prompt_is_read_again(tmp_path: Path) -> None:
    from immich_memories.analysis.text_episode_prompt import TEXT_EPISODE_PROMPT_VERSION

    lines = {"a1": "a crowd outside a building with a stage and banners"}
    stale = _producer("episode-prompt-v1")
    identity = EpisodeReadingIdentity.from_annotations(
        group_id="e1", producer_key=stale.key(), annotation_lines=lines
    )
    store = EpisodeReadingStore(tmp_path / "annotations.sqlite")
    store.remember(
        (
            BankedEpisodeReading(
                identity=identity,
                full_asset_ids=("a1",),
                what_happened="The festival on the banner.",
                representatives=(EpisodeRepresentative("a1", "the stage"),),
                cull_decisions=(),
            ),
        )
    )

    current = EpisodeReadingIdentity.from_annotations(
        group_id="e1",
        producer_key=_producer(TEXT_EPISODE_PROMPT_VERSION).key(),
        annotation_lines=lines,
    )

    assert store.readings_for((identity,))
    assert store.readings_for((current,)) == {}


def test_the_reader_shows_the_album_that_holds_the_episode(tmp_path: Path) -> None:
    from immich_memories.analysis.editorial_album_index import RunAlbumNames
    from immich_memories.analysis.text_episode_prompt import TEXT_EPISODE_PROMPT_VERSION
    from immich_memories.analysis.text_episode_reader import CachedTextEpisodeReader
    from tests.test_editorial_album_index import _Albums
    from tests.test_text_episode_reader import _AnnotationLines

    noon = datetime(2022, 8, 6, 12, tzinfo=UTC)
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (
                make_asset("stage-wide", file_created_at=noon),
                make_asset("crowd-front", file_created_at=noon + timedelta(minutes=4)),
            )
        ),
    )
    projections = project_episode_groups(prepared, prepared.candidate_ids)
    annotations = _AnnotationLines(
        {
            "stage-wide": "a crowd outside a building with a stage and banners",
            "crowd-front": "people with drinks in front of a stage",
        }
    )
    source = _Albums(
        ((AlbumRef(id="al-1", name="Summer Festival 2022", asset_count=2), ("stage-wide",)),)
    )
    prompts: list[str] = []

    def requester(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps(
            {
                "schema_version": "episode-reading-text-v1",
                "episodes": [
                    {
                        "episode": 1,
                        "what_happened": "An afternoon at a festival.",
                        "representatives": [{"asset": 1, "reason": "the stage"}],
                        "cull": [],
                    }
                ],
            }
        )

    CachedTextEpisodeReader(
        store=EpisodeReadingStore(tmp_path / "annotations.sqlite"),
        producer=_producer(TEXT_EPISODE_PROMPT_VERSION),
        annotations=annotations,
        requester=requester,
        albums=RunAlbumNames(source),
    ).read(projections)

    assert prompts and "  Albums: Summer Festival 2022\n" in prompts[0]
