"""Shared production composition for the store-backed editorial planner."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeVar

from immich_memories.analysis.editorial_album_index import (
    RunAlbumNames,
    record_album_index,
)
from immich_memories.analysis.editorial_attached_outcomes import AttachedOutcomeReplay
from immich_memories.analysis.editorial_evidence_provenance import AttemptEvidenceProvenance
from immich_memories.analysis.editorial_film_reach import film_reach
from immich_memories.analysis.editorial_motion_outcomes import MotionOutcomeReplay
from immich_memories.analysis.editorial_orchestration import TextEditorialPlanner
from immich_memories.analysis.editorial_people import adapt_editorial_people
from immich_memories.analysis.editorial_planner import EditorialPlan
from immich_memories.analysis.editorial_rule_episodes import (
    EpisodeReader,
    RuleEpisodeReader,
)
from immich_memories.analysis.editorial_runtime_backend import ProductionPostCardBackend
from immich_memories.analysis.editorial_runtime_evidence import (
    AnnotationReadings,
    EvidencePreparation,
    ensure_annotation_store,
)
from immich_memories.analysis.editorial_runtime_ports import EditorialRuntimePorts
from immich_memories.analysis.editorial_source import FullEditorialSource, library_source_scope
from immich_memories.analysis.editorial_source_route import (
    EditorialSourcePlan,
    metadata_demand,
    project_source_rendering,
)
from immich_memories.analysis.editorial_source_snapshot import AttemptSourceSnapshots
from immich_memories.analysis.editorial_text_artifacts import TextPromptArtifacts
from immich_memories.analysis.editorial_text_gateway import (
    SyncTextPromptRequester,
    semantic_text_model_identity,
)
from immich_memories.analysis.editorial_thin_layer import catalogued_period
from immich_memories.analysis.episode_demand import demand_reader_factory
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    SourceScope,
)
from immich_memories.analysis.selection_trace import Trace
from immich_memories.analysis.special_event_scope import (
    SpecialEventAdmission,
    select_source_members,
    validate_special_event_scope,
)
from immich_memories.analysis.text_episode_answers import TEXT_EPISODE_SCHEMA_VERSION
from immich_memories.analysis.text_episode_prompt import (
    TEXT_EPISODE_LEAN_PROMPT_VERSION,
    TEXT_EPISODE_PROMPT_VERSION,
)
from immich_memories.analysis.text_episode_reader import CachedTextEpisodeReader
from immich_memories.analysis.thumbnail_prefetch import cached_preview_bytes
from immich_memories.api.models import Asset, VideoClipInfo
from immich_memories.api.person_expression import PersonExpression
from immich_memories.cache.editorial_verdicts import EditorialVerdicts
from immich_memories.operations.cut_progress import ANALYSIS_PHASE, StageUpdate, announcing_stages
from immich_memories.planning.auto_duration import DURATION_FROM_DURATION_FLAG
from immich_memories.processing.editorial_timing import EditorialTimingPolicy
from immich_memories.security import write_secret_file
from immich_memories.store.episode_readings import EpisodeReadingProducer, EpisodeReadingStore
from immich_memories.timeperiod import DateRange

if TYPE_CHECKING:
    from immich_memories.analysis.smart_pipeline import (
        ClipWithSegment,
        PipelineConfig,
        SmartPipeline,
    )
    from immich_memories.api.sync_client import SyncImmichClient
    from immich_memories.cache.thumbnail_cache import ThumbnailCache
    from immich_memories.config_loader import Config

logger = logging.getLogger(__name__)

_Row = TypeVar("_Row")


@dataclass(frozen=True, slots=True)
class EditorialRunContext:
    """Exact source and product facts shared by the CLI and UI entry points."""

    key: str
    label: str
    product: str
    date_ranges: tuple[DateRange, ...]
    target_seconds: float
    artifact_dir: Path
    people: tuple[str, ...] = ()
    person_match: Literal["and", "or"] = "and"
    accept_any_provenance: bool = False
    trip: bool = False
    album_ref: str | None = None
    special_event_id: str | None = None
    event_asset_ids: tuple[str, ...] = ()
    event_admission: SpecialEventAdmission | None = None
    album_sources: tuple[Asset | VideoClipInfo, ...] = ()
    owner_excluded_asset_ids: tuple[str, ...] = ()
    owner_required_asset_ids: tuple[str, ...] = ()
    target_source: str = "runtime"
    # What set target_seconds: --duration, --short-form, the material or the
    # preset floor. Carried so a run record can say why a film is this long.
    duration_source: str = DURATION_FROM_DURATION_FLAG
    base_brief: str | None = None
    motion_outcome_replay: MotionOutcomeReplay | None = None
    person_expression: PersonExpression | None = None
    attached_outcome_replay: AttachedOutcomeReplay | None = None
    render_timing: EditorialTimingPolicy | None = None
    hemisphere: Literal["north", "south"] = "north"
    window_origin: str | None = None  # why a window nobody typed starts where it does

    def __post_init__(self) -> None:
        """Canonicalize exact windows while preserving every intentional gap."""
        self._check_render_policy()
        self._adopt_person_expression()
        self._check_identity()
        self._adopt_special_event_members()
        ordered = tuple(sorted(self.date_ranges, key=lambda window: (window.start, window.end)))
        if any(window.start > window.end for window in ordered):
            raise ValueError("editorial date windows must start before they end")
        # Birthday history can overlap its rolling year. Keep both semantic
        # windows; acquisition coalesces repeated asset IDs before selection.
        object.__setattr__(self, "date_ranges", ordered)
        self._check_acquisition_shape(ordered)

    def _check_render_policy(self) -> None:
        if self.hemisphere not in ("north", "south"):
            raise ValueError("editorial hemisphere must be north or south")
        if self.render_timing is not None and (
            not isinstance(self.render_timing, EditorialTimingPolicy)
            or self.render_timing.target_seconds != self.target_seconds
            or self.render_timing.memory_type != self.product
        ):
            raise ValueError("Runtime rendering policy disagrees with the requested memory")

    def _adopt_person_expression(self) -> None:
        if self.person_expression is None:
            return
        if not isinstance(self.person_expression, PersonExpression):
            raise ValueError("runtime people condition must be a validated expression")
        if self.people and set(self.people) != set(self.person_expression.leaf_values):
            raise ValueError("runtime people names and grouped condition disagree")
        object.__setattr__(self, "people", self.person_expression.leaf_values)

    def _check_identity(self) -> None:
        if any(not value.strip() for value in (self.key, self.label, self.product)):
            raise ValueError("editorial run identity cannot be blank")
        if self.target_seconds <= 0:
            raise ValueError("editorial target duration must be positive")

    def _adopt_special_event_members(self) -> None:
        members = validate_special_event_scope(
            self.special_event_id, self.event_asset_ids, product=self.product
        )
        object.__setattr__(self, "event_asset_ids", members)
        if self.event_admission is None:
            return
        if not isinstance(self.event_admission, SpecialEventAdmission):
            raise ValueError("special event admission must be an explicit validated record")
        self.event_admission.validate_scope(self.special_event_id, members, product=self.product)

    def _check_acquisition_shape(self, ordered: tuple[DateRange, ...]) -> None:
        if self.product == "album":
            if ordered or not self.album_sources:
                raise ValueError("album editorial runs need captured sources and no fetch windows")
            if self.album_ref is None or not self.album_ref.strip():
                raise ValueError("album editorial runs need an album reference")
        elif not ordered or self.album_sources or self.album_ref is not None:
            raise ValueError("non-album editorial runs need exact fetch windows only")

    @property
    def case_ranges(self) -> tuple[DateRange, ...]:
        """Return semantic ranges without turning an album span into acquisition scope."""
        if self.date_ranges:
            return self.date_ranges
        taken_at = tuple(_asset(source).file_created_at for source in self.album_sources)
        return (DateRange(min(taken_at), max(taken_at)),)


def _asset(source: Asset | VideoClipInfo) -> Asset:
    return source.asset if isinstance(source, VideoClipInfo) else source


def _projected_rendering(
    result: Any,
    candidates: Any,
    plan: EditorialPlan,
    *,
    config: Config,
    backend: ProductionPostCardBackend,
    include_live_photos: bool,
) -> EditorialSourcePlan:
    """Re-time the chosen carriers and record the exact intervals the renderer will use."""
    projected = project_source_rendering(
        result.plan["carriers"],
        candidates,
        config=config,
        include_live_photos=include_live_photos,
        companion_assets=backend.last_companion_assets,
        clock_offsets=backend.clock_offsets_provider,
    )
    if projected.plan.selected_asset_ids != plan.selected_asset_ids:
        raise ValueError("editorial source projection changed final membership")
    projected = replace(
        projected,
        render_timing=result.plan.get("render_timing"),
        duration_realization=result.plan.get("duration_realization"),
    )
    write_secret_file(
        backend._context.artifact_dir / "render-projection.private.json",
        json.dumps(
            {
                "format": "editorial-source-rendering-v1",
                "selected_ids": list(projected.plan.selected_asset_ids),
                "adjustments": list(projected.render_adjustments),
                "allow_live_motion": include_live_photos,
                "intervals": {
                    row.asset_id: [row.start_time, row.end_time]
                    for row in projected.plan.selections
                },
            },
            indent=2,
        ),
    )
    return projected


class RuntimeEditorialPlanner:
    """Replayable planner whose thread-owned stores close after every planning attempt."""

    def __init__(
        self,
        planner: TextEditorialPlanner,
        *,
        episode_store: EpisodeReadingStore,
        asset_ids: tuple[str, ...] | None = None,
        config: Config | None = None,
        backend: ProductionPostCardBackend | None = None,
        person_expression: PersonExpression | None = None,
    ) -> None:
        self._planner = planner
        self._config = config
        self._backend = backend
        self._episode_store = episode_store
        self._asset_ids = frozenset(asset_ids) if asset_ids is not None else None
        self._person_expression = person_expression
        self.last_attempt_directory: Path | None = None
        self._prepare_annotations: Callable[..., Any] | None = None

    def plan(
        self,
        candidates: tuple[ClipWithSegment, ...],
        *,
        trace: Trace,
    ) -> EditorialPlan:
        try:
            return self._planner.plan(
                self._narrowed(candidates, lambda row: row.clip.asset), trace=trace
            )
        finally:
            self.close()

    def _narrowed(
        self, rows: Sequence[_Row], asset_of: Callable[[_Row], Asset]
    ) -> tuple[_Row, ...]:
        """Apply the run's people condition and explicit membership to any source shape."""
        if self._person_expression is not None:
            from immich_memories.analysis.editorial_source import filter_named_expression

            allowed = {
                asset.id
                for asset in filter_named_expression(
                    [asset_of(row) for row in rows], self._person_expression
                )
            }
            rows = [row for row in rows if asset_of(row).id in allowed]
        if self._asset_ids is not None:
            rows = [row for row in rows if asset_of(row).id in self._asset_ids]
        return tuple(rows)

    def plan_source(
        self,
        sources: Sequence[Asset | VideoClipInfo],
        *,
        trace: Trace,
        include_live_photos: bool = True,
        hdr_only: bool = False,
        on_stage: Callable[[StageUpdate], None] | None = None,
    ) -> EditorialSourcePlan:
        """Keep a durable attempt and isolate artifacts before doing any source work."""
        from immich_memories.operations.editorial_attempt import EditorialAttempt

        if self._backend is None:
            raise RuntimeError("editorial source route is not configured")
        context = self._backend._context
        request = {
            "key": context.key,
            "product": context.product,
            "target_seconds": context.target_seconds,
            "duration_source": context.duration_source,
            "audience": "family",
            "hemisphere": context.hemisphere,
            "date_ranges": [[r.start.isoformat(), r.end.isoformat()] for r in context.case_ranges],
            "requested_assets": [_asset(source).id for source in sources],
            "include_live_photos": include_live_photos,
            "hdr_only": hdr_only,
        } | ({"window_origin": context.window_origin} if context.window_origin else {})
        with EditorialAttempt(context.artifact_dir, request=request) as attempt:
            self.last_attempt_directory = attempt.directory
            self._backend._context = replace(context, artifact_dir=attempt.directory)

            def stage(update: StageUpdate) -> None:
                update = attempt.stage(update)
                if on_stage is not None:
                    on_stage(update)

            try:
                result = self._plan_source(
                    sources,
                    trace=trace,
                    include_live_photos=include_live_photos,
                    hdr_only=hdr_only,
                    on_stage=stage,
                )
                structure = self._backend.last_structure_result
                attempt.complete(
                    selected=len(result.plan.selections),
                    outcome="selected" if result.plan.selections else "no_selection",
                    duration_realization=result.duration_realization,
                    calls_by_stage=structure.plan.get("calls_by_stage") if structure else None,
                )
                return result
            finally:
                self._backend._context = context

    def _plan_source(
        self,
        sources: Sequence[Asset | VideoClipInfo],
        *,
        trace: Trace,
        include_live_photos: bool = True,
        hdr_only: bool = False,
        on_stage: Callable[[StageUpdate], None] | None = None,
    ) -> EditorialSourcePlan:
        """Read canonical evidence directly; never fall back to subjective pool analysis."""
        if self._config is None or self._backend is None:
            raise RuntimeError("editorial source route is not configured")
        config, backend = self._config, self._backend
        previous_live_motion = backend.allow_live_motion
        backend.allow_live_motion = include_live_photos
        try:
            sources = self._narrowed(sources, _asset)
            if on_stage is not None:
                on_stage(StageUpdate("Reading dates, places and people", ANALYSIS_PHASE))
            # The readers and the structure planner announce through the
            # context, since the callback never reaches that deep.
            with announcing_stages(on_stage):
                prepared, reach = self._prepared_source(
                    trace=trace, on_stage=on_stage, demanded=[_asset(s).id for s in sources]
                )
                candidates = metadata_demand(
                    prepared,
                    sources,
                    photo_seconds=config.photos.duration,
                    hdr_only=hdr_only,
                )
                backend.last_structure_result = None
                plan = self._planner.plan_prepared(
                    candidates,
                    prepared=prepared,
                    trace=trace,
                    on_stage=on_stage,
                )
            if plan.unavailable_reason is not None:
                raise RuntimeError(
                    f"editorial source evidence unavailable: {plan.unavailable_reason}"
                )
            if reach is not None and (unread := set(plan.selected_asset_ids) - reach):
                raise RuntimeError(f"the cut selected {len(unread)} picture(s) it never prepared")
            result = backend.last_structure_result
            if not plan.selections:
                return EditorialSourcePlan(
                    candidates,
                    plan,
                    duration_realization=result.plan.get("duration_realization")
                    if result
                    else None,
                )
            if result is None:
                raise RuntimeError("editorial source route has no completed structure result")
            if on_stage is not None:
                on_stage(StageUpdate("Validating selected source timing"))
            return _projected_rendering(
                result,
                candidates,
                plan,
                config=config,
                backend=backend,
                include_live_photos=include_live_photos,
            )
        finally:
            backend.allow_live_motion = previous_live_motion
            self.close()

    def _prepared_source(
        self,
        *,
        trace: Trace,
        on_stage: Callable[[StageUpdate], None] | None,
        demanded: Sequence[str],
    ) -> tuple[Any, frozenset[str] | None]:
        if self._prepare_annotations is None:
            return self._planner.prepare_source(trace=trace), None
        # Preparation covers what the film can reach; the rest of the window is read
        # as metadata for the grouping (#1181). The preliminary pass only admits, so
        # it cuts no groups, and only the final pass belongs to the plan trace.
        preliminary = self._planner.prepare_source(
            trace=Trace(), include_previews=False, group=False
        )
        reach = film_reach(preliminary.candidates, demanded)
        logger.info(
            "preparing %d of %d pictures in the window", len(reach), len(preliminary.candidates)
        )
        exclusions = self._prepare_annotations(preliminary, on_stage, reach)
        # No preview bytes: nothing reads them, and over a long window they were gigabytes.
        final = self._planner.prepare_source(
            trace=trace, evidence_exclusions=exclusions, include_previews=False
        )
        return final, reach

    def close(self) -> None:
        """Release every thread-owned SQLite connection; later reads reopen safely."""
        self._episode_store.close()


