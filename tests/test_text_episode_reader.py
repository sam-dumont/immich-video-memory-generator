"""A text episode reader reuses full-corpus meaning across request scopes."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from immich_memories.analysis.annotation_lines import (
    AnnotationContract,
    AnnotationLineBatch,
    AssetAnnotationLine,
)
from immich_memories.analysis.editorial_contracts import DecisionProvenance
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.analysis.selection_source_groups import project_episode_groups
from immich_memories.operations.cut_progress import StageUpdate, announcing_stages
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
    announced: list[StageUpdate] = []
    with announcing_stages(announced.append):
        first = CachedTextEpisodeReader(
            store=store,
            producer=producer,
            annotations=lines,
            requester=requester,
            limits=TextEpisodeRequestLimits(max_prompt_chars=2_246),
        ).read(projections)

    assert len(prompts) > 1
    # Every model request announces where the reading is, so the long silent
    # stretch of a cut becomes "Reading event evidence: 2/3" on both surfaces.
    assert [(u.done, u.total) for u in announced if u.counted] == [
        (index, len(prompts)) for index in range(1, len(prompts) + 1)
    ]
    assert announced[0].stage_label == f"Reading event evidence: 1/{len(prompts)}"
    assert all(len(prompt) <= 2_246 for prompt in prompts)
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
        limits=TextEpisodeRequestLimits(max_prompt_chars=2_246),
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
            max_prompt_chars=2_246,
            max_assets_per_page=90,
        ),
    ).read(projections)

    assert len(prompts) > 1
    assert all(len(prompt) <= 2_246 for prompt in prompts)
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

    valid = """{
      "schema_version": "episode-reading-text-v1",
      "episodes": [{
        "episode": 1,
        "what_happened": "A friend joins a family gathering.",
        "representatives": [{"asset": 2, "reason": "Shows the friend arriving."}],
        "cull": []
      }]
    }"""

    # The refusal is banked under this exact question, so the next run does not buy it again.
    again = CachedTextEpisodeReader(
        store=store, producer=producer, annotations=lines, requester=lambda _p: valid
    ).read(projections)

    assert again.actual_calls == 0
    assert again.episodes[0].reading is None
    assert again.episodes[0].cache_hit is False

    # A changed prompt is a changed question, so it is asked once more.
    bumped = CachedTextEpisodeReader(
        store=store,
        producer=replace(producer, prompt_version="episode-prompt-v2"),
        annotations=lines,
        requester=lambda _p: valid,
    ).read(projections)

    assert bumped.actual_calls == 1
    assert bumped.episodes[0].reading is not None


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

    valid = """{
      "schema_version": "episode-reading-text-v1",
      "episodes": [{
        "episode": 1,
        "what_happened": "A friend joins a family gathering.",
        "representatives": [{"asset": 2, "reason": "Shows the friend arriving."}],
        "cull": []
      }]
    }"""

    # A provider that never answered has refused nothing, so the next run asks again.
    again = CachedTextEpisodeReader(
        store=store, producer=producer, annotations=lines, requester=lambda _p: valid
    ).read(projections)

    assert again.actual_calls == 1
    assert again.episodes[0].reading is not None
    assert again.episodes[0].cache_hit is False


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


def test_text_cull_evidence_uses_the_existing_favourite_and_trace_policy(
    tmp_path: Path,
) -> None:
    from immich_memories.analysis.selection_cull import run_cull_decisions
    from immich_memories.analysis.text_episode_reader import CachedTextEpisodeReader

    noon = datetime(2026, 8, 25, 12, tzinfo=UTC)
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (
                make_asset("receipt", file_created_at=noon),
                make_asset(
                    "starred-screen",
                    file_created_at=noon + timedelta(minutes=1),
                    is_favorite=True,
                ),
                make_asset("keeper", file_created_at=noon + timedelta(minutes=2)),
            )
        ),
    )
    projections = project_episode_groups(prepared, prepared.candidate_ids)
    producer = EpisodeReadingProducer(
        model_id="qwen3-vl-30b",
        prompt_version="episode-prompt-v1",
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:student-v1",),
    )
    episode_result = CachedTextEpisodeReader(
        store=EpisodeReadingStore(tmp_path / "annotations.sqlite"),
        producer=producer,
        annotations=_AnnotationLines(
            {
                "receipt": "a photographed receipt",
                "starred-screen": "a starred photograph of a television",
                "keeper": "family gathered in the room",
            }
        ),
        requester=lambda _prompt: (
            """{
          "schema_version": "episode-reading-text-v1",
          "episodes": [{
            "episode": 1,
            "what_happened": "A family gathering includes two record shots.",
            "representatives": [{"asset": 3, "reason": "Shows the family gathering."}],
            "cull": [
              {"asset": 1, "bucket": "notes"},
              {"asset": 2, "bucket": "notes"}
            ]
          }]
        }"""
        ),
    ).read(projections)

    culled = run_cull_decisions(
        prepared,
        episode_result.cull_decisions,
        provenance=DecisionProvenance(
            pass_name="pass-1-cull",  # noqa: S106 -- editorial pass label, not a credential.
            pass_version="pass-1-v1",  # noqa: S106 -- provenance version, not a credential.
            schema_version="episode-reading-text-v1",
            model_identity=producer.model_id,
            input_ids=prepared.candidate_ids,
            sheet_hashes=(),
            request_key="text-pass-zero",
            cache_hit=False,
        ),
        warnings=episode_result.warnings,
        actual_calls=episode_result.actual_calls,
    )

    assert tuple(candidate.asset_id for candidate in culled.survivors) == (
        "starred-screen",
        "keeper",
    )
    assert tuple(decision.asset_id for decision in culled.rejected) == ("receipt",)
    assert any("protected favourite" in warning for warning in culled.warnings)
    assert culled.trace.conservation.valid is True


def test_text_representatives_drive_structure_without_reducing_the_reservoir(
    tmp_path: Path,
) -> None:
    from immich_memories.analysis.selection_structure import build_structure_workprint
    from immich_memories.analysis.text_episode_reader import CachedTextEpisodeReader

    noon = datetime(2026, 8, 25, 12, tzinfo=UTC)
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: tuple(
                make_asset(asset_id, file_created_at=noon + timedelta(minutes=index))
                for index, asset_id in enumerate(("average", "action", "portrait"))
            )
        ),
    )
    projections = project_episode_groups(prepared, ("portrait",))
    producer = EpisodeReadingProducer(
        model_id="qwen3-vl-30b",
        prompt_version="episode-prompt-v1",
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:student-v1",),
    )
    episode_result = CachedTextEpisodeReader(
        store=EpisodeReadingStore(tmp_path / "annotations.sqlite"),
        producer=producer,
        annotations=_AnnotationLines(
            {
                "average": "people waiting beside a race course",
                "action": "the owner running through the race",
                "portrait": "a friend cheering beside the course",
            }
        ),
        requester=lambda _prompt: (
            """{
          "schema_version": "episode-reading-text-v1",
          "episodes": [{
            "episode": 1,
            "what_happened": "The owner runs a race while a friend cheers.",
            "representatives": [
              {"asset": 2, "reason": "Shows the owner actually running."},
              {"asset": 3, "reason": "Shows the friend cheering."}
            ],
            "cull": []
          }]
        }"""
        ),
    ).read(projections)

    workprint = build_structure_workprint(
        prepared,
        prepared.candidates,
        representative_resolver=episode_result.representative_for,
    )

    assert workprint.representative_ids == ("action",)
    assert workprint.moments[0].candidate_ids == ("average", "action", "portrait")


def test_a_swallowed_provider_failure_names_the_rejecting_check_once_in_the_log(
    tmp_path: Path,
    caplog,
) -> None:
    import logging as _logging

    from immich_memories.analysis.text_episode_reader import (
        CachedTextEpisodeReader,
        TextEpisodeRequestLimits,
    )

    noon = datetime(2026, 8, 25, 12, tzinfo=UTC)
    later = noon + timedelta(days=3)
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (
                make_asset("morning", file_created_at=noon),
                make_asset("evening", file_created_at=later),
            )
        ),
    )
    projections = project_episode_groups(prepared, ("morning", "evening"))
    # Long enough that the two groups cannot share one request, so both packs fail alike.
    described = "a table laid out with plates and glasses for a long lunch " * 8
    lines = _AnnotationLines({"morning": described, "evening": described})
    producer = EpisodeReadingProducer(
        model_id="glm-5.3-flash",
        prompt_version="episode-prompt-v1",
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:student-v1",),
    )

    with caplog.at_level(_logging.WARNING, logger="immich_memories.analysis.text_episode_reader"):
        result = CachedTextEpisodeReader(
            store=EpisodeReadingStore(tmp_path / "annotations.sqlite"),
            producer=producer,
            annotations=lines,
            requester=lambda _prompt: (_ for _ in ()).throw(
                ValueError("LLM provider returned no choices: ['code', 'msg']")
            ),
            limits=TextEpisodeRequestLimits(max_prompt_chars=2201),
        ).read(projections)

    assert all(episode.reading is None for episode in result.episodes)
    assert result.actual_calls == 2
    assert [record.getMessage() for record in caplog.records] == [
        "text episode provider failed (ValueError): LLM provider returned no choices: "
        "['code', 'msg']"
    ]


def test_a_cold_episode_can_be_read_from_a_provider_batch(tmp_path: Path) -> None:
    """The whole stage is offered at once, and what the queue answers is never asked live."""
    import httpx

    from immich_memories.analysis.editorial_text_gateway import SyncTextPromptRequester
    from immich_memories.analysis.llm_batch import BatchCoordinator, BatchPolicy
    from immich_memories.analysis.text_episode_reader import (
        TEXT_EPISODE_MAX_OUTPUT_TOKENS,
        CachedTextEpisodeReader,
    )
    from immich_memories.config_models_llm import LLMConfig

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
            "party-context": "birthday cake | with family",
            "friend-in-scope": "beside the cake | with friend",
        }
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
                    "what_happened": "A friend joins a family birthday party.",
                    "representatives": [{"asset": 2, "reason": "Beside the cake."}],
                    "cull": [],
                }
            ],
        }
    )
    queued: list[str] = []

    def wire(request: httpx.Request) -> httpx.Response:
        # WHY: replaces the provider's HTTP endpoint, the one external boundary.
        path = request.url.path
        if path.endswith("/files") and request.method == "POST":
            queued.extend(re.findall(r'"custom_id": "([0-9a-f]+)"', request.content.decode()))
            return httpx.Response(200, json={"id": "in"})
        if path.endswith("/content"):
            return httpx.Response(
                200,
                text="\n".join(
                    json.dumps(
                        {
                            "custom_id": key,
                            "response": {
                                "status_code": 200,
                                "body": {"choices": [{"message": {"content": answer}}]},
                            },
                        }
                    )
                    for key in queued
                ),
            )
        if "/batches/" in path:
            return httpx.Response(200, json={"status": "completed", "output_file_id": "out"})
        if request.method == "POST":
            return httpx.Response(200, json={"id": "b1"})
        if path.endswith("/batches"):
            return httpx.Response(200, json={"data": []})
        raise AssertionError(f"a batched answer must not be asked live: {path}")

    config = LLMConfig(provider="openai", model="m", api_key="k", batch="auto")
    coordinator = BatchCoordinator(
        config,
        BatchPolicy(mode="auto", min_requests=1, max_wait_minutes=5),
        client_factory=lambda headers: httpx.AsyncClient(
            transport=httpx.MockTransport(wire), headers=headers
        ),
    )
    result = CachedTextEpisodeReader(
        store=EpisodeReadingStore(tmp_path / "annotations.sqlite"),
        producer=producer,
        annotations=lines,
        requester=SyncTextPromptRequester(
            config,
            max_tokens=TEXT_EPISODE_MAX_OUTPUT_TOKENS,
            timeout_seconds=30,
            batch=coordinator,
        ),
    ).read(projections)

    assert len(queued) == 1
    assert result.episodes[0].reading is not None
    assert result.episodes[0].reading.what_happened == "A friend joins a family birthday party."


def test_a_reading_names_the_moment_worth_a_record_and_it_survives_the_cull(
    tmp_path: Path,
) -> None:
    """A picture the reading calls a record cannot also be thrown out as a Cull reject."""
    from immich_memories.analysis.text_episode_reader import CachedTextEpisodeReader

    noon = datetime(2026, 8, 25, 12, tzinfo=UTC)
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (
                make_asset("the-room", file_created_at=noon),
                make_asset("first-steps", file_created_at=noon + timedelta(minutes=5)),
            )
        ),
    )
    projections = project_episode_groups(prepared, prepared.candidate_ids)
    lines = _AnnotationLines(
        {
            "the-room": "the-room | the living room",
            "first-steps": "first-steps | a toddler walking unaided",
        }
    )
    producer = EpisodeReadingProducer(
        model_id="a-model",
        prompt_version="episode-prompt-v3",
        schema_version="episode-schema-v1",
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:student-v1",),
    )

    def answer(_prompt: str) -> str:
        return json.dumps(
            {
                "schema_version": "episode-reading-text-v1",
                "episodes": [
                    {
                        "episode": 1,
                        "what_happened": "An afternoon in the living room.",
                        "representatives": [{"asset": 1, "reason": "Shows the room."}],
                        "notable_moments": [{"asset": 2, "reason": "walking unaided"}],
                        "cull": [{"asset": 2, "bucket": "failed"}],
                    }
                ],
            }
        )

    store = EpisodeReadingStore(tmp_path / "annotations.sqlite")
    reading = (
        CachedTextEpisodeReader(store=store, producer=producer, annotations=lines, requester=answer)
        .read(projections)
        .episodes[0]
        .reading
    )

    assert reading is not None
    assert reading.notable_moments == (EpisodeRepresentative("first-steps", "walking unaided"),)
    assert reading.cull_decisions == ()
