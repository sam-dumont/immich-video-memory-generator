"""The text editorial lane reaches one post-card backend through production contracts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from immich_memories.analysis.annotation_lines import (
    AnnotationContract,
    AnnotationLineBatch,
    AssetAnnotationLine,
)
from immich_memories.analysis.editorial_orchestration import (
    TextEditorialPlanner,
    TextEditorialWorkprint,
)
from immich_memories.analysis.editorial_planner import EditorialPlan, EditorialSelection
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
)
from immich_memories.analysis.selection_trace import Trace
from immich_memories.analysis.smart_pipeline import ClipWithSegment
from immich_memories.analysis.text_episode_answers import TEXT_EPISODE_SCHEMA_VERSION
from immich_memories.analysis.text_episode_reader import (
    CachedTextEpisodeReader,
    TextEpisodeReadDiagnostics,
)
from immich_memories.analysis.text_period_insight import run_text_period_insight
from immich_memories.analysis.text_period_wire import TEXT_PERIOD_SCHEMA_VERSION
from immich_memories.store.episode_readings import (
    EpisodeReadingProducer,
    EpisodeReadingStore,
)
from immich_memories.store.period_insights import PeriodInsightProducer, PeriodInsightStore
from tests.conftest import make_asset, make_clip


class _AnnotationLines:
    def __init__(self, lines: dict[str, AssetAnnotationLine]) -> None:
        self._lines = lines
        self.requests: list[tuple[str, ...]] = []

    def lines_for(self, asset_ids: tuple[str, ...]) -> AnnotationLineBatch:
        self.requests.append(asset_ids)
        lines = tuple(self._lines[asset_id] for asset_id in asset_ids if asset_id in self._lines)
        return AnnotationLineBatch(
            requested_asset_ids=asset_ids,
            lines=lines,
            missing_asset_ids=tuple(
                asset_id for asset_id in asset_ids if asset_id not in self._lines
            ),
            contract=AnnotationContract("annotation-line-v1", ("description:test-v1",)),
        )


@dataclass
class _RecordingBackend:
    workprints: list[TextEditorialWorkprint] = field(default_factory=list)
    traces: list[Trace] = field(default_factory=list)

    def edit(
        self,
        workprint: TextEditorialWorkprint,
        *,
        trace: Trace,
    ) -> EditorialPlan:
        self.workprints.append(workprint)
        self.traces.append(trace)
        return EditorialPlan(selections=(EditorialSelection("demanded-favourite"),))


def test_episode_diagnostics_are_observed_before_a_later_stage_failure() -> None:
    source = make_asset("observed")
    demanded = (
        ClipWithSegment(make_clip("observed", file_created_at=source.file_created_at), 0, 1, 1),
    )
    diagnostics = TextEpisodeReadDiagnostics()
    observed: list[TextEpisodeReadDiagnostics] = []

    def fail_after_episode_read(_episodes):
        raise RuntimeError("period failed after episode parsing")

    planner = TextEditorialPlanner(
        selection_request=EditorialSelectionRequest(scope=SourceScope()),
        source_dependencies=EditorialDependencies(source_fetcher=lambda _scope: (source,)),
        episode_reader_factory=lambda _prepared: SimpleNamespace(
            read=lambda _projections: SimpleNamespace(
                diagnostics=diagnostics,
                warnings=(),
                request_trace=None,
            )
        ),
        period_reader=fail_after_episode_read,
        backend=_RecordingBackend(),
        episode_diagnostics_sink=observed.append,
    )

    with pytest.raises(RuntimeError, match="period failed"):
        planner.plan(demanded, trace=Trace())

    assert observed == [diagnostics]


def test_text_editorial_planner_runs_the_real_banked_lane_with_full_context(
    tmp_path: Path,
) -> None:
    noon = datetime(2022, 6, 19, 12, tzinfo=UTC)
    full_source = (
        make_asset("context-frame", file_created_at=noon),
        make_asset(
            "demanded-favourite",
            file_created_at=noon + timedelta(minutes=4),
            is_favorite=True,
        ),
        make_asset("demanded-junk", file_created_at=noon + timedelta(minutes=7)),
        make_asset("undemanded-episode", file_created_at=noon + timedelta(hours=3)),
    )
    candidates = (
        ClipWithSegment(
            make_clip(
                "demanded-favourite",
                file_created_at=noon + timedelta(minutes=4),
                is_favorite=True,
            ),
            1.25,
            4.75,
            0.91,
        ),
        ClipWithSegment(
            make_clip("demanded-junk", file_created_at=noon + timedelta(minutes=7)),
            0.5,
            3.5,
            0.41,
        ),
    )
    annotations = _AnnotationLines(
        {
            "context-frame": AssetAnnotationLine(
                "context-frame",
                "family gathering around a table",
                description="Family gathering around a table.",
                heads=(("activity", "celebration"),),
            ),
            "demanded-favourite": AssetAnnotationLine(
                "demanded-favourite",
                "starred finish-line embrace",
                description="Two relatives embrace at the finish line.",
                heads=(("activity", "sport"), ("people", "group")),
            ),
            "demanded-junk": AssetAnnotationLine(
                "demanded-junk",
                "phone screenshot of a receipt",
                description="A phone screenshot of a receipt.",
                heads=(("activity", "document"),),
            ),
            "undemanded-episode": AssetAnnotationLine(
                "undemanded-episode",
                "quiet evening walk",
                description="A quiet evening walk.",
            ),
        }
    )
    episode_store = EpisodeReadingStore(tmp_path / "annotations.sqlite")
    period_store = PeriodInsightStore(tmp_path / "annotations.sqlite")
    episode_producer = EpisodeReadingProducer(
        model_id="text-judge-test",
        prompt_version="episode-prompt-v1",
        schema_version=TEXT_EPISODE_SCHEMA_VERSION,
        annotation_renderer_version="annotation-line-v1",
        annotation_versions=("description:test-v1",),
    )
    period_producer = PeriodInsightProducer(
        model_id="text-judge-test",
        prompt_version="period-prompt-v1",
        schema_version=TEXT_PERIOD_SCHEMA_VERSION,
    )
    episode_prompts: list[str] = []
    period_prompts: list[str] = []

    def request_episode(prompt: str) -> str:
        episode_prompts.append(prompt)
        assert re.findall(r"^episode (\d+)$", prompt, re.MULTILINE) == ["1"]
        return json.dumps(
            {
                "schema_version": TEXT_EPISODE_SCHEMA_VERSION,
                "episodes": [
                    {
                        "episode": 1,
                        "what_happened": "Family celebrates after a race finish.",
                        "representatives": [{"asset": 1, "reason": "Shows the family occasion."}],
                        # The model is allowed to be wrong about a favourite;
                        # the production law, not this fixture, must save it.
                        "cull": [
                            {"asset": 2, "bucket": "failed"},
                            {"asset": 3, "bucket": "notes"},
                        ],
                    }
                ],
            }
        )

    def request_period(prompt: str) -> str:
        period_prompts.append(prompt)
        return json.dumps(
            {
                "schema_version": TEXT_PERIOD_SCHEMA_VERSION,
                "thesis": "A family race day resolves in a shared celebration.",
                "evidence": [
                    {
                        "observation": "The finish turns effort into a family occasion.",
                        "episodes": [1],
                    }
                ],
                "tensions": ["Effort before relief."],
                "recurring_threads": ["Family shows up."],
            }
        )

    def episode_reader_factory(prepared):
        return CachedTextEpisodeReader(
            store=episode_store,
            producer=episode_producer,
            annotations=annotations,
            requester=request_episode,
        )

    def period_reader(episodes):
        return run_text_period_insight(
            episodes,
            store=period_store,
            producer=period_producer,
            requester=request_period,
        )

    backend = _RecordingBackend()
    planner = TextEditorialPlanner(
        selection_request=EditorialSelectionRequest(scope=SourceScope()),
        source_dependencies=EditorialDependencies(source_fetcher=lambda _scope: full_source),
        episode_reader_factory=episode_reader_factory,
        period_reader=period_reader,
        backend=backend,
    )
    first_trace = Trace()
    second_trace = Trace()

    first = planner.plan(candidates, trace=first_trace)
    second = planner.plan(candidates, trace=second_trace)

    assert first == second == EditorialPlan(selections=(EditorialSelection("demanded-favourite"),))
    assert len(episode_prompts) == 1
    assert len(period_prompts) == 1
    assert all(asset.id not in episode_prompts[0] for asset in full_source)
    assert all(asset.id not in period_prompts[0] for asset in full_source)
    assert annotations.requests == [
        ("context-frame", "demanded-favourite", "demanded-junk"),
        ("context-frame", "demanded-favourite", "demanded-junk"),
    ]

    cold, warm = backend.workprints
    assert cold.episodes.actual_calls == 1
    assert warm.episodes.actual_calls == 0
    assert cold.episodes.request_trace is not None
    assert warm.episodes.request_trace is not None
    assert cold.episodes.request_trace.actual_calls == 1
    assert warm.episodes.request_trace.actual_calls == 0
    assert warm.episodes.request_trace.cache_hit is True
    assert cold.period.actual_calls == 1
    assert warm.period.actual_calls == 0
    assert cold.cull.rejected[0].asset_id == "demanded-junk"
    assert cold.cull.survivors[0].asset_id == "context-frame"
    assert [candidate.asset_id for candidate in cold.scoped_survivors] == ["demanded-favourite"]
    assert cold.scoped_survivors[0].proposed_segment == (1.25, 4.75)
    assert cold.structure.moments[0].representative.proposed_segment == (1.25, 4.75)
    assert len(cold.cards) == 1
    assert cold.cards[0].full_asset_ids == (
        "context-frame",
        "demanded-favourite",
        "demanded-junk",
    )
    assert cold.cards[0].selectable_asset_ids == ("demanded-favourite",)
    assert len(cold.cards[0].text) <= 700
    assert cold.cards[0].text == warm.cards[0].text
    assert backend.traces == [first_trace, second_trace]
    assert [item.name for item in first_trace.editorial_passes] == [
        "source-eligibility",
        "pass-1-cull",
    ]
    assert first_trace.editorial_passes[-1].provenance.cache_hit is False
    assert second_trace.editorial_passes[-1].provenance.cache_hit is True
    assert first_trace.editorial_passes[-1].provenance.input_ids == (
        "context-frame",
        "demanded-favourite",
        "demanded-junk",
    )
    assert first_trace.editorial_passes[-1].request_traces == (cold.episodes.request_trace,)
    assert [request.provenance.pass_name for request in first_trace.requests] == [
        "episode-reading-text",
        "period-insight-text",
    ]
    assert (
        first_trace.requests[-1].provenance.pass_name == "period-insight-text"  # noqa: S105 - public editorial pass identity.
    )
    assert second_trace.requests[-1].cache_hit is True