def _recorded(requester, stage: str, directory: Callable[[], Path]):
    """Keep the private prompt transcript of a reading stage, so `runs why` can show it."""
    if not isinstance(requester, SyncTextPromptRequester):
        return requester
    return replace(requester, artifacts=TextPromptArtifacts(directory, stage=stage))


def _reading_requesters(config, ports, reader_mode):
    if reader_mode == "rules":
        return "rules-v1", None
    return (
        semantic_text_model_identity(config.llm, thinking=False),
        ports.episode_requester_factory(config),
    )


def build_editorial_planner(
    *,
    client: FullEditorialSource,
    config: Config,
    thumbnail_cache: ThumbnailCache,
    context: EditorialRunContext,
    dry_run: bool = False,
    ports: EditorialRuntimePorts | None = None,
) -> RuntimeEditorialPlanner:
    """Build the only production selector from the library's prepared evidence."""
    if dry_run:
        raise ValueError("Dry-run prepares a request without constructing a selector")
    reader_mode = config.editorial.resolve_reader(config.llm.model)
    # A plain date range is cut from the same dates, places, favourites and people as a month.
    # Only a written subject asks what the pictures are about, which no metadata can answer.
    if reader_mode == "rules" and context.base_brief:
        raise ValueError(
            "a written subject needs a model reader: the rules reader cuts from dates, places, "
            "favourites and people only. Drop the subject to cut the date range as it is, "
            "or configure a model reader (llm.model) to cut it about the subject."
        )
    store_path = config.editorial.resolve_annotation_database(config.cache.cache_path)
    ensure_annotation_store(store_path)
    runtime_ports = ports or EditorialRuntimePorts()
    context_by_id = runtime_ports.load_people()
    people = adapt_editorial_people(context_by_id)
    episode_store = runtime_ports.episode_store_factory(store_path)
    model_id, episode_requester = _reading_requesters(config, runtime_ports, reader_mode)
    scope = library_source_scope(
        client,
        config,
        context.date_ranges,
        asset_ids=context.event_asset_ids if context.special_event_id else None,
        accept_any_provenance=context.accept_any_provenance,
    )
    selection_request = EditorialSelectionRequest(
        scope=scope,
        owner_excluded_asset_ids=context.owner_excluded_asset_ids,
        owner_required_asset_ids=context.owner_required_asset_ids,
    )

    source_snapshot: tuple[Asset | VideoClipInfo, ...] | None = (
        context.album_sources if context.product == "album" else None
    )
    source_snapshots = AttemptSourceSnapshots()
    evidence_provenance = AttemptEvidenceProvenance()

    def source_fetcher(requested_scope: SourceScope) -> Sequence[Asset | VideoClipInfo]:
        nonlocal source_snapshot
        if source_snapshot is None:
            source_snapshot = select_source_members(
                runtime_ports.fetch_full_source(client, requested_scope), requested_scope.asset_ids
            )
        source_snapshots.capture(
            source_snapshot,
            directory=backend._context.artifact_dir,
            scope=requested_scope,
            person_expression=context.person_expression,
            people=context.people,
            person_match=context.person_match,
            owner_excluded_asset_ids=context.owner_excluded_asset_ids,
        )
        return source_snapshot

    readings = AnnotationReadings(
        store_path=store_path, config=config, people=context_by_id, subjects=context.people
    )

    album_names = RunAlbumNames(
        client,
        record=lambda index: record_album_index(index, backend._context.artifact_dir),
    )

    def rule_episode_reader(prepared: Any) -> EpisodeReader:
        return RuleEpisodeReader(readings.reader(prepared), by_quality=True)

    def text_episode_reader(prepared: Any, *, lean: bool = False) -> EpisodeReader:
        annotations = readings.reader(prepared)
        assert episode_requester is not None
        contract = annotations.contract

        def producer(prompt_version: str) -> EpisodeReadingProducer:
            return EpisodeReadingProducer(
                model_id=model_id,
                prompt_version=prompt_version,
                schema_version=TEXT_EPISODE_SCHEMA_VERSION,
                annotation_renderer_version=contract.renderer_version,
                annotation_versions=contract.producer_versions,
            )

        full = producer(TEXT_EPISODE_PROMPT_VERSION)
        return CachedTextEpisodeReader(
            store=episode_store,
            producer=producer(TEXT_EPISODE_LEAN_PROMPT_VERSION) if lean else full,
            annotations=annotations,
            requester=episode_requester,
            record_evidence=lambda episodes: evidence_provenance.capture(
                episodes, directory=backend._context.artifact_dir
            ),
            albums=album_names,
            lean=lean,
            served_by=full if lean else None,
        )

    episode_reader_factory, demand = demand_reader_factory(
        rule_episode_reader,
        text_episode_reader,
        mode=reader_mode,
        on_demand=config.editorial.thin_model_layer
        and bool(catalogued_period(context.date_ranges)),
        lean=lambda prepared: text_episode_reader(prepared, lean=True),
    )

    backend = ProductionPostCardBackend(
        config=config,
        context=context,
        people=people,
        thumbnail_cache=thumbnail_cache,
        store_path=store_path,
        ports=runtime_ports,
        fetch_preview=lambda asset_id: runtime_ports.fetch_preview(client, asset_id),
        attached_sources=lambda: source_snapshot or (),
        episode_demand=demand,
    )

    def attempt_directory() -> Path:
        return backend._context.artifact_dir

    episode_requester = _recorded(episode_requester, "episodes", attempt_directory)
    try:
        planner = TextEditorialPlanner(
            selection_request=selection_request,
            source_dependencies=EditorialDependencies(
                source_fetcher=source_fetcher,
                preview_jpeg=lambda asset: cached_preview_bytes(thumbnail_cache, asset.id),
            ),
            episode_reader_factory=episode_reader_factory,
            backend=backend,
            verdicts=EditorialVerdicts(store_path),
        )
    except BaseException:
        episode_store.close()
        raise
    runtime = RuntimeEditorialPlanner(
        planner,
        episode_store=episode_store,
        asset_ids=scope.asset_ids,
        config=config,
        backend=backend,
        person_expression=context.person_expression,
    )

    runtime._prepare_annotations = EvidencePreparation(
        readings=readings,
        client=client,
        thumbnail_cache=thumbnail_cache,
        ports=runtime_ports,
        artifact_dir=lambda: backend._context.artifact_dir,
    )
    return runtime


def build_smart_pipeline(
    client: SyncImmichClient,
    thumbnail_cache: ThumbnailCache,
    config: PipelineConfig | None = None,
    *,
    app_config: Config,
    editorial_context: EditorialRunContext,
    dry_run: bool = False,
    editorial_ports: EditorialRuntimePorts | None = None,
) -> SmartPipeline:
    """Construct the shared pipeline while retaining its existing public patch seam."""
    from immich_memories.analysis.smart_pipeline import SmartPipeline

    planner = build_editorial_planner(
        client=client,
        config=app_config,
        thumbnail_cache=thumbnail_cache,
        context=editorial_context,
        dry_run=dry_run,
        ports=editorial_ports,
    )
    return SmartPipeline(config=config, planner=planner)
