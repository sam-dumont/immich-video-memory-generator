"""Pipeline execution UI for Step 2: Clip Review."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from nicegui import ui

from immich_memories.api.immich import SyncImmichClient
from immich_memories.api.models import Asset, VideoClipInfo
from immich_memories.operations.cancellation import PipelineCancelled
from immich_memories.planning.auto_duration import (
    AutoDurationResult,
    resolve_trip_auto_duration,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from immich_memories.processing.timeline_budget import TimelinePlan

# The scorer's "average seconds per clip" dial, now a fixed planning estimate: the
# editor decides every carrier's seconds, so the preliminary timeline only needs a
# plausible density to size its content budget.
_EXPECTED_CLIP_SECONDS = 5.0


def _pipeline_summary_counts(result: dict) -> tuple[int, int, int]:
    """Return reviewed, expensive-analysis, and final-plan counts."""
    stats = result.get("stats", {})
    eligible = int(stats.get("eligible_count", stats.get("total_analyzed", 0)))
    deep = int(stats.get("deeply_analyzed_count", stats.get("total_analyzed", 0)))
    planned = int(stats.get("planned_count", stats.get("selected_count", 0)))
    return eligible, deep, planned


def render_pipeline_summary(result: dict) -> None:
    """Render pipeline completion summary."""
    from immich_memories.analysis.editorial_duration_advisory import editorial_duration_warning

    stats = result.get("stats", {})
    errors = result.get("errors", [])

    eligible_count, deeply_analyzed_count, planned_count = _pipeline_summary_counts(result)
    error_count = stats.get("error_count", 0)
    elapsed = stats.get("elapsed_seconds", 0)

    with ui.card().classes("w-full p-4").style("background: var(--im-success-bg)"):
        ui.label(
            f"Pipeline complete! Planned {planned_count} clips from "
            f"{eligible_count} eligible media items."
        ).classes("font-semibold").style("color: var(--im-success-text)")
        if warning := editorial_duration_warning(stats.get("editorial_duration_realization")):
            ui.label(warning).classes("text-sm").style("color: var(--im-warning)")

        with ui.row().classes("w-full gap-8 mt-4"):
            with ui.column().classes("items-center"):
                ui.label("Media Eligible").classes("text-sm").style(
                    "color: var(--im-text-secondary)"
                )
                ui.label(str(eligible_count)).classes("text-2xl font-bold")
            with ui.column().classes("items-center"):
                ui.label("Videos Deeply Analyzed").classes("text-sm").style(
                    "color: var(--im-text-secondary)"
                )
                ui.label(str(deeply_analyzed_count)).classes("text-2xl font-bold")
            with ui.column().classes("items-center"):
                ui.label("Clips Planned").classes("text-sm").style(
                    "color: var(--im-text-secondary)"
                )
                ui.label(str(planned_count)).classes("text-2xl font-bold")
            with ui.column().classes("items-center"):
                ui.label("Time Elapsed").classes("text-sm").style("color: var(--im-text-secondary)")
                time_str = f"{elapsed / 60:.1f}m" if elapsed > 60 else f"{elapsed:.0f}s"
                ui.label(time_str).classes("text-2xl font-bold")

        if error_count > 0:
            with ui.expansion(f"Errors ({error_count})", icon="warning").classes("mt-4"):
                for err in errors:
                    clip_id = err.get("clip_id", "Unknown")
                    error_msg = err.get("error", "Unknown error")
                    ui.label(f"{clip_id}: {error_msg}").style("color: var(--im-warning)")


_PROGRESS_STATUS_KEYS = [
    "indeterminate",
    "status",
    "started_at",
    "phase_label",
    "progress_fraction",
    "current_item",
    "current_asset_id",
    "current_index",
    "total_items",
    "elapsed",
    "eta",
    "avg_duration",
    "speed_ratio",
    "completed_count",
    "error_count",
    "last_completed_asset_id",
    "last_completed_segment",
    "last_completed_score",
    "last_completed_video_path",
    "last_completed_llm_description",
    "last_completed_llm_emotion",
    "last_completed_llm_interestingness",
    "last_completed_llm_quality",
    "last_completed_audio_categories",
]
_PROGRESS_DEFAULTS: dict[str, Any] = {
    "indeterminate": False,
    "status": "running",
    "started_at": None,
    "phase_label": "Processing",
    "progress_fraction": 0,
    "current_item": "",
    "current_index": 0,
    "total_items": 0,
    "elapsed": "0s",
    "eta": "--",
    "avg_duration": 0.0,
    "speed_ratio": 0.0,
    "completed_count": 0,
    "error_count": 0,
}


def _make_progress_callback(
    progress_state: dict[str, Any], cancelled: Callable[[], bool] = bool
) -> Any:
    """Return a progress callback that writes into the shared progress_state dict.

    `cancelled` is asked on every report; a True answer stops the run at the
    next request boundary through the planner's own cancellation scope.
    """

    def on_progress(status: dict) -> None:
        if cancelled():
            raise PipelineCancelled("Cancelled by user")
        for key in _PROGRESS_STATUS_KEYS:
            value = status.get(key, _PROGRESS_DEFAULTS.get(key))
            if key not in progress_state or progress_state[key] != value:
                progress_state[key] = value

    return on_progress


def _eligible_pipeline_media(
    state: Any,
    clips: list[VideoClipInfo],
) -> tuple[list[VideoClipInfo], list[Asset]]:
    """Return only media explicitly kept in the Step 2 review."""
    eligible_clips = [clip for clip in clips if clip.asset.id in state.selected_clip_ids]
    eligible_photos = []
    if state.include_photos:
        eligible_photos = [
            photo for photo in state.photo_assets if photo.id in state.selected_photo_ids
        ]
    return eligible_clips, eligible_photos


def _resolve_auto_duration_for_selection(
    state: Any,
    clips: list[VideoClipInfo],
    photos: list[Asset],
) -> AutoDurationResult | None:
    """Resolve trip Auto duration from the reviewed eligible media."""
    if state.memory_type != "trip" or state.duration_mode != "auto":
        return None

    config = state.config
    if config is None:
        from immich_memories.config import get_config

        config = get_config()
    title_config = config.title_screens
    title_duration = title_config.title_duration if title_config.enabled else 0.0
    ending_duration = title_config.ending_duration if title_config.enabled else 0.0
    result = resolve_trip_auto_duration(
        clips,
        photos,
        avg_clip_duration=_EXPECTED_CLIP_SECONDS,
        photo_duration=state.photo_duration,
        title_duration=title_duration,
        ending_duration=ending_duration,
    )
    state.target_duration = result.total_seconds / 60.0
    return result


def _configure_timeline_for_selection(
    state: Any,
    clips: list[VideoClipInfo],
    photos: list[Asset],
) -> TimelinePlan:
    """Persist one preliminary timeline and apply its content budget."""
    from immich_memories.generate import GenerationParams
    from immich_memories.generate_settings import _build_title_settings
    from immich_memories.processing.timeline_budget import plan_timeline

    config = state.config
    if config is None:
        from immich_memories.config import get_config

        config = get_config()
    person = state.selected_person
    date_range = state.date_range
    planning_params = GenerationParams(
        clips=clips,
        output_path=Path("ui-timeline-plan.mp4"),
        config=config,
        memory_type=state.memory_type,
        person_name=person.name if person else None,
        date_start=date_range.start if date_range else None,
        date_end=date_range.end if date_range else None,
        memory_preset_params=state.memory_preset_params,
    )
    title_settings = _build_title_settings(planning_params, config, [])
    plan = plan_timeline(
        [*clips, *photos],
        title_settings,
        state.target_duration_seconds,
        state.memory_type,
        expected_clip_duration=_EXPECTED_CLIP_SECONDS,
        transition_mode="smart",
        transition_duration=config.defaults.transition_duration,
    )
    state.timeline_plan = plan
    return plan


def _ui_timing_policy(state: Any, config: Any):
    from immich_memories.processing.editorial_timing import build_editorial_timing_policy
    from immich_memories.ui.pages._step4_generate import _TRANSITION_MAP

    person = state.selected_person
    date_range = state.date_range
    return build_editorial_timing_policy(
        config=config,
        target_seconds=state.target_duration_seconds,
        memory_type=state.memory_type,
        date_start=date_range.start if date_range else None,
        date_end=date_range.end if date_range else None,
        person_name=person.name if person else None,
        memory_preset_params=state.memory_preset_params,
        transition=_TRANSITION_MAP.get(
            state.generation_options.get("transition", "Smart (mix of fades & cuts)"), "crossfade"
        ),
        transition_duration=config.defaults.transition_duration,
    )


def _editorial_source_id(source: VideoClipInfo | Asset) -> str:
    """Return the Immich identity carried by either production source type."""
    return source.asset.id if isinstance(source, VideoClipInfo) else source.id


def _ui_editorial_people(state: Any) -> tuple[str, ...]:
    """Keep the names the owner selected, in their displayed order."""
    expression = _ui_person_expression(state)
    if expression is not None:
        return expression.leaf_values
    names = tuple(str(name) for name in state.memory_preset_params.get("person_names", ()) if name)
    if names:
        return names
    person = state.selected_person
    return (person.display_name,) if person is not None else ()


def _ui_person_expression(state: Any) -> Any:
    """Validate explicit grouped scope before using even already-loaded media."""
    if state.memory_preset_params.get("person_expression") is None and not getattr(
        state, "person_expression_error", None
    ):
        return None
    state.resolved_person_expression()  # Reject unsupported scope or unresolved names.
    return state.person_expression


def _ui_editorial_label(
    state: Any,
    *,
    product: str,
    date_ranges: tuple[Any, ...],
    people: tuple[str, ...],
) -> str:
    """Build prompt-facing identity without collapsing discontinuous windows."""
    if product == "album" and state.album_name:
        return str(state.album_name)
    if product == "trip" and state.memory_preset_params.get("location_name"):
        return str(state.memory_preset_params["location_name"])
    expression = _ui_person_expression(state)
    if expression is not None:
        return expression.display_label
    if people:
        return " and ".join(people)
    if date_ranges:
        return "; ".join(date_range.description for date_range in date_ranges)
    return product.replace("_", " ").title()


def _ui_editorial_key(
    state: Any,
    *,
    product: str,
    date_ranges: tuple[Any, ...],
    people: tuple[str, ...],
    full_sources: tuple[VideoClipInfo | Asset, ...],
) -> str:
    """Return one stable cache identity even before Step 4 has a run id."""
    import hashlib
    import json

    expression = _ui_person_expression(state)
    expression_json = (
        json.dumps(expression.to_dict(), sort_keys=True, separators=(",", ":"))
        if expression is not None
        else None
    )
    suffix = (
        "-people-" + hashlib.sha256(expression_json.encode()).hexdigest()[:16]
        if expression_json is not None
        else ""
    )
    if state.active_run_id:
        return str(state.active_run_id) + suffix
    if state.output_path:
        return state.output_path.stem + suffix

    identity_parts = [product, str(state.album_id or ""), state.person_match, *people]
    if expression_json is not None:
        identity_parts.append(expression_json)
    identity_parts.extend(
        f"{date_range.start.isoformat()}/{date_range.end.isoformat()}" for date_range in date_ranges
    )
    if product == "album":
        identity_parts.extend(_editorial_source_id(source) for source in full_sources)
    if product == "special_day" and state.memory_preset_params.get("event_id"):
        identity_parts.append(str(state.memory_preset_params["event_id"]))
    digest = hashlib.sha256("\n".join(identity_parts).encode()).hexdigest()[:16]
    return f"{product}-{digest}"


def _cut_identity(state: Any) -> tuple[str, tuple[Any, ...], tuple[str, ...], tuple[Any, ...]]:
    """The four facts a cut's key and context are built from: product, windows, people, sources."""
    product = str(state.memory_type or "custom")
    date_ranges = () if product == "album" else tuple(state.date_ranges)
    return product, date_ranges, _ui_editorial_people(state), (*state.clips, *state.photo_assets)


