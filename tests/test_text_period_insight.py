"""The period reader binds banked episode meaning without seeing pixels or IDs."""

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
from immich_memories.analysis.editorial_contracts import InsightEvidence
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
    prepare_editorial_source,
)
from immich_memories.analysis.selection_source_groups import project_episode_groups
from immich_memories.analysis.text_episode_reader import (
    EpisodeEditorialEvidence,
    TextEpisodeReadResult,
)
from immich_memories.store.episode_readings import (
    BankedEpisodeReading,
    EpisodeReadingIdentity,
    EpisodeRepresentative,
)
from immich_memories.store.period_insights import PeriodInsightProducer, PeriodInsightStore
from tests.conftest import make_asset


@pytest.mark.parametrize(
    "envelope",
    (
        "{response}",
        "{response}\nNote: The reading uses the supplied episode table.",
        "```json\n{response}\n```\nNote: The reading uses the supplied episode table.",
    ),
)
def test_cold_period_is_read_once_then_reused_from_exact_episode_grounding(
    tmp_path: Path,
    envelope: str,
) -> None:
    from immich_memories.analysis.text_period_insight import run_text_period_insight

    noon = datetime(2026, 8, 25, 12, tzinfo=UTC)
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (
                make_asset("private-morning-context", file_created_at=noon),
                make_asset(
                    "private-morning-star",
                    file_created_at=noon + timedelta(minutes=5),
                    is_favorite=True,
                ),
                make_asset("private-finish", file_created_at=noon + timedelta(hours=3)),
            )
        ),
    )
    projections = project_episode_groups(prepared, prepared.candidate_ids)
    annotation_batch = AnnotationLineBatch(
        requested_asset_ids=prepared.candidate_ids,
        lines=tuple(
            AssetAnnotationLine(asset_id, f"description for line {index}")
            for index, asset_id in enumerate(prepared.candidate_ids, start=1)
        ),
        missing_asset_ids=(),
        contract=AnnotationContract("annotation-line-v1", ("description:test-v1",)),
    )
    summaries = (
        "The family packs together before leaving.",
        "They finish an afternoon race.",
    )
    representative_ids = ("private-morning-star", "private-finish")
    episode_evidence = []
    for projection, summary, representative_id in zip(
        projections,
        summaries,
        representative_ids,
        strict=True,
    ):
        identity = EpisodeReadingIdentity.from_annotations(
            group_id=projection.group.group_id,
            producer_key="episode-reader-v1",
            annotation_lines={
                asset_id: annotation_batch.as_mapping()[asset_id]
                for asset_id in projection.group.candidate_ids
            },
        )
        reading = BankedEpisodeReading(
            identity=identity,
            full_asset_ids=projection.group.candidate_ids,
            what_happened=summary,
            representatives=(EpisodeRepresentative(representative_id, "Carries the episode."),),
            cull_decisions=(),
        )
        episode_evidence.append(
            EpisodeEditorialEvidence(
                projection=projection,
                identity=identity,
                reading=reading,
                cache_hit=True,
                unavailable_reason=None,
            )
        )
    episode_result = TextEpisodeReadResult(
        episodes=tuple(episode_evidence),
        annotation_batch=annotation_batch,
        warnings=(),
        actual_calls=0,
    )
    producer = PeriodInsightProducer(
        model_id="qwen3-vl-30b",
        prompt_version="period-text-v1",
        schema_version="period-insight-text-v1",
    )
    prompts: list[str] = []

    def requester(prompt: str) -> str:
        prompts.append(prompt)
        response = """{
          "schema_version": "period-insight-text-v1",
          "thesis": "Preparation gives way to the relief of finishing.",
          "evidence": [{
            "observation": "The morning preparation resolves at the finish.",
            "episodes": [1, 2]
          }],
          "tensions": ["Nerves before the race versus relief afterward."],
          "recurring_threads": ["Showing up for difficult things."]
        }"""
        return envelope.format(response=response)

    store = PeriodInsightStore(tmp_path / "annotations.sqlite")
    first = run_text_period_insight(
        episode_result,
        store=store,
        producer=producer,
        requester=requester,
    )
    second = run_text_period_insight(
        episode_result,
        store=store,
        producer=producer,
        requester=lambda _prompt: (_ for _ in ()).throw(
            AssertionError("the exact period grounding must be banked")
        ),
    )

    assert first.insight.thesis == "Preparation gives way to the relief of finishing."
    assert first.insight.evidence == (
        InsightEvidence(
            observation="The morning preparation resolves at the finish.",
            episode_ids=tuple(projection.group.group_id for projection in projections),
            asset_ids=representative_ids,
        ),
    )
    assert first.insight.provenance.sheet_hashes == ()
    assert first.request_trace is not None
    assert first.request_trace.attached_sheet_hashes == ()
    assert first.request_trace.tile_count == 0
    assert first.actual_calls == 1
    assert first.insight.provenance.cache_hit is False
    assert second.insight == first.insight.__class__(
        thesis=first.insight.thesis,
        evidence=first.insight.evidence,
        tensions=first.insight.tensions,
        recurring_threads=first.insight.recurring_threads,
        unavailable_reason=None,
        revision=first.insight.revision,
        provenance=first.insight.provenance.__class__(
            **{**first.insight.provenance.__dict__, "cache_hit": True}
        ),
    )
    assert second.actual_calls == 0
    assert len(prompts) == 1
    assert "columns=episode\tdate_or_span\tplace\tpeople\tassets\thappened" in prompts[0]
    assert "1\t08-25" in prompts[0]
    assert "2\t08-25" in prompts[0]
    assert all(asset_id not in prompts[0] for asset_id in prepared.candidate_ids)
    assert all(projection.group.group_id not in prompts[0] for projection in projections)


