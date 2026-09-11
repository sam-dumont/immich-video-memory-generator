"""Pre-request cache-plan gates for the text episode reader."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from immich_memories.analysis.annotation_lines import (
    AnnotationContract,
    AnnotationLineBatch,
    AssetAnnotationLine,
)
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.analysis.selection_source_groups import project_episode_groups
from immich_memories.analysis.text_episode_reader import (
    CachedTextEpisodeReader,
    EpisodeCacheRequestPlan,
    TextEpisodeRequestLimits,
)
from immich_memories.store.episode_readings import (
    BankedEpisodeReading,
    EpisodeReadingIdentity,
    EpisodeReadingProducer,
    EpisodeReadingStore,
    EpisodeRepresentative,
)
from tests.conftest import make_asset


class _Lines:
    def __init__(self, lines: dict[str, str]) -> None:
        self._lines = lines

    def lines_for(self, asset_ids: tuple[str, ...]) -> AnnotationLineBatch:
        return AnnotationLineBatch(
            requested_asset_ids=asset_ids,
            lines=tuple(
                AssetAnnotationLine(asset_id, self._lines[asset_id]) for asset_id in asset_ids
            ),
            missing_asset_ids=(),
            contract=AnnotationContract("annotation-line-v1", ("description:public-v1",)),
        )


def _scenario(tmp_path: Path, *, warm: bool = True):
    started = datetime(2022, 1, 1, 9, tzinfo=UTC)
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (
                make_asset("warm-frame", file_created_at=started),
                make_asset("missing-frame", file_created_at=started + timedelta(hours=3)),
            )
        ),
    )
    projections = project_episode_groups(prepared, prepared.candidate_ids)
    lines = _Lines(
        {
            "warm-frame": "warm frame | public description",
            "missing-frame": "missing frame | public description",
        }
    )
    producer = EpisodeReadingProducer(
        model_id="public-text-model",
        prompt_version="episode-prompt-v1",
        schema_version="episode-reading-text-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:public-v1",),
    )
    identities = tuple(
        EpisodeReadingIdentity.from_annotations(
            group_id=projection.group.group_id,
            producer_key=producer.key(),
            annotation_lines={
                asset_id: lines._lines[asset_id] for asset_id in projection.group.candidate_ids
            },
        )
        for projection in projections
    )
    store = EpisodeReadingStore(tmp_path / "episode-plan.sqlite")
    if warm:
        store.remember(
            (
                BankedEpisodeReading(
                    identity=identities[0],
                    full_asset_ids=projections[0].group.candidate_ids,
                    what_happened="The first public episode is already understood.",
                    representatives=(
                        EpisodeRepresentative("warm-frame", "It represents the first episode."),
                    ),
                    cull_decisions=(),
                ),
            )
        )
    return projections, lines, producer, identities, store


def test_fail_open_store_drift_is_stopped_before_the_requester(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projections, lines, producer, identities, store = _scenario(tmp_path)
    observed: list[EpisodeCacheRequestPlan] = []
    requester_calls: list[str] = []
    monkeypatch.setattr(store, "readings_for", lambda _identities: {})

    def require_retained_hit(plan: EpisodeCacheRequestPlan) -> None:
        observed.append(plan)
        if plan.cache_hit_identities != (identities[0],):
            raise RuntimeError("retained cache partition drifted")

    with pytest.raises(RuntimeError, match="cache partition drifted"):
        CachedTextEpisodeReader(
            store=store,
            producer=producer,
            annotations=lines,
            requester=lambda prompt: requester_calls.append(prompt) or "{}",
            request_plan_guard=require_retained_hit,
        ).read(projections)

    assert requester_calls == []
    assert observed[0].requested_identities == identities
    assert observed[0].cache_hit_identities == ()
    assert observed[0].missing_identities == identities
    assert observed[0].initial_pack_count == 1


def test_valid_warm_missing_partition_passes_before_request(
    tmp_path: Path,
) -> None:
    projections, lines, producer, identities, store = _scenario(tmp_path)
    events: list[str] = []

    def guard(plan: EpisodeCacheRequestPlan) -> None:
        events.append("guard")
        assert plan.requested_identities == identities
        assert plan.cache_hit_identities == (identities[0],)
        assert plan.missing_identities == (identities[1],)
        assert plan.initial_page_count == 1
        assert plan.initial_pack_count == 1
        assert plan.oversized_page_count == 0

    def requester(_prompt: str) -> str:
        events.append("request")
        return json.dumps(
            {
                "schema_version": "episode-reading-text-v1",
                "episodes": [
                    {
                        "episode": 1,
                        "what_happened": "The second public episode is now understood.",
                        "representatives": [
                            {"asset": 1, "reason": "It represents the second episode."}
                        ],
                        "cull": [],
                    }
                ],
            }
        )

    result = CachedTextEpisodeReader(
        store=store,
        producer=producer,
        annotations=lines,
        requester=requester,
        request_plan_guard=guard,
        strict_persistence_readback=True,
    ).read(projections)

    assert events == ["guard", "request"]
    assert result.actual_calls == 1
    assert [episode.cache_hit for episode in result.episodes] == [True, False]
    assert all(episode.reading is not None for episode in result.episodes)


def test_initial_and_retry_completions_survive_a_later_interruption(tmp_path: Path) -> None:
    started = datetime(2022, 1, 1, 9, tzinfo=UTC)
    asset_ids = ("first", "second", "third")
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: tuple(
                make_asset(asset_id, file_created_at=started + timedelta(hours=index * 4))
                for index, asset_id in enumerate(asset_ids)
            )
        ),
    )
    projections = project_episode_groups(prepared, prepared.candidate_ids)
    lines = _Lines({asset_id: f"{asset_id} | public description" for asset_id in asset_ids})
    producer = EpisodeReadingProducer(
        model_id="public-text-model",
        prompt_version="episode-prompt-v1",
        schema_version="episode-reading-text-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:public-v1",),
    )
    identities = tuple(
        EpisodeReadingIdentity.from_annotations(
            group_id=projection.group.group_id,
            producer_key=producer.key(),
            annotation_lines={
                asset_id: lines._lines[asset_id] for asset_id in projection.group.candidate_ids
            },
        )
        for projection in projections
    )
    store = EpisodeReadingStore(tmp_path / "interrupted.sqlite")
    calls = 0

    def requester(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise KeyboardInterrupt
        return json.dumps(
            {
                "schema_version": "episode-reading-text-v1",
                "episodes": [
                    {
                        "episode": 1,
                        "what_happened": "One complete episode is understood.",
                        "representatives": [{"asset": 1, "reason": "Grounded frame."}],
                        "cull": [],
                    }
                ],
            }
        )

    with pytest.raises(KeyboardInterrupt):
        CachedTextEpisodeReader(
            store=store,
            producer=producer,
            annotations=lines,
            requester=requester,
            strict_persistence_readback=True,
        ).read(projections)

    recalled = store.readings_for(identities)
    assert calls == 3
    assert set(recalled) == {identities[0].group_id, identities[1].group_id}


def test_interrupted_multi_page_episode_is_not_partially_banked(tmp_path: Path) -> None:
    started = datetime(2022, 1, 1, 9, tzinfo=UTC)
    asset_ids = tuple(f"long-{index:03d}" for index in range(95))
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: tuple(
                make_asset(asset_id, file_created_at=started + timedelta(minutes=index))
                for index, asset_id in enumerate(asset_ids)
            )
        ),
    )
    projections = project_episode_groups(prepared, prepared.candidate_ids)
    lines = _Lines(dict.fromkeys(asset_ids, "one complete public annotation"))
    producer = EpisodeReadingProducer(
        model_id="public-text-model",
        prompt_version="episode-prompt-v1",
        schema_version="episode-reading-text-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:public-v1",),
    )
    identity = EpisodeReadingIdentity.from_annotations(
        group_id=projections[0].group.group_id,
        producer_key=producer.key(),
        annotation_lines=lines._lines,
    )
    store = EpisodeReadingStore(tmp_path / "paged-interrupted.sqlite")
    calls = 0

    def requester(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return json.dumps(
            {
                "schema_version": "episode-reading-text-v1",
                "episodes": [
                    {
                        "episode": 1,
                        "what_happened": "The first page is understood.",
                        "representatives": [{"asset": 1, "reason": "Grounded page frame."}],
                        "cull": [],
                    }
                ],
            }
        )

    with pytest.raises(KeyboardInterrupt):
        CachedTextEpisodeReader(
            store=store,
            producer=producer,
            annotations=lines,
            requester=requester,
            limits=TextEpisodeRequestLimits(max_assets_per_page=90),
            strict_persistence_readback=True,
        ).read(projections)

    assert calls == 2
    assert store.readings_for((identity,)) == {}


def test_strict_readback_aborts_on_silent_write_failure_but_default_fails_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projections, lines, producer, _identities, store = _scenario(tmp_path, warm=False)
    monkeypatch.setattr(store, "remember", lambda _readings: None)
    calls = 0

    def requester(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        return json.dumps(
            {
                "schema_version": "episode-reading-text-v1",
                "episodes": [
                    {
                        "episode": 1,
                        "what_happened": "A complete episode was read.",
                        "representatives": [{"asset": 1, "reason": "Grounded frame."}],
                        "cull": [],
                    }
                ],
            }
        )

    with pytest.raises(
        RuntimeError,
        match=r"attempted=1, read_back=0, matched=0",
    ) as error:
        CachedTextEpisodeReader(
            store=store,
            producer=producer,
            annotations=lines,
            requester=requester,
            strict_persistence_readback=True,
        ).read(projections[:1])
    assert "warm-frame" not in str(error.value)

    fail_open = CachedTextEpisodeReader(
        store=store,
        producer=producer,
        annotations=lines,
        requester=requester,
    ).read(projections[:1])
    assert fail_open.episodes[0].reading is not None
    assert calls == 2


def test_strict_readback_aborts_when_semantic_content_differs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projections, lines, producer, identities, store = _scenario(tmp_path, warm=False)
    wrong = BankedEpisodeReading(
        identity=identities[0],
        full_asset_ids=projections[0].group.candidate_ids,
        what_happened="Different persisted meaning.",
        representatives=(EpisodeRepresentative("warm-frame", "Different reason."),),
        cull_decisions=(),
    )
    lookups = 0

    def readings_for(_identities):
        nonlocal lookups
        lookups += 1
        return {} if lookups == 1 else {identities[0].group_id: wrong}

    monkeypatch.setattr(store, "readings_for", readings_for)
    with pytest.raises(RuntimeError, match=r"attempted=1, read_back=1, matched=0"):
        CachedTextEpisodeReader(
            store=store,
            producer=producer,
            annotations=lines,
            requester=lambda _prompt: json.dumps(
                {
                    "schema_version": "episode-reading-text-v1",
                    "episodes": [
                        {
                            "episode": 1,
                            "what_happened": "The expected persisted meaning.",
                            "representatives": [{"asset": 1, "reason": "The expected reason."}],
                            "cull": [],
                        }
                    ],
                }
            ),
            strict_persistence_readback=True,
        ).read(projections[:1])