def ui_cut_key(state: Any) -> str:
    """The cache identity of the cut the session would run now, before its worker starts."""
    product, date_ranges, people, full_sources = _cut_identity(state)
    return _ui_editorial_key(
        state, product=product, date_ranges=date_ranges, people=people, full_sources=full_sources
    )


def _build_ui_editorial_context(
    state: Any,
    app_config: Any,
    clips: list[VideoClipInfo],
    photos: list[Asset],
) -> Any:
    """Adapt immutable wizard truth to the shared production runtime contract."""
    from immich_memories.analysis.editorial_runtime import EditorialRunContext
    from immich_memories.analysis.special_event_scope import read_special_event_admission

    product, date_ranges, people, full_sources = _cut_identity(state)
    reviewed_ids = {
        *(clip.asset.id for clip in clips),
        *(photo.id for photo in photos),
    }
    owner_excluded_asset_ids = tuple(
        _editorial_source_id(source)
        for source in full_sources
        if _editorial_source_id(source) not in reviewed_ids
    )
    key = ui_cut_key(state)
    person_match: Literal["and", "or"] = "or" if state.person_match == "or" else "and"
    return EditorialRunContext(
        key=key,
        label=_ui_editorial_label(
            state,
            product=product,
            date_ranges=date_ranges,
            people=people,
        ),
        product=product,
        date_ranges=date_ranges,
        target_seconds=state.target_duration_seconds,
        hemisphere=state.memory_preset_params.get("hemisphere", "north"),
        render_timing=_ui_timing_policy(state, app_config),
        artifact_dir=app_config.cache.cache_path / "editorial-runs" / key,
        people=people,
        person_match=person_match,
        person_expression=_ui_person_expression(state),
        accept_any_provenance=state.accept_any_provenance,
        trip=product == "trip",
        album_ref=str(state.album_id or "") if product == "album" else None,
        album_sources=full_sources if product == "album" else (),
        owner_excluded_asset_ids=owner_excluded_asset_ids,
        special_event_id=(
            state.memory_preset_params.get("event_id") if product == "special_day" else None
        ),
        event_admission=read_special_event_admission(
            state.memory_preset_params.get("event_admission")
        ),
        event_asset_ids=(
            tuple(state.memory_preset_params.get("asset_ids") or ())
            if product == "special_day"
            else ()
        ),
    )


