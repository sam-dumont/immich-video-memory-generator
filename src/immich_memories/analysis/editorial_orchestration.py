"""Compose the production text-reading lane up to one post-card editor."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import TYPE_CHECKING, Protocol

from immich_memories.analysis.editorial_contracts import (
    DecisionProvenance,
    EditorialCandidate,
)
from immich_memories.analysis.editorial_planner import EditorialPlan
from immich_memories.analysis.editorial_rule_episodes import EpisodeReader
from immich_memories.analysis.moment_cards import MomentCard, build_moment_cards
from immich_memories.analysis.selection_cull import (
    CullDecisionResult,
    cull_pass_version,
    run_cull_decisions,
)
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    PreparedEditorialSource,
    prepare_editorial_source,
)
from immich_memories.analysis.selection_source_groups import (
    EditorialGroupProjection,
    project_episode_groups,
    project_moment_groups,
)
from immich_memories.analysis.selection_structure import (
    StructureWorkprint,
    build_structure_workprint,
)
from immich_memories.analysis.selection_trace import Trace
from immich_memories.analysis.text_episode_reader import (
    TextEpisodeReadDiagnostics,
    TextEpisodeReadResult,
)
from immich_memories.analysis.text_period_insight import TextPeriodInsightResult
from immich_memories.operations.cut_progress import StageUpdate

if TYPE_CHECKING:
    from immich_memories.analysis.smart_pipeline import ClipWithSegment
    from immich_memories.cache.editorial_verdicts import EditorialVerdicts

__all__ = [
    "PostCardEditorialBackend",
    "TextEditorialPlanner",
    "TextEditorialWorkprint",
]


class PostCardEditorialBackend(Protocol):
    """Turn complete production cards into the reviewed final cut."""

    def edit(
        self,
        workprint: TextEditorialWorkprint,
        *,
        trace: Trace,
    ) -> EditorialPlan: ...


@dataclass(frozen=True)
class TextEditorialWorkprint:
    """Conserved evidence handed across the one semantic editor boundary."""

    input_candidates: tuple[ClipWithSegment, ...]
    prepared: PreparedEditorialSource
    episodes: TextEpisodeReadResult
    period: TextPeriodInsightResult
    cull: CullDecisionResult
    scoped_survivors: tuple[EditorialCandidate, ...]
    structure: StructureWorkprint
    cards: tuple[MomentCard, ...]


class TextEditorialPlanner:
    """Read demanded canonical context, then delegate exactly once after cards."""

    def __init__(
        self,
        *,
        selection_request: EditorialSelectionRequest,
        source_dependencies: EditorialDependencies,
        episode_reader_factory: Callable[[PreparedEditorialSource], EpisodeReader],
        period_reader: Callable[[TextEpisodeReadResult], TextPeriodInsightResult],
        backend: PostCardEditorialBackend,
        verdicts: EditorialVerdicts | None = None,
        episode_diagnostics_sink: Callable[[TextEpisodeReadDiagnostics], None] | None = None,
    ) -> None:
        self._selection_request = selection_request
        self._source_dependencies = source_dependencies
        self._episode_reader_factory = episode_reader_factory
        self._period_reader = period_reader
        self._backend = backend
        self._verdicts = verdicts
        self._episode_diagnostics_sink = episode_diagnostics_sink

    def plan(
        self,
        candidates: tuple[ClipWithSegment, ...],
        *,
        trace: Trace,
    ) -> EditorialPlan:
        """Return a final scoped plan, or a visible fail-open availability state."""
        if not _demanded_ids(candidates):
            return EditorialPlan()

        prepared = self.prepare_source(trace=trace)
        return self.plan_prepared(candidates, prepared=prepared, trace=trace)

    def prepare_source(
        self,
        *,
        trace: Trace,
        evidence_exclusions: Mapping[str, str] | None = None,
        include_previews: bool = True,
    ) -> PreparedEditorialSource:
        """Capture canonical context before choosing which eligible sources are demanded."""
        return prepare_editorial_source(
            self._selection_request
            if evidence_exclusions is None
            else replace(self._selection_request, evidence_exclusions=evidence_exclusions),
            self._source_dependencies
            if include_previews
            else replace(self._source_dependencies, preview_jpeg=None),
            trace=trace,
        )

    def plan_prepared(
        self,
        candidates: tuple[ClipWithSegment, ...],
        *,
        prepared: PreparedEditorialSource,
        trace: Trace,
        on_stage: Callable[[StageUpdate], None] | None = None,
    ) -> EditorialPlan:
        """Read the same canonical groups from an already prepared source."""
        demanded_ids = _demanded_ids(candidates)
        if not demanded_ids:
            return EditorialPlan()
        missing_demand = set(demanded_ids).difference(prepared.candidate_ids)
        if missing_demand:
            return _unavailable(
                trace,
                f"canonical source omitted {len(missing_demand)} demanded asset(s)",
            )
        episode_projections = project_episode_groups(prepared, demanded_ids)
        episode_reader = self._episode_reader_factory(prepared)
        episodes = self._read_episodes(
            episode_projections, reader=episode_reader, trace=trace, on_stage=on_stage
        )
        period = self._read_period(episodes, trace=trace, on_stage=on_stage)
        if period.insight.unavailable_reason is not None:
            return _unavailable(trace, period.insight.unavailable_reason)

        _stage(on_stage, "Building editorial cards")
        workprint = self._build_workprint(
            candidates,
            prepared=prepared,
            episodes=episodes,
            period=period,
            reader=episode_reader,
            demanded_ids=demanded_ids,
        )
        if workprint is None:
            return EditorialPlan()
        _stage(on_stage, "Editing the memory")
        return self._scoped_plan(workprint, trace=trace)

    def _read_episodes(
        self,
        episode_projections: tuple[EditorialGroupProjection, ...],
        *,
        reader: EpisodeReader,
        trace: Trace,
        on_stage: Callable[[StageUpdate], None] | None,
    ) -> TextEpisodeReadResult:
        _stage(on_stage, "Reading event evidence")
        episodes = reader.read(episode_projections)
        if self._episode_diagnostics_sink is not None:
            self._episode_diagnostics_sink(episodes.diagnostics)
        _record_warnings(trace, episodes.warnings)
        if episodes.request_trace is not None:
            trace.record_request(episodes.request_trace)
        return episodes

    def _read_period(
        self,
        episodes: TextEpisodeReadResult,
        *,
        trace: Trace,
        on_stage: Callable[[StageUpdate], None] | None,
    ) -> TextPeriodInsightResult:
        _stage(on_stage, "Reading the period account")
        period = self._period_reader(episodes)
        _record_warnings(trace, period.warnings)
        if period.request_trace is not None:
            trace.record_request(period.request_trace)
        return period

    def _build_workprint(
        self,
        candidates: tuple[ClipWithSegment, ...],
        *,
        prepared: PreparedEditorialSource,
        episodes: TextEpisodeReadResult,
        period: TextPeriodInsightResult,
        reader: EpisodeReader,
        demanded_ids: tuple[str, ...],
    ) -> TextEditorialWorkprint | None:
        """Return None when the cull left nothing inside the demanded scope."""
        cull = run_cull_decisions(
            prepared,
            episodes.cull_decisions,
            provenance=_cull_provenance(episodes, reader),
            warnings=episodes.warnings,
            request_traces=() if episodes.request_trace is None else (episodes.request_trace,),
            verdicts=self._verdicts,
            actual_calls=episodes.actual_calls,
        )
        demanded = frozenset(demanded_ids)
        scoped_survivors = tuple(
            candidate for candidate in cull.survivors if candidate.asset_id in demanded
        )
        if not scoped_survivors:
            return None

        structure = build_structure_workprint(
            prepared,
            scoped_survivors,
            representative_resolver=episodes.representative_for,
        )
        moment_projections = project_moment_groups(
            prepared,
            tuple(candidate.asset_id for candidate in scoped_survivors),
        )
        return TextEditorialWorkprint(
            input_candidates=candidates,
            prepared=prepared,
            episodes=episodes,
            period=period,
            cull=cull,
            scoped_survivors=scoped_survivors,
            structure=structure,
            cards=build_moment_cards(moment_projections, episodes=episodes),
        )

    def _scoped_plan(self, workprint: TextEditorialWorkprint, *, trace: Trace) -> EditorialPlan:
        plan = self._backend.edit(workprint, trace=trace)
        if not isinstance(plan, EditorialPlan):
            raise TypeError("post-card editor must return an EditorialPlan")
        if plan.unavailable_reason is not None:
            return plan
        selectable_ids = {
            asset_id for card in workprint.cards for asset_id in card.selectable_asset_ids
        }
        escaped = set(plan.selected_asset_ids).difference(selectable_ids)
        if escaped:
            raise ValueError("post-card editor selected outside the scoped survivors")
        return plan


def _demanded_ids(candidates: tuple[ClipWithSegment, ...]) -> tuple[str, ...]:
    demanded_ids = tuple(candidate.clip.asset.id for candidate in candidates)
    if len(demanded_ids) != len(set(demanded_ids)):
        raise ValueError("text editorial planner needs unique input asset IDs")
    return demanded_ids


def _stage(on_stage: Callable[[StageUpdate], None] | None, label: str) -> None:
    if on_stage is not None:
        on_stage(StageUpdate(label))


def _cull_provenance(
    episodes: TextEpisodeReadResult,
    reader: EpisodeReader,
) -> DecisionProvenance:
    identities = tuple(
        (
            episode.identity.group_id,
            episode.identity.producer_key,
            episode.identity.evidence_key,
        )
        for episode in episodes.episodes
        if episode.identity is not None
    )
    request_key = sha256(
        json.dumps(identities, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return DecisionProvenance(
        pass_name="pass-1-cull",  # noqa: S106 - public editorial pass identity.
        pass_version=cull_pass_version(reader.producer),
        schema_version=reader.producer.schema_version,
        model_identity=reader.producer.model_id,
        input_ids=episodes.annotation_batch.requested_asset_ids,
        sheet_hashes=(),
        request_key=request_key,
        cache_hit=bool(episodes.episodes)
        and all(episode.cache_hit for episode in episodes.episodes),
    )


def _record_warnings(trace: Trace, warnings: tuple[str, ...]) -> None:
    for warning in warnings:
        if warning not in trace.warnings:
            trace.warnings.append(warning)


def _unavailable(trace: Trace, reason: str) -> EditorialPlan:
    warning = f"!! text editorial planner unavailable: {reason}"
    if warning not in trace.warnings:
        trace.warnings.append(warning)
    return EditorialPlan(unavailable_reason=reason)
