"""Unambiguous page labels are banked without paying for the same reading again."""

import json
from datetime import UTC, datetime

import pytest

from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.analysis.selection_source_groups import project_episode_groups
from immich_memories.analysis.text_episode_reader import CachedTextEpisodeReader
from immich_memories.store.episode_readings import (
    EpisodeReadingProducer,
    EpisodeReadingStore,
    EpisodeRepresentative,
)
from tests.conftest import make_asset
from tests.test_text_episode_request_plan import _Lines


def _reader_case(tmp_path, alias):
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (
                make_asset("offered-frame", file_created_at=datetime(2022, 1, 1, tzinfo=UTC)),
            )
        ),
    )
    calls = []
    answer = json.dumps(
        {
            "schema_version": "episode-reading-text-v1",
            "episodes": [
                {
                    "episode": 1,
                    "what_happened": "A friend visits for lunch.",
                    "representatives": [{"asset": alias, "reason": "Shows the friend."}],
                    "cull": [],
                }
            ],
        }
    )
    reader = CachedTextEpisodeReader(
        store=EpisodeReadingStore(tmp_path / "annotations.sqlite"),
        producer=EpisodeReadingProducer(
            model_id="public-text-model",
            prompt_version="episode-prompt-v1",
            schema_version="episode-reading-text-v1",
            annotation_renderer_version="annotation-line-v1",
            annotation_versions=("description:public-v1",),
        ),
        annotations=_Lines({"offered-frame": "A friend sits at the lunch table."}),
        requester=lambda prompt: calls.append(prompt) or answer,
    )
    return reader, project_episode_groups(prepared, prepared.candidate_ids), calls


@pytest.mark.parametrize("alias", ["1", "asset 1"])
def test_numeric_representative_labels_are_read_once_then_banked(tmp_path, alias):
    reader, projections, calls = _reader_case(tmp_path, alias)

    first = reader.read(projections)
    second = reader.read(projections)

    assert first.episodes[0].reading is not None
    assert first.episodes[0].reading.representatives == (
        EpisodeRepresentative("offered-frame", "Shows the friend."),
    )
    assert second.episodes[0].reading == first.episodes[0].reading
    assert second.episodes[0].cache_hit
    assert len(calls) == 1


@pytest.mark.parametrize(
    "alias",
    [True, 1.0, 0, "0", "2", "asset 2", "01", "1.0", "１", "asset 1 or 2", "9" * 5000],
)
def test_invalid_or_unoffered_labels_do_not_become_banked_readings(tmp_path, alias):
    """No reading is banked, and the refusal is, so the same question is not bought twice."""
    reader, projections, calls = _reader_case(tmp_path, alias)

    first = reader.read(projections)
    asked_once = len(calls)
    second = reader.read(projections)

    assert first.episodes[0].reading is None
    assert second.episodes[0].reading is None
    assert not second.episodes[0].cache_hit
    assert asked_once > 1
    assert len(calls) == asked_once