def _adopt_result(state: Any, result: Any) -> None:
    """Write one finished cut onto the session: what shipped, where the plan went, how it is timed."""
    state.pipeline_result = {
        "selected_clips": result.selected_clips,
        "editorial_selections": result.editorial_selections,
        "clip_segments": result.clip_segments,
        "errors": result.errors,
        "stats": result.stats,
        "coverage": result.coverage,
    }
    state.pipeline_selected_clips = result.selected_clips
    attempt_dir = result.stats.get("editorial_attempt_directory")
    state.editorial_attempt_dir = Path(attempt_dir) if attempt_dir else None
    state.editorial_render_timing = result.stats.get("editorial_render_timing")
    if state.editorial_render_timing is not None:
        from immich_memories.processing.editorial_timing import read_editorial_timeline

        state.timeline_plan = read_editorial_timeline(state.editorial_render_timing)
    state.editorial_selections = result.editorial_selections
    state.selected_clip_ids = {c.asset.id for c in result.selected_clips}
    state.clip_segments = result.clip_segments

    # Photos are now in selected_clips as IMAGE-type assets
    # Tell Step 4 not to re-add them via the old path
    if state.include_photos and state.photo_assets:
        from immich_memories.api.models import AssetType

        state.selected_photo_ids = {
            c.asset.id for c in result.selected_clips if c.asset.type == AssetType.IMAGE
        }


