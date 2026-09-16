"""Large period accounts page all episode evidence and retain the warm replay."""

import json
import re
from datetime import UTC, datetime, timedelta

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
    EpisodeEditorialEvidence,
    TextEpisodeReadResult,
)
from immich_memories.analysis.text_period_insight import run_text_period_insight
from immich_memories.store.episode_readings import (
    BankedEpisodeReading,
    EpisodeReadingIdentity,
    EpisodeRepresentative,
)
from immich_memories.store.period_insights import PeriodInsightProducer, PeriodInsightStore
from tests.conftest import make_asset


def year_episodes(count=200):
    assets = tuple(
        make_asset(
            f"year-source-{i}",
            file_created_at=datetime(2030, 1, 1, 12, tzinfo=UTC) + timedelta(days=i),
        )
        for i in range(count)
    )
    prepared = prepare_editorial_source(
        EditorialSelectionRequest(scope=SourceScope()),
        EditorialDependencies(source_fetcher=lambda _: assets),
    )
    episodes, lines = [], []
    for i, projection in enumerate(project_episode_groups(prepared, prepared.candidate_ids)):
        asset_id = projection.group.candidate_ids[0]
        description = f"Visit-{i:03d}. " + "The family shares an afternoon at the park. " * 8
        lines.append(AssetAnnotationLine(asset_id, description))
        identity = EpisodeReadingIdentity.from_annotations(
            group_id=projection.group.group_id,
            producer_key="year-reader",
            annotation_lines={asset_id: description},
        )
        episodes.append(
            EpisodeEditorialEvidence(
                projection=projection,
                identity=identity,
                reading=BankedEpisodeReading(
                    identity,
                    (asset_id,),
                    description,
                    (EpisodeRepresentative(asset_id, "Visit"),),
                    (),
                ),
                cache_hit=True,
                unavailable_reason=None,
            )
        )
    return TextEpisodeReadResult(
        tuple(episodes),
        AnnotationLineBatch(
            prepared.candidate_ids,
            tuple(lines),
            (),
            AnnotationContract("annotation-line-v1", ("year-reader",)),
        ),
        (),
        0,
    )


def test_large_period_pages_every_episode_then_reuses_the_complete_account(tmp_path):
    episodes = year_episodes()
    prompts = []

    # WHY: replace the external reader; the production paging, grounding and SQLite bank run.
    def requester(prompt):
        prompts.append(prompt)
        return json.dumps(
            {
                "schema_version": "period-insight-text-v1",
                "thesis": "Family visits through the year.",
                "evidence": [{"observation": "The visits recur.", "episodes": [1]}],
                "tensions": [],
                "recurring_threads": [],
            }
        )

    args = {
        "store": PeriodInsightStore(tmp_path / "annotations.sqlite"),
        "producer": PeriodInsightProducer("reader", "test", "period-insight-text-v1"),
        "requester": requester,
    }
    result = run_text_period_insight(episodes, **args)
    warm = run_text_period_insight(episodes, **args)

    assert result.insight.unavailable_reason is None
    assert result.pages > 1
    assert max(map(len, prompts)) <= 48_000
    assert len(result.episode_grounding) == 200
    assert sorted(re.findall(r"Visit-\d{3}", "\n".join(prompts))) == [
        f"Visit-{i:03d}" for i in range(200)
    ]
    assert warm.actual_calls == 0
    assert warm.episode_grounding == result.episode_grounding