def test_all_unread_episodes_skip_the_provider_but_remain_in_provenance(
    tmp_path: Path,
) -> None:
    from immich_memories.analysis.text_period_insight import run_text_period_insight

    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(source_fetcher=lambda _scope: (make_asset("private-unread-asset"),)),
    )
    projection = project_episode_groups(prepared, prepared.candidate_ids)[0]
    episode_result = TextEpisodeReadResult(
        episodes=(
            EpisodeEditorialEvidence(
                projection=projection,
                identity=None,
                reading=None,
                cache_hit=False,
                unavailable_reason="annotation evidence unavailable",
            ),
        ),
        annotation_batch=AnnotationLineBatch(
            requested_asset_ids=prepared.candidate_ids,
            lines=(),
            missing_asset_ids=prepared.candidate_ids,
            contract=AnnotationContract("annotation-line-v1", ("description:test-v1",)),
        ),
        warnings=("!! 1 demanded episode(s) unread; full membership retained",),
        actual_calls=0,
    )
    producer = PeriodInsightProducer(
        model_id="qwen3-vl-30b",
        prompt_version="period-text-v1",
        schema_version="period-insight-text-v1",
    )

    result = run_text_period_insight(
        episode_result,
        store=PeriodInsightStore(tmp_path / "annotations.sqlite"),
        producer=producer,
        requester=lambda _prompt: (_ for _ in ()).throw(
            AssertionError("no readable episode means no period request")
        ),
    )

    assert result.actual_calls == 0
    assert result.request_trace is None
    assert result.insight.thesis is None
    assert result.insight.unavailable_reason == "no readable episode evidence for period insight"
    assert result.insight.provenance.input_ids == prepared.candidate_ids
    assert result.warnings[-1] == (
        "!! 1 demanded episode(s) omitted from period insight because unread"
    )


def test_non_text_period_answer_is_unavailable_and_stays_cold(tmp_path: Path) -> None:
    from immich_memories.analysis.text_period_insight import run_text_period_insight

    episodes = _one_readable_episode_result()
    producer = PeriodInsightProducer(
        model_id="qwen3-vl-30b",
        prompt_version="period-text-v1",
        schema_version="period-insight-text-v1",
    )
    store = PeriodInsightStore(tmp_path / "annotations.sqlite")

    malformed = run_text_period_insight(
        episodes,
        store=store,
        producer=producer,
        requester=lambda _prompt: None,  # type: ignore[arg-type,return-value]
    )
    fresh_calls = 0

    def valid(_prompt: str) -> str:
        nonlocal fresh_calls
        fresh_calls += 1
        return """{
          "schema_version": "period-insight-text-v1",
          "thesis": "One ordinary afternoon becomes a small finish.",
          "evidence": [{"observation": "The finish resolves the walk.", "episodes": [1]}],
          "tensions": [],
          "recurring_threads": []
        }"""

    recovered = run_text_period_insight(
        episodes,
        store=store,
        producer=producer,
        requester=valid,
    )

    assert malformed.insight.thesis is None
    assert malformed.insight.unavailable_reason == ("text period response was missing or invalid")
    assert malformed.actual_calls == 2
    assert recovered.insight.thesis == "One ordinary afternoon becomes a small finish."
    assert fresh_calls == 1


def test_single_page_malformed_json_is_repaired_once_and_only_valid_evidence_is_banked(tmp_path):
    from immich_memories.analysis.text_period_insight import run_text_period_insight

    episodes = _one_readable_episode_result()
    producer = PeriodInsightProducer(
        model_id="test", prompt_version="test", schema_version="period-insight-text-v1"
    )
    store = PeriodInsightStore(tmp_path / "repair.sqlite")
    prompts = []

    def answer(prompt):
        prompts.append(prompt)
        if len(prompts) == 1:
            return '{"tensions":["Quiet", "recurring_threads":["Visits"]}}'
        return json.dumps(
            {
                "schema_version": "period-insight-text-v1",
                "thesis": "A small outing.",
                "evidence": [{"observation": "The outing.", "episodes": [1]}],
                "tensions": [],
                "recurring_threads": [],
            }
        )

    result = run_text_period_insight(episodes, store=store, producer=producer, requester=answer)
    assert result.actual_calls == 2 and result.insight.thesis == "A small outing."
    assert "not one JSON object" in prompts[1]
    assert "aliases between 1 and 1" in prompts[1]
    warm = run_text_period_insight(episodes, store=store, producer=producer, requester=answer)
    assert warm.actual_calls == 0 and len(prompts) == 2


