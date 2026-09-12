"""Shared production composition for the store-backed editorial planner."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeVar

from immich_memories.analysis.annotation_lines import StoredAnnotationLineReader
from immich_memories.analysis.editorial_attached_outcomes import AttachedOutcomeReplay
from immich_memories.analysis.editorial_evidence_provenance import AttemptEvidenceProvenance
from immich_memories.analysis.editorial_motion_outcomes import MotionOutcomeReplay
from immich_memories.analysis.editorial_orchestration import TextEditorialPlanner
from immich_memories.analysis.editorial_people import adapt_editorial_people
from immich_memories.analysis.editorial_planner import EditorialPlan
from immich_memories.analysis.editorial_rule_episodes import (
    EpisodeReader,
    RuleEpisodeReader,
    rule_period,
)
from immich_memories.analysis.editorial_runtime_backend import ProductionPostCardBackend
from immich_memories.analysis.editorial_runtime_ports import EditorialRuntimePorts
from immich_memories.analysis.editorial_source import FullEditorialSource
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
from immich_memories.analysis.text_episode_reader import (
    TEXT_EPISODE_PROMPT_VERSION,
    CachedTextEpisodeReader,
)
from immich_memories.analysis.text_period_insight import (
    TEXT_PERIOD_PROMPT_VERSION,
    run_text_period_insight,
)
from immich_memories.analysis.text_period_wire import TEXT_PERIOD_SCHEMA_VERSION
from immich_memories.analysis.thumbnail_prefetch import cached_preview_bytes
from immich_memories.api.models import Asset, VideoClipInfo
from immich_memories.api.person_expression import PersonExpression
from immich_memories.cache.editorial_verdicts import EditorialVerdicts
from immich_memories.people.context import PersonPromptContext
from immich_memories.processing.editorial_timing import EditorialTimingPolicy
from immich_memories.security import write_secret_file
from immich_memories.store.episode_readings import EpisodeReadingProducer, EpisodeReadingStore
from immich_memories.store.period_insights import PeriodInsightProducer, PeriodInsightStore
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
    base_brief: str | None = None
    motion_outcome_replay: MotionOutcomeReplay | None = None
    person_expression: PersonExpression | None = None
    attached_outcome_replay: AttachedOutcomeReplay | None = None
    render_timing: EditorialTimingPolicy | None = None
    hemisphere: Literal["north", "south"] = "north"

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
        period_store: PeriodInsightStore,
        asset_ids: tuple[str, ...] | None = None,
        config: Config | None = None,
        backend: ProductionPostCardBackend | None = None,
        person_expression: PersonExpression | None = None,
    ) -> None:
        self._planner = planner
        self._config = config
        self._backend = backend
        self._episode_store = episode_store
        self._period_store = period_store
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
        on_stage: Callable[[str], None] | None = None,
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
            "audience": "family",
            "hemisphere": context.hemisphere,
            "date_ranges": [[r.start.isoformat(), r.end.isoformat()] for r in context.case_ranges],
            "requested_assets": [_asset(source).id for source in sources],
            "include_live_photos": include_live_photos,
            "hdr_only": hdr_only,
        }
        with EditorialAttempt(context.artifact_dir, request=request) as attempt:
            self.last_attempt_directory = attempt.directory
            self._backend._context = replace(context, artifact_dir=attempt.directory)

            def stage(label: str) -> None:
                attempt.stage(label)
                if on_stage is not None:
                    on_stage(label)

            try:
                result = self._plan_source(
                    sources,
                    trace=trace,
                    include_live_photos=include_live_photos,
                    hdr_only=hdr_only,
                    on_stage=stage,
                )
                attempt.complete(
                    selected=len(result.plan.selections),
                    outcome="selected" if result.plan.selections else "no_selection",
                    duration_realization=result.duration_realization,
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
        on_stage: Callable[[str], None] | None = None,
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
                on_stage("Preparing source metadata")
            prepared = self._prepared_source(trace=trace, on_stage=on_stage)
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
                verified_segments=False,
                on_stage=on_stage,
            )
            if plan.unavailable_reason is not None:
                raise RuntimeError(
                    f"editorial source evidence unavailable: {plan.unavailable_reason}"
                )
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
                on_stage("Validating selected source timing")
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

    def _prepared_source(self, *, trace: Trace, on_stage: Callable[[str], None] | None) -> Any:
        if self._prepare_annotations is None:
            return self._planner.prepare_source(trace=trace)
        # Preparation sees the full eligible corpus. Only the final source
        # pass belongs to the plan trace (excluded_ids reads that pass).
        preliminary = self._planner.prepare_source(trace=Trace(), include_previews=False)
        exclusions = self._prepare_annotations(preliminary, on_stage)
        return self._planner.prepare_source(trace=trace, evidence_exclusions=exclusions)

    def close(self) -> None:
        """Release every thread-owned SQLite connection; later reads reopen safely."""
        self._episode_store.close()
        self._period_store.close()


class EditorialInputsRequired(RuntimeError):
    """Selection needs prepared annotation evidence before it can run."""

    def __init__(self, store_path: Path, *, detail: str = "") -> None:
        self.store_path = store_path
        super().__init__(
            f"Story-first selection needs prepared annotations at {store_path}. "
            "Prepare this library's annotations or set advanced.editorial.annotation_database "
            "to its existing annotation store."
            + (f" Missing or unavailable: {detail}" if detail else "")
        )


def _ensure_annotation_store(store_path: Path) -> None:
    if store_path.is_file():
        return
    import sqlite3

    from immich_memories.store.editorial_preparation import initialize, private_database_path

    with closing(sqlite3.connect(private_database_path(store_path))) as connection:
        initialize(connection)


@dataclass(frozen=True, slots=True)
class _AnnotationReadings:
    """One annotation-line contract shared by episode reading and the source gate."""

    store_path: Path
    config: Config
    people: Mapping[str, PersonPromptContext]

    def reader(self, prepared: Any) -> StoredAnnotationLineReader:
        editorial = self.config.editorial
        return StoredAnnotationLineReader(
            store_path=self.store_path,
            candidates=prepared.candidates,
            description_model=editorial.description_model,
            head_versions=editorial.head_versions,
            pixel_producer_key=editorial.pixel_producer_key,
            people_context=self.people,
        )


def _log_preparation(result: Any) -> None:
    """Name the tier and what it cost, in the terminal, on the machine that paid for it.

    A wall-clock total cannot tell a self-hoster which producer their box cannot
    afford, and the artifact holding the same numbers is inside the attempt tree.
    """
    rates = " ".join(
        f"{stage} {seconds:.3f}s/pic" for stage, seconds in sorted(result.stage_rates().items())
    )
    logger.info(
        "preparation tier=%s: %d pictures requested%s",
        result.tier,
        result.requested,
        f"; {rates}" if rates else "; nothing to produce",
    )


@dataclass(frozen=True, slots=True)
class _EvidencePreparation:
    """Produce every annotation the story-first read needs, then gate screen documents."""

    readings: _AnnotationReadings
    client: FullEditorialSource
    thumbnail_cache: ThumbnailCache
    ports: EditorialRuntimePorts
    artifact_dir: Callable[[], Path]

    def __call__(self, prepared: Any, on_stage: Callable[[str], None] | None) -> dict[str, Any]:
        result = self._produce(prepared, on_stage)
        _log_preparation(result)
        write_secret_file(
            self.artifact_dir() / "preparation.private.json",
            json.dumps(
                asdict(result) | {"seconds_per_picture": result.stage_rates()},
                ensure_ascii=False,
                indent=2,
            ),
        )
        if not result.complete:
            missing = ", ".join(
                f"{key}: {len(ids)}" for key, ids in result.missing_by_producer.items()
            )
            # A count of missing facts is a symptom. When a producer refused --
            # no model, no endpoint -- its own sentence says why, so it goes in
            # the message rather than only into preparation.private.json.
            raise EditorialInputsRequired(
                self.readings.store_path,
                detail="; ".join(filter(None, (missing, *result.producer_failures))),
            )
        if not prepared.candidate_ids:
            return {}
        return self._screen_documents(prepared)

    def _produce(self, prepared: Any, on_stage: Callable[[str], None] | None) -> Any:
        from immich_memories.analysis.editorial_preparation import prepare_editorial_annotations
        from immich_memories.operations.cut_progress import StageProgressWriter

        config = self.readings.config
        batch_size = config.editorial.preparation.batch_size
        # The sentence and the numbers are published together, on one throttle,
        # so a watcher never sees a bar disagreeing with the row above it.
        live = StageProgressWriter(self.artifact_dir)

        def progress(stage: str, done: int, total: int) -> None:
            if done not in {0, total} and done % batch_size:
                return
            if on_stage is not None:
                on_stage(live.publish(stage, done, total).stage_label)

        prepare = self.ports.prepare_annotations or prepare_editorial_annotations
        return prepare(
            assets=tuple(candidate.source for candidate in prepared.candidates),
            store_path=self.readings.store_path,
            thumbnail_cache=self.thumbnail_cache,
            preparation_config=config.editorial.preparation,
            triage_config=config.triage,
            head_versions=config.editorial.head_versions,
            description_model=config.editorial.description_model,
            pixel_producer_key=config.editorial.pixel_producer_key,
            fetch_preview=lambda asset_id: self.ports.fetch_preview(self.client, asset_id),
            progress=progress,
            on_asset=live.note_asset,
        )

    def _screen_documents(self, prepared: Any) -> dict[str, Any]:
        from immich_memories.analysis.editorial_source_gate import (
            SCREEN_DOCUMENT_GATE_VERSION,
            screen_document_rejections,
        )

        batch = self.readings.reader(prepared).lines_for(prepared.candidate_ids)
        if batch.missing_asset_ids:
            raise EditorialInputsRequired(
                self.readings.store_path,
                detail=f"{len(batch.missing_asset_ids)} unreadable annotation lines; "
                + "; ".join(batch.warnings),
            )
        exclusions: dict[str, Any] = screen_document_rejections(batch)
        write_secret_file(
            self.artifact_dir() / "source-gate.private.json",
            json.dumps(
                {"version": SCREEN_DOCUMENT_GATE_VERSION, "excluded": exclusions},
                ensure_ascii=False,
                indent=2,
            ),
        )
        return exclusions


def _reading_requesters(config, ports, reader_mode):
    if reader_mode == "rules":
        return "rules-v1", None, None
    return (
        semantic_text_model_identity(config.llm, thinking=False),
        ports.episode_requester_factory(config),
        ports.period_requester_factory(config),
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
    if reader_mode == "rules" and context.product == "custom":
        raise ValueError("custom subjects require a model reader; rules use captured metadata only")
    store_path = config.editorial.resolve_annotation_database(config.cache.cache_path)
    _ensure_annotation_store(store_path)
    runtime_ports = ports or EditorialRuntimePorts()
    context_by_id = runtime_ports.load_people()
    people = adapt_editorial_people(context_by_id)
    episode_store = runtime_ports.episode_store_factory(store_path)
    period_store = runtime_ports.period_store_factory(store_path)
    model_id, episode_requester, period_requester = _reading_requesters(
        config, runtime_ports, reader_mode
    )
    period_producer = PeriodInsightProducer(
        model_id=model_id,
        prompt_version=TEXT_PERIOD_PROMPT_VERSION,
        schema_version=TEXT_PERIOD_SCHEMA_VERSION,
    )
    scope = SourceScope(
        date_ranges=context.date_ranges,
        asset_ids=context.event_asset_ids if context.special_event_id else None,
        excluded_filename_patterns=tuple(config.analysis.exclude_filename_patterns),
        stills_need_a_camera=config.analysis.exclude_stills_without_camera_exif,
        min_source_short_side=config.analysis.min_source_short_side,
        accept_any_provenance=context.accept_any_provenance,
        include_off_timeline=False,
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

    readings = _AnnotationReadings(store_path=store_path, config=config, people=context_by_id)

    def episode_reader_factory(prepared: Any) -> EpisodeReader:
        annotations = readings.reader(prepared)
        if reader_mode == "rules":
            return RuleEpisodeReader(annotations)
        assert episode_requester is not None
        contract = annotations.contract
        producer = EpisodeReadingProducer(
            model_id=model_id,
            prompt_version=TEXT_EPISODE_PROMPT_VERSION,
            schema_version=TEXT_EPISODE_SCHEMA_VERSION,
            annotation_renderer_version=contract.renderer_version,
            annotation_versions=contract.producer_versions,
        )
        return CachedTextEpisodeReader(
            store=episode_store,
            producer=producer,
            annotations=annotations,
            requester=episode_requester,
            record_evidence=lambda episodes: evidence_provenance.capture(
                episodes, directory=backend._context.artifact_dir
            ),
        )

    def period_reader(episodes: Any) -> Any:
        if reader_mode == "rules":
            return rule_period(episodes)
        assert period_requester is not None
        return run_text_period_insight(
            episodes,
            store=period_store,
            producer=period_producer,
            requester=period_requester,
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
    )
    if isinstance(period_requester, SyncTextPromptRequester):
        period_requester = replace(
            period_requester,
            artifacts=TextPromptArtifacts(lambda: backend._context.artifact_dir, stage="period"),
        )
    try:
        planner = TextEditorialPlanner(
            selection_request=selection_request,
            source_dependencies=EditorialDependencies(
                source_fetcher=source_fetcher,
                preview_jpeg=lambda asset: cached_preview_bytes(thumbnail_cache, asset.id),
            ),
            episode_reader_factory=episode_reader_factory,
            period_reader=period_reader,
            backend=backend,
            verdicts=EditorialVerdicts(store_path),
        )
    except BaseException:
        episode_store.close()
        period_store.close()
        raise
    runtime = RuntimeEditorialPlanner(
        planner,
        episode_store=episode_store,
        period_store=period_store,
        asset_ids=scope.asset_ids,
        config=config,
        backend=backend,
        person_expression=context.person_expression,
    )

    runtime._prepare_annotations = _EvidencePreparation(
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
