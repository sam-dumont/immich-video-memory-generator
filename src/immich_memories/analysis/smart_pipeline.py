"""The pipeline surface the CLI and the UI drive.

One route: `run_editorial_source()` hands the requested sources to the editorial
planner and projects its plan into a `PipelineResult`. Nothing here scores,
filters or ranks; the planner decides every carrier.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from immich_memories.analysis import selection_trace as trace
from immich_memories.analysis.editorial_projection import (
    EditorialStageReporter,
    editorial_clip_segment,
    editorial_membership,
)
from immich_memories.analysis.progress import ProgressTracker

if TYPE_CHECKING:
    from immich_memories.analysis.editorial_planner import (
        EditorialPlan,
        EditorialPlanner,
        EditorialSelection,
    )
    from immich_memories.api.models import Asset, VideoClipInfo

logger = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    """The per-run switches the editorial route reads. None of them comes from config.yaml."""

    hdr_only: bool = False


@dataclass
class PipelineResult:
    """Result of the smart pipeline."""

    selected_clips: list[VideoClipInfo]
    clip_segments: dict[str, tuple[float, float]]  # asset_id -> (start, end)
    errors: list[dict]  # List of {clip_id, error}
    stats: dict = field(default_factory=dict)
    # Kept separate from mutable API models: the final editor may deliberately
    # render a video as a still or a Live Photo as motion.
    editorial_selections: tuple[EditorialSelection, ...] = ()


@dataclass
class ClipWithSegment:
    """A clip with its optimal segment."""

    clip: VideoClipInfo
    start_time: float
    end_time: float
    score: float
    analyzed: bool = True


class SmartPipeline:
    """Run the editorial planner over the requested sources and project its cut."""

    def __init__(
        self,
        config: PipelineConfig | None = None,
        *,
        planner: EditorialPlanner | None = None,
    ):
        self.config = config or PipelineConfig()
        self.tracker = ProgressTracker()
        self._planner = planner

    def run(
        self,
        clips: list[VideoClipInfo],
        progress_callback: Callable[[dict], None] | None = None,
    ) -> PipelineResult:
        """Return the final cut from the sole production editorial source route."""
        _, result = self.run_editorial_source(clips, progress_callback)
        return result

    def run_editorial_source(
        self,
        sources: list[Asset | VideoClipInfo],
        progress_callback: Callable[[dict], None] | None = None,
        *,
        include_live_photos: bool = True,
    ) -> tuple[list[ClipWithSegment], PipelineResult]:
        """Plan from full requested metadata, with no legacy analysis or fallback."""
        from immich_memories.analysis.editorial_source_route import EditorialSourcePlanner
        from immich_memories.operations.cancellation import PipelineCancelled, cancellation_scope

        if not isinstance(self._planner, EditorialSourcePlanner):
            raise RuntimeError("pipeline has no production editorial source route")
        report_stage = EditorialStageReporter(self.tracker, progress_callback)
        try:
            self.tracker.start()
            with (
                cancellation_scope(report_stage.repeat),
                trace.tracing(trace.path_from_env()),
            ):
                candidates, result = self._planned_editorial_source(
                    sources, report_stage, include_live_photos=include_live_photos
                )
                report_stage("Editorial selection complete", status="complete")
                # Only a successful cut completes the tracker. Its display
                # callbacks cannot turn an already finished selection into failure.
                with contextlib.suppress(Exception, PipelineCancelled):
                    self.tracker.finish()
                return candidates, result
        except PipelineCancelled:
            with contextlib.suppress(PipelineCancelled):
                report_stage("Editorial selection cancelled", status="cancelled")
            raise
        except Exception:
            # A stop requested by a terminal display callback must not replace
            # the source failure that caused this notification.
            with contextlib.suppress(PipelineCancelled):
                report_stage("Editorial selection failed", status="failed")
            raise

    def _planned_editorial_source(
        self,
        sources: list[Asset | VideoClipInfo],
        report_stage: EditorialStageReporter,
        *,
        include_live_photos: bool,
    ) -> tuple[list[ClipWithSegment], PipelineResult]:
        """Ask the planner for the cut, then put its provenance on the result."""
        active_trace = trace.active()
        if active_trace is None:
            raise RuntimeError("editorial source route requires an active trace")
        planned = self._planner.plan_source(  # type: ignore[union-attr]
            sources,
            trace=active_trace,
            include_live_photos=include_live_photos,
            hdr_only=self.config.hdr_only,
            on_stage=report_stage,
        )
        if planned.plan.unavailable_reason is not None:
            raise RuntimeError("editorial source route returned unavailable evidence")
        candidates = list(planned.candidates)
        result = self._project_editorial_plan(planned.plan, candidates, source_verified=True)
        result.stats.update(
            {
                "selection_route": "editorial-source",
                "source_evidence": "canonical annotations and native editorial checks",
                "legacy_deep_analysis_count": 0,
                "total_analyzed": 0,
                "source_candidate_count": len(candidates),
                "editorial_render_adjustments": list(planned.render_adjustments),
                "editorial_duration_realization": planned.duration_realization,
            }
        )
        attempt_dir = getattr(self._planner, "last_attempt_directory", None)
        if attempt_dir is not None:
            result.stats["editorial_attempt_directory"] = str(attempt_dir)
        active_trace.record("editorial final cut", candidates, result.selected_clips)
        if planned.render_timing is not None:
            result.stats["editorial_render_timing"] = planned.render_timing
        trace.record_favourite_law(candidates, result.selected_clips)
        result.stats["elapsed_seconds"] = self.tracker.progress.elapsed_seconds
        return candidates, result

    def _project_editorial_plan(
        self,
        plan: EditorialPlan,
        analyzed: list[ClipWithSegment],
        *,
        source_verified: bool = False,
    ) -> PipelineResult:
        """Validate final membership/timing without choosing or repairing any carrier."""
        by_id = editorial_membership(plan, analyzed)
        selected = [by_id[item.asset_id] for item in plan.selections]
        clip_segments = {
            item.asset_id: editorial_clip_segment(item, candidate, source_verified=source_verified)
            for item, candidate in zip(plan.selections, selected, strict=True)
        }
        return PipelineResult(
            selected_clips=[candidate.clip for candidate in selected],
            clip_segments=clip_segments,
            errors=[],
            stats={
                "total_analyzed": len(analyzed),
                "selected_count": len(selected),
                "error_count": 0,
                "elapsed_seconds": self.tracker.progress.elapsed_seconds,
            },
            editorial_selections=plan.selections,
        )
