"""A text episode reader reuses full-corpus meaning across request scopes.

The cull-evidence and representatives tests return with the cull and structure modules (slice 6).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
from immich_memories.store.episode_readings import (
    BankedEpisodeReading,
    EpisodeReadingIdentity,
    EpisodeReadingProducer,
    EpisodeReadingStore,
    EpisodeRepresentative,
)
from tests.conftest import make_asset


class _AnnotationLines:
    def __init__(
        self,
        lines: dict[str, str],
        *,
        producer_versions: tuple[str, ...] = ("description:student-v1",),
    ) -> None:
        self.lines = lines
        self.requested: tuple[str, ...] = ()
        self.producer_versions = producer_versions

    def lines_for(self, asset_ids: tuple[str, ...]) -> AnnotationLineBatch:
        self.requested = asset_ids
        available = tuple(asset_id for asset_id in asset_ids if asset_id in self.lines)
        return AnnotationLineBatch(
            requested_asset_ids=asset_ids,
            lines=tuple(
                AssetAnnotationLine(asset_id, self.lines[asset_id]) for asset_id in available
            ),
            missing_asset_ids=tuple(
                asset_id for asset_id in asset_ids if asset_id not in self.lines
            ),
            contract=AnnotationContract("annotation-line-v1", self.producer_versions),
        )


def test_scoped_episode_reuses_full_membership_reading_without_a_model_call(
    tmp_path: Path,
) -> None:
    from immich_memories.analysis.text_episode_reader import CachedTextEpisodeReader

    noon = datetime(2026, 8, 25, 12, tzinfo=UTC)
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (
                make_asset("party-context", file_created_at=noon),
                make_asset("friend-in-scope", file_created_at=noon + timedelta(minutes=5)),
                make_asset("later-episode", file_created_at=noon + timedelta(hours=3)),
            )
        ),
    )
    projections = project_episode_groups(prepared, ("friend-in-scope",))
    lines = _AnnotationLines(
        {
            "party-context": "party-context | birthday cake | with family",
            "friend-in-scope": "friend-in-scope | beside the cake | with friend",
        },
        producer_versions=("description:student-v1", "people:immich-live"),
    )
    producer = EpisodeReadingProducer(
        model_id="qwen3-vl-30b",
        prompt_version="episode-prompt-v1",
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:student-v1", "people:immich-live"),
    )
    group = projections[0].group
    identity = EpisodeReadingIdentity.from_annotations(
        group_id=group.group_id,
        producer_key=producer.key(),
        annotation_lines=lines.lines,
    )
    reading = BankedEpisodeReading(
        identity=identity,
        full_asset_ids=group.candidate_ids,
        what_happened="A friend joins a family birthday party.",
        representatives=(
            EpisodeRepresentative("friend-in-scope", "Shows the friend beside the cake."),
        ),
        cull_decisions=(),
    )
    store = EpisodeReadingStore(tmp_path / "annotations.sqlite")
    store.remember((reading,))

    def forbidden_request(_prompt: str) -> str:
        raise AssertionError("a complete banked episode must not be asked again")

    result = CachedTextEpisodeReader(
        store=store,
        producer=producer,
        annotations=lines,
        requester=forbidden_request,
    ).read(projections)

    assert tuple(episode.reading for episode in result.episodes) == (reading,)
    assert result.actual_calls == 0
    assert result.episodes[0].cache_hit is True
    assert lines.requested == ("party-context", "friend-in-scope")


def test_a_cold_episode_is_read_once_then_reused_from_the_bank(tmp_path: Path) -> None:
    from immich_memories.analysis.text_episode_reader import CachedTextEpisodeReader

    noon = datetime(2026, 8, 25, 12, tzinfo=UTC)
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (
                make_asset("party-context", file_created_at=noon),
                make_asset("friend-in-scope", file_created_at=noon + timedelta(minutes=5)),
            )
        ),
    )
    projections = project_episode_groups(prepared, ("friend-in-scope",))
    lines = _AnnotationLines(
        {
            "party-context": "birthday cake | with family | STARRED",
            "friend-in-scope": "beside the cake | with friend | activity=celebration",
        },
        producer_versions=("description:student-v1", "people:immich-live"),
    )
    producer = EpisodeReadingProducer(
        model_id="qwen3-vl-30b",
        prompt_version="episode-prompt-v1",
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:student-v1", "people:immich-live"),
    )
    calls: list[str] = []

    def requester(prompt: str) -> str:
        calls.append(prompt)
        return """{
          "schema_version": "episode-reading-text-v1",
          "episodes": [{
            "episode": 1,
            "what_happened": "A friend joins a family birthday party.",
            "representatives": [{"asset": 2, "reason": "Shows the friend beside the cake."}],
            "cull": []
          }]
        }"""

    store = EpisodeReadingStore(tmp_path / "annotations.sqlite")
    first = CachedTextEpisodeReader(
        store=store,
        producer=producer,
        annotations=lines,
        requester=requester,
    ).read(projections)
    second = CachedTextEpisodeReader(
        store=store,
        producer=producer,
        annotations=lines,
        requester=lambda _prompt: (_ for _ in ()).throw(
            AssertionError("the newly banked episode must not be asked twice")
        ),
    ).read(projections)

    assert first.episodes[0].reading == second.episodes[0].reading
    assert first.episodes[0].reading is not None
    assert first.episodes[0].reading.full_asset_ids == ("party-context", "friend-in-scope")
    assert first.episodes[0].reading.representatives == (
        EpisodeRepresentative("friend-in-scope", "Shows the friend beside the cake."),
    )
    assert first.episodes[0].cache_hit is False
    assert second.episodes[0].cache_hit is True
    assert len(calls) == 1
    assert "birthday cake | with family | STARRED" in calls[0]
    assert "beside the cake | with friend | activity=celebration" in calls[0]
    assert "party-context" not in calls[0]
    assert "friend-in-scope" not in calls[0]


def test_cold_episodes_are_packed_below_the_serialized_prompt_limit(
    tmp_path: Path,
) -> None:
    from immich_memories.analysis.text_episode_reader import (
        CachedTextEpisodeReader,
        TextEpisodeRequestLimits,
    )

    noon = datetime(2026, 8, 25, 12, tzinfo=UTC)
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: tuple(
                make_asset(asset_id, file_created_at=noon + timedelta(hours=index * 3))
                for index, asset_id in enumerate(
                    ("episode-one", "episode-two", "episode-three", "episode-four")
                )
            )
        ),
    )
    projections = project_episode_groups(prepared, prepared.candidate_ids)
    lines = _AnnotationLines(
        {
            asset_id: f"{index} | " + "complete annotation evidence " * 14
            for index, asset_id in enumerate(prepared.candidate_ids, start=1)
        }
    )
    producer = EpisodeReadingProducer(
        model_id="qwen3-vl-30b",
        prompt_version="episode-prompt-v1",
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:student-v1",),
    )
    prompts: list[str] = []

    def requester(prompt: str) -> str:
        prompts.append(prompt)
        aliases = tuple(int(value) for value in re.findall(r"^episode (\d+)$", prompt, re.M))
        return json.dumps(
            {
                "schema_version": "episode-reading-text-v1",
                "episodes": [
                    {
                        "episode": alias,
                        "what_happened": "One complete episode happened.",
                        "representatives": [
                            {"asset": 1, "reason": "The only frame represents it."}
                        ],
                        "cull": [],
                    }
                    for alias in aliases
                ],
            }
        )

    store = EpisodeReadingStore(tmp_path / "annotations.sqlite")
    first = CachedTextEpisodeReader(
        store=store,
        producer=producer,
        annotations=lines,
        requester=requester,
        limits=TextEpisodeRequestLimits(max_prompt_chars=1_400),
    ).read(projections)

    assert len(prompts) > 1
    assert all(len(prompt) <= 1_400 for prompt in prompts)
    assert all(asset_id not in "".join(prompts) for asset_id in prepared.candidate_ids)
    assert all(episode.reading is not None for episode in first.episodes)
    assert first.actual_calls == len(prompts)

    warm = CachedTextEpisodeReader(
        store=store,
        producer=producer,
        annotations=lines,
        requester=lambda _prompt: (_ for _ in ()).throw(
            AssertionError("packed episode readings should be independently reusable")
        ),
        limits=TextEpisodeRequestLimits(max_prompt_chars=1_400),
    ).read(projections)

    assert warm.actual_calls == 0
    assert all(episode.cache_hit for episode in warm.episodes)


def test_an_oversized_episode_is_paged_then_banked_as_one_full_reading(
    tmp_path: Path,
) -> None:
    from immich_memories.analysis.text_episode_reader import (
        CachedTextEpisodeReader,
        TextEpisodeRequestLimits,
    )

    start = datetime(2026, 8, 25, 8, tzinfo=UTC)
    assets = tuple(
        make_asset(f"private-frame-{index:03d}", file_created_at=start + timedelta(minutes=index))
        for index in range(95)
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(source_fetcher=lambda _scope: assets),
    )
    projections = project_episode_groups(prepared, (prepared.candidate_ids[-1],))
    lines = _AnnotationLines(
        {
            asset_id: f"complete annotation for chronological frame {index}"
            for index, asset_id in enumerate(prepared.candidate_ids, start=1)
        }
    )
    producer = EpisodeReadingProducer(
        model_id="qwen3-vl-30b",
        prompt_version="episode-prompt-v1",
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:student-v1",),
    )
    prompts: list[str] = []

    def requester(prompt: str) -> str:
        prompts.append(prompt)
        page = len(prompts)
        return json.dumps(
            {
                "schema_version": "episode-reading-text-v1",
                "episodes": [
                    {
                        "episode": 1,
                        "what_happened": (
                            "The group starts a long activity."
                            if page == 1
                            else "The group finishes the activity together."
                        ),
                        "representatives": [{"asset": 1, "reason": f"Represents page {page}."}],
                        "cull": ([] if page == 1 else [{"asset": 5, "bucket": "notes"}]),
                    }
                ],
            }
        )

    store = EpisodeReadingStore(tmp_path / "annotations.sqlite")
    result = CachedTextEpisodeReader(
        store=store,
        producer=producer,
        annotations=lines,
        requester=requester,
        limits=TextEpisodeRequestLimits(max_assets_per_page=90),
    ).read(projections)

    assert result.actual_calls == 2
    assert [len(re.findall(r"^  asset ", prompt, re.M)) for prompt in prompts] == [90, 5]
    reading = result.episodes[0].reading
    assert reading is not None
    assert reading.full_asset_ids == prepared.candidate_ids
    assert tuple(item.asset_id for item in reading.representatives) == (
        "private-frame-000",
        "private-frame-090",
    )
    assert tuple(item.asset_id for item in reading.cull_decisions) == ("private-frame-094",)
    assert "starts a long activity" in reading.what_happened
    assert "finishes the activity" in reading.what_happened

    warm = CachedTextEpisodeReader(
        store=store,
        producer=producer,
        annotations=lines,
        requester=lambda _prompt: (_ for _ in ()).throw(
            AssertionError("a merged full-episode reading should be reused")
        ),
        limits=TextEpisodeRequestLimits(max_assets_per_page=90),
    ).read(projections)

    assert warm.actual_calls == 0
    assert warm.episodes[0].cache_hit is True


def test_episode_pages_shrink_to_the_serialized_prompt_limit(tmp_path: Path) -> None:
    from immich_memories.analysis.text_episode_reader import (
        CachedTextEpisodeReader,
        TextEpisodeRequestLimits,
    )

    start = datetime(2026, 8, 25, 8, tzinfo=UTC)
    assets = tuple(
        make_asset(f"private-frame-{index:03d}", file_created_at=start + timedelta(minutes=index))
        for index in range(12)
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(source_fetcher=lambda _scope: assets),
    )
    projections = project_episode_groups(prepared, prepared.candidate_ids)
    lines = _AnnotationLines(
        {
            asset_id: f"frame {index} | " + "complete rendered evidence " * 12
            for index, asset_id in enumerate(prepared.candidate_ids, start=1)
        }
    )
    producer = EpisodeReadingProducer(
        model_id="qwen3-vl-30b",
        prompt_version="episode-prompt-v1",
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:student-v1",),
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
                        "what_happened": "One part of the complete episode is visible.",
                        "representatives": [
                            {"asset": 1, "reason": "Represents this bounded page."}
                        ],
                        "cull": [],
                    }
                ],
            }
        )

    result = CachedTextEpisodeReader(
        store=EpisodeReadingStore(tmp_path / "annotations.sqlite"),
        producer=producer,
        annotations=lines,
        requester=requester,
        limits=TextEpisodeRequestLimits(
            max_prompt_chars=1_400,
            max_assets_per_page=90,
        ),
    ).read(projections)

    assert len(prompts) > 1
    assert all(len(prompt) <= 1_400 for prompt in prompts)
    assert result.actual_calls == len(prompts)
    assert result.episodes[0].reading is not None
    assert result.episodes[0].reading.full_asset_ids == prepared.candidate_ids


def test_an_episode_omitted_from_a_pack_is_reasked_alone(
    tmp_path: Path,
) -> None:
    from immich_memories.analysis.text_episode_reader import (
        CachedTextEpisodeReader,
        TextEpisodeRequestLimits,
    )

    start = datetime(2026, 8, 25, 8, tzinfo=UTC)
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (
                make_asset("first-private", file_created_at=start),
                make_asset("second-private", file_created_at=start + timedelta(hours=3)),
            )
        ),
    )
    projections = project_episode_groups(prepared, prepared.candidate_ids)
    lines = _AnnotationLines(
        {
            "first-private": "the first complete episode line",
            "second-private": "the second complete episode line",
        }
    )
    producer = EpisodeReadingProducer(
        model_id="qwen3-vl-30b",
        prompt_version="episode-prompt-v1",
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:student-v1",),
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
                        "what_happened": "A complete episode is visible.",
                        "representatives": [
                            {"asset": 1, "reason": "The frame represents this episode."}
                        ],
                        "cull": [],
                    }
                ],
            }
        )

    result = CachedTextEpisodeReader(
        store=EpisodeReadingStore(tmp_path / "annotations.sqlite"),
        producer=producer,
        annotations=lines,
        requester=requester,
        limits=TextEpisodeRequestLimits(unread_retry_rounds=2),
    ).read(projections)

    assert result.actual_calls == 2
    assert all(episode.reading is not None for episode in result.episodes)
    assert "the first complete episode line" in prompts[0]
    assert "the second complete episode line" in prompts[0]
    assert "the first complete episode line" not in prompts[1]
    assert "the second complete episode line" in prompts[1]


def test_an_invalid_episode_answer_retains_full_membership_and_is_not_banked(
    tmp_path: Path,
) -> None:
    from immich_memories.analysis.text_episode_reader import CachedTextEpisodeReader

    noon = datetime(2026, 8, 25, 12, tzinfo=UTC)
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (
                make_asset("context", file_created_at=noon),
                make_asset("in-scope", file_created_at=noon + timedelta(minutes=5)),
            )
        ),
    )
    projections = project_episode_groups(prepared, ("in-scope",))
    lines = _AnnotationLines(
        {
            "context": "family gathered around a table",
            "in-scope": "friend arriving at the table",
        }
    )
    producer = EpisodeReadingProducer(
        model_id="qwen3-vl-30b",
        prompt_version="episode-prompt-v1",
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:student-v1",),
    )
    store = EpisodeReadingStore(tmp_path / "annotations.sqlite")
    invalid_prompts: list[str] = []

    def invalid_requester(prompt: str) -> str:
        invalid_prompts.append(prompt)
        return '{"schema_version":"wrong","episodes":[]}'

    invalid = CachedTextEpisodeReader(
        store=store,
        producer=producer,
        annotations=lines,
        requester=invalid_requester,
    ).read(projections)

    assert invalid.actual_calls == 3
    assert len(invalid_prompts) == 3
    assert invalid.episodes[0].projection.group.candidate_ids == ("context", "in-scope")
    assert invalid.episodes[0].reading is None
    assert "invalid" in (invalid.episodes[0].unavailable_reason or "")

    retried = CachedTextEpisodeReader(
        store=store,
        producer=producer,
        annotations=lines,
        requester=lambda _prompt: (
            """{
          "schema_version": "episode-reading-text-v1",
          "episodes": [{
            "episode": 1,
            "what_happened": "A friend joins a family gathering.",
            "representatives": [{"asset": 2, "reason": "Shows the friend arriving."}],
            "cull": []
          }]
        }"""
        ),
    ).read(projections)

    assert retried.actual_calls == 1
    assert retried.episodes[0].reading is not None
    assert retried.episodes[0].cache_hit is False


def test_one_schema_envelope_remains_usable_when_followed_by_model_prose(
    tmp_path: Path,
) -> None:
    from immich_memories.analysis.text_episode_reader import CachedTextEpisodeReader

    source = make_asset("private-frame")
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(source_fetcher=lambda _scope: (source,)),
    )
    projections = project_episode_groups(prepared, prepared.candidate_ids)
    lines = _AnnotationLines({"private-frame": "one complete rendered evidence line"})
    producer = EpisodeReadingProducer(
        model_id="qwen3-vl-30b",
        prompt_version="episode-prompt-v1",
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:student-v1",),
    )
    envelope = json.dumps(
        {
            "schema_version": "episode-reading-text-v1",
            "episodes": [
                {
                    "episode": 1,
                    "what_happened": "One complete episode is visible.",
                    "representatives": [{"asset": 1, "reason": "The only frame represents it."}],
                    "cull": [],
                }
            ],
        }
    )
    answer = envelope + "\nAdditional prose that was not requested."

    result = CachedTextEpisodeReader(
        store=EpisodeReadingStore(tmp_path / "annotations.sqlite"),
        producer=producer,
        annotations=lines,
        requester=lambda _prompt: answer,
    ).read(projections)

    assert result.episodes[0].reading is not None
    assert result.diagnostics.embedded_json_envelopes == 1
    assert result.diagnostics.discarded_invalid_cull_rows == 0
    assert result.diagnostics.discarded_conflicting_cull_rows == 0
    assert (
        result.diagnostics.responses[0].response_sha256
        == hashlib.sha256(answer.encode()).hexdigest()
    )


def test_invalid_or_conflicting_cull_rows_are_discarded_without_becoming_cuts(
    tmp_path: Path,
) -> None:
    from immich_memories.analysis.text_episode_reader import CachedTextEpisodeReader

    noon = datetime(2026, 8, 25, 12, tzinfo=UTC)
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (
                make_asset("representative", file_created_at=noon),
                make_asset("failed-frame", file_created_at=noon + timedelta(minutes=1)),
                make_asset("unknown-bucket", file_created_at=noon + timedelta(minutes=2)),
            )
        ),
    )
    projections = project_episode_groups(prepared, prepared.candidate_ids)
    lines = _AnnotationLines(
        dict.fromkeys(prepared.candidate_ids, "one complete rendered evidence line")
    )
    producer = EpisodeReadingProducer(
        model_id="qwen3-vl-30b",
        prompt_version="episode-prompt-v1",
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:student-v1",),
    )
    answer = json.dumps(
        {
            "schema_version": "episode-reading-text-v1",
            "episodes": [
                {
                    "episode": 1,
                    "what_happened": "A short sequence was recorded.",
                    "representatives": [
                        {"asset": 1, "reason": "Carries the usable episode meaning."}
                    ],
                    "cull": [
                        {"asset": 1, "bucket": "notes"},
                        {"asset": 2, "bucket": "failed"},
                        {"asset": 3, "bucket": "ordinary"},
                    ],
                }
            ],
        }
    )

    result = CachedTextEpisodeReader(
        store=EpisodeReadingStore(tmp_path / "annotations.sqlite"),
        producer=producer,
        annotations=lines,
        requester=lambda _prompt: answer,
    ).read(projections)

    reading = result.episodes[0].reading
    assert reading is not None
    assert tuple((row.asset_id, row.bucket) for row in reading.cull_decisions) == (
        ("failed-frame", "failed"),
    )
    assert result.diagnostics.discarded_invalid_cull_rows == 1
    assert result.diagnostics.discarded_conflicting_cull_rows == 1
    assert (
        result.diagnostics.responses[0].response_sha256
        == hashlib.sha256(answer.encode()).hexdigest()
    )


def test_invalid_representative_rows_are_discarded_when_one_grounded_row_survives(
    tmp_path: Path,
) -> None:
    from immich_memories.analysis.text_episode_reader import CachedTextEpisodeReader

    noon = datetime(2026, 8, 25, 12, tzinfo=UTC)
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: tuple(
                make_asset(f"frame-{index}", file_created_at=noon + timedelta(minutes=index))
                for index in range(2)
            )
        ),
    )
    projections = project_episode_groups(prepared, prepared.candidate_ids)
    answer = json.dumps(
        {
            "schema_version": "episode-reading-text-v1",
            "episodes": [
                {
                    "episode": 1,
                    "what_happened": "Two grounded frames show the same short visit.",
                    "representatives": [
                        {"asset": 99, "reason": "Ungrounded alias."},
                        {"asset": 1, "reason": "Grounded representative."},
                        {"asset": 1, "reason": "Duplicate representative."},
                    ],
                    "cull": [],
                }
            ],
        }
    )

    result = CachedTextEpisodeReader(
        store=EpisodeReadingStore(tmp_path / "annotations.sqlite"),
        producer=EpisodeReadingProducer(
            model_id="qwen3-vl-30b",
            prompt_version="episode-prompt-v1",
            schema_version="episode-schema-v1",
            annotation_renderer_version="annotation-line-v1",
            annotation_versions=("description:student-v1",),
        ),
        annotations=_AnnotationLines(
            dict.fromkeys(prepared.candidate_ids, "one complete rendered evidence line")
        ),
        requester=lambda _prompt: answer,
    ).read(projections)

    reading = result.episodes[0].reading
    assert reading is not None
    assert tuple(row.asset_id for row in reading.representatives) == ("frame-0",)
    assert result.diagnostics.discarded_invalid_representative_rows == 2
    assert (
        result.diagnostics.responses[0].response_sha256
        == hashlib.sha256(answer.encode()).hexdigest()
    )


def test_output_packing_and_transport_budget_share_one_bounded_estimate(tmp_path: Path) -> None:
    from immich_memories.analysis.text_episode_reader import CachedTextEpisodeReader

    noon = datetime(2026, 8, 25, 12, tzinfo=UTC)
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: tuple(
                make_asset(
                    f"episode-{index:02d}",
                    file_created_at=noon + timedelta(hours=index * 3),
                )
                for index in range(90)
            )
        ),
    )
    calls: list[tuple[int, int]] = []

    class BudgetedRequester:
        def __call__(self, _prompt: str) -> str:
            raise AssertionError("production episode requests must carry their computed budget")

        def request_with_budget(self, prompt: str, *, max_tokens: int) -> str:
            aliases = tuple(int(value) for value in re.findall(r"^episode (\d+)$", prompt, re.M))
            calls.append((len(aliases), max_tokens))
            return json.dumps(
                {
                    "schema_version": "episode-reading-text-v1",
                    "episodes": [
                        {
                            "episode": alias,
                            "what_happened": "One grounded frame.",
                            "representatives": [{"asset": 1, "reason": "Only frame."}],
                            "cull": [],
                        }
                        for alias in aliases
                    ],
                }
            )

    result = CachedTextEpisodeReader(
        store=EpisodeReadingStore(tmp_path / "annotations.sqlite"),
        producer=EpisodeReadingProducer(
            model_id="qwen3-vl-30b",
            prompt_version="episode-prompt-v1",
            schema_version="episode-schema-v1",
            annotation_renderer_version="annotation-line-v1",
            annotation_versions=("description:student-v1",),
        ),
        annotations=_AnnotationLines(
            dict.fromkeys(prepared.candidate_ids, "one short complete evidence line")
        ),
        requester=BudgetedRequester(),
    ).read(project_episode_groups(prepared, prepared.candidate_ids))

    assert [rows for rows, _budget in calls] == [27, 27, 27, 9]
    assert [budget for _rows, budget in calls] == [3980, 3980, 3980, 1460]
    assert result.actual_calls == 4
    assert all(episode.reading is not None for episode in result.episodes)


def test_a_provider_failure_retains_the_episode_and_leaves_the_bank_cold(
    tmp_path: Path,
) -> None:
    from immich_memories.analysis.text_episode_reader import CachedTextEpisodeReader

    noon = datetime(2026, 8, 25, 12, tzinfo=UTC)
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (
                make_asset("context", file_created_at=noon),
                make_asset("in-scope", file_created_at=noon + timedelta(minutes=5)),
            )
        ),
    )
    projections = project_episode_groups(prepared, ("in-scope",))
    lines = _AnnotationLines(
        {
            "context": "family gathered around a table",
            "in-scope": "friend arriving at the table",
        }
    )
    producer = EpisodeReadingProducer(
        model_id="qwen3-vl-30b",
        prompt_version="episode-prompt-v1",
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:student-v1",),
    )
    store = EpisodeReadingStore(tmp_path / "annotations.sqlite")

    failed = CachedTextEpisodeReader(
        store=store,
        producer=producer,
        annotations=lines,
        requester=lambda _prompt: (_ for _ in ()).throw(TimeoutError("provider timed out")),
    ).read(projections)

    assert failed.actual_calls == 1
    assert failed.episodes[0].projection.group.candidate_ids == ("context", "in-scope")
    assert failed.episodes[0].reading is None
    assert "provider timed out" in (failed.episodes[0].unavailable_reason or "")

    retried = CachedTextEpisodeReader(
        store=store,
        producer=producer,
        annotations=lines,
        requester=lambda _prompt: (
            """{
          "schema_version": "episode-reading-text-v1",
          "episodes": [{
            "episode": 1,
            "what_happened": "A friend joins a family gathering.",
            "representatives": [{"asset": 2, "reason": "Shows the friend arriving."}],
            "cull": []
          }]
        }"""
        ),
    ).read(projections)

    assert retried.actual_calls == 1
    assert retried.episodes[0].reading is not None
    assert retried.episodes[0].cache_hit is False


def test_an_episode_with_incomplete_annotations_is_retained_without_being_sent(
    tmp_path: Path,
) -> None:
    from immich_memories.analysis.text_episode_reader import CachedTextEpisodeReader

    noon = datetime(2026, 8, 25, 12, tzinfo=UTC)
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (
                make_asset("missing-annotations", file_created_at=noon),
                make_asset("in-scope", file_created_at=noon + timedelta(minutes=5)),
            )
        ),
    )
    projections = project_episode_groups(prepared, ("in-scope",))
    producer = EpisodeReadingProducer(
        model_id="qwen3-vl-30b",
        prompt_version="episode-prompt-v1",
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:student-v1",),
    )

    def forbidden_request(_prompt: str) -> str:
        raise AssertionError("an incomplete episode must not be sent as complete evidence")

    result = CachedTextEpisodeReader(
        store=EpisodeReadingStore(tmp_path / "annotations.sqlite"),
        producer=producer,
        annotations=_AnnotationLines({"in-scope": "friend at a party"}),
        requester=forbidden_request,
    ).read(projections)

    assert result.actual_calls == 0
    assert result.episodes[0].projection.group.candidate_ids == (
        "missing-annotations",
        "in-scope",
    )
    assert result.episodes[0].reading is None
    assert "annotation" in (result.episodes[0].unavailable_reason or "")