def _run_pipeline_blocking(
    state: Any,
    config: Any,
    clips: list[VideoClipInfo],
    photos: list[Asset],
    progress_state: dict[str, Any],
) -> None:
    """Run the SmartPipeline in a background thread — no UI calls here."""
    tc = state.thumbnail_cache
    if tc is None:
        raise RuntimeError("Thumbnail cache not initialized")
    try:
        with SyncImmichClient(
            base_url=state.immich_url,
            api_key=state.immich_api_key,
            api_version=state.immich_api_version,
        ) as client:
            from immich_memories.analysis.editorial_runtime import build_smart_pipeline
            from immich_memories.config import get_config

            app_config = get_config()
            pipeline = build_smart_pipeline(
                client=client,
                analysis_cache=state.analysis_cache,
                thumbnail_cache=tc,
                config=config,
                analysis_config=app_config.analysis,
                app_config=app_config,
                editorial_context=_build_ui_editorial_context(
                    state,
                    app_config,
                    clips,
                    photos,
                ),
                dry_run=False,
                triage=None,
            )

            source_photos = photos if state.include_photos else []
            all_candidates, result = pipeline.run_editorial_source(
                [*clips, *source_photos],
                progress_callback=_make_progress_callback(
                    progress_state, lambda: state.cancel_requested
                ),
                include_live_photos=(
                    state.include_live_photos and app_config.analysis.include_live_photos
                ),
            )
            result.stats.update(
                {
                    "eligible_count": len(clips) + len(photos),
                    "deeply_analyzed_count": pipeline.last_deep_analysis_count,
                    "planned_count": len(result.selected_clips),
                }
            )

            _adopt_result(state, result)
            state.pipeline_running = False
            progress_state["done"] = True
    except PipelineCancelled:
        logger.info("Pipeline cancelled by user")
        state.pipeline_running = False
        progress_state["done"] = True
    except Exception as e:  # WHY: UI graceful degradation
        logger.exception("Pipeline error")
        state.pipeline_running = False
        progress_state["error"] = str(e)
        progress_state["done"] = True


def _detect_overnight_bases(
    state: Any,
    clips: list[VideoClipInfo] | None = None,
) -> list | None:
    """Detect overnight stop bases for trip memories."""
    source_clips = state.clips if clips is None else clips
    if not (state.memory_type == "trip" and source_clips):
        return None
    try:
        from immich_memories.analysis.trip_detection import detect_overnight_stops

        trip_assets = [c.asset for c in source_clips]
        return detect_overnight_stops(trip_assets) or None
    except Exception:  # WHY: UI graceful degradation
        logger.debug("Trip segment detection failed", exc_info=True)
        return None


def _build_pipeline_config(
    state: Any,
    clips: list[VideoClipInfo] | None = None,
) -> Any:
    """Build PipelineConfig from app state: the pool switches the route reads, no dials."""
    from immich_memories.analysis.smart_pipeline import PipelineConfig
    from immich_memories.config_loader import Config

    plan = state.timeline_plan
    # Defaults stand in only before the wizard has loaded a config.
    return PipelineConfig.from_app_config(
        state.config or Config(),
        target_duration_seconds=plan.content_budget if plan is not None else None,
        hdr_only=state.hdr_only,
        overnight_bases=_detect_overnight_bases(state, clips),
        accept_any_provenance=state.accept_any_provenance,
    )