def test_oversized_period_prompt_skips_provider_and_stays_cold(tmp_path: Path) -> None:
    from immich_memories.analysis.text_period_insight import (
        TextPeriodRequestLimits,
        run_text_period_insight,
    )

    episodes = _one_readable_episode_result()
    producer = PeriodInsightProducer(
        model_id="qwen3-vl-30b",
        prompt_version="period-text-v1",
        schema_version="period-insight-text-v1",
    )
    store = PeriodInsightStore(tmp_path / "annotations.sqlite")

    oversized = run_text_period_insight(
        episodes,
        store=store,
        producer=producer,
        requester=lambda _prompt: (_ for _ in ()).throw(
            AssertionError("an oversized period prompt must not reach the provider")
        ),
        limits=TextPeriodRequestLimits(max_prompt_chars=100),
    )
    recovered_calls = 0

    def valid(_prompt: str) -> str:
        nonlocal recovered_calls
        recovered_calls += 1
        return """{
          "schema_version": "period-insight-text-v1",
          "thesis": "One ordinary afternoon becomes a small finish.",
          "evidence": [{"observation": "The finish resolves the walk.", "episodes": [1]}],
          "tensions": [],
          "recurring_threads": []
        }"""

    recovered = run_text_period_insight(
        episodes,
        store=store,
        producer=producer,
        requester=valid,
    )

    assert oversized.actual_calls == 0
    assert oversized.insight.thesis is None
    # a limit no single episode fits under cannot be paged either: still cold, still no call
    assert (
        oversized.insight.unavailable_reason == "one episode alone exceeds the period request limit"
    )
    assert oversized.request_trace is not None
    assert oversized.request_trace.actual_calls == 0
    assert recovered.insight.thesis == "One ordinary afternoon becomes a small finish."
    assert recovered_calls == 1


def test_compact_period_transport_keeps_all_512_meanings_under_the_request_limit() -> None:
    from immich_memories.analysis.text_period_insight import _PeriodEpisodeFacts, _prompt_for

    start = datetime(2022, 1, 1, 12, tzinfo=UTC)
    facts = tuple(
        _PeriodEpisodeFacts(
            first_taken_at=start + timedelta(days=index % 365),
            last_taken_at=start + timedelta(days=index % 365, minutes=5),
            place="Brussels, Brussels, Belgium",
            people=("person one", "person two"),
            asset_count=12,
            what_happened=f"episode {index + 1}: " + "specific factual meaning " * 4,
        )
        for index in range(512)
    )

    prompt = _prompt_for(facts)

    assert len(prompt) <= 96_000
    assert prompt.count(json.dumps("Brussels, Brussels, Belgium")) == 1
    assert prompt.count('["person one","person two"]') == 1
    assert prompt.count("specific factual meaning") == 512 * 4
    assert "1\t01-01\tL1\tP1\t12\t" in prompt
    assert "512\t05-27\tL1\tP1\t12\t" in prompt
    assert "episode 1:" in prompt
    assert "episode 512:" in prompt


def _one_readable_episode_result() -> TextEpisodeReadResult:
    captured = datetime(2026, 8, 25, 12, tzinfo=UTC)
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(
            source_fetcher=lambda _scope: (
                make_asset("private-readable-asset", file_created_at=captured),
            )
        ),
    )
    projection = project_episode_groups(prepared, prepared.candidate_ids)[0]
    annotation_batch = AnnotationLineBatch(
        requested_asset_ids=prepared.candidate_ids,
        lines=(AssetAnnotationLine("private-readable-asset", "An afternoon walk."),),
        missing_asset_ids=(),
        contract=AnnotationContract("annotation-line-v1", ("description:test-v1",)),
    )
    identity = EpisodeReadingIdentity.from_annotations(
        group_id=projection.group.group_id,
        producer_key="episode-reader-v1",
        annotation_lines=annotation_batch.as_mapping(),
    )
    reading = BankedEpisodeReading(
        identity=identity,
        full_asset_ids=projection.group.candidate_ids,
        what_happened="The family takes an afternoon walk.",
        representatives=(EpisodeRepresentative("private-readable-asset", "Shows the walk."),),
        cull_decisions=(),
    )
    return TextEpisodeReadResult(
        episodes=(
            EpisodeEditorialEvidence(
                projection=projection,
                identity=identity,
                reading=reading,
                cache_hit=True,
                unavailable_reason=None,
            ),
        ),
        annotation_batch=annotation_batch,
        warnings=(),
        actual_calls=0,
    )
