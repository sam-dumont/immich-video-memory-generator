"""The one public path from source through the editorial passes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.analysis.period_insight import PassZeroResult, run_period_insight
from immich_memories.analysis.selection_cull import CullPassResult, run_cull
from immich_memories.analysis.selection_source import (
    EditorialDependencies,
    EditorialSelectionRequest,
    PreparedEditorialSource,
    prepare_editorial_source,
)
from immich_memories.analysis.selection_trace import Trace

if TYPE_CHECKING:
    from immich_memories.analysis.editorial_gateway import EditorialGateway
    from immich_memories.analysis.visual_request_planner import VisionRequestLimits

__all__ = ["EditorialInsightCullResult", "run_editorial_insight_cull"]


@dataclass(frozen=True)
class EditorialInsightCullResult:
    """The public replacement slice through source, Insight, and Cull."""

    prepared: PreparedEditorialSource
    pass_zero: PassZeroResult
    pass_one: CullPassResult


def run_editorial_insight_cull(
    request: EditorialSelectionRequest,
    dependencies: EditorialDependencies,
    *,
    gateway_factory: Callable[[Trace], EditorialGateway],
    sheet_output_dir: Path,
    frame_cache_dir: Path | None,
    review_output_dir: Path,
    limits: VisionRequestLimits | None = None,
) -> EditorialInsightCullResult:
    """Run the single source-to-Pass-1 path on one shared trace and gateway."""
    prepared = prepare_editorial_source(request, dependencies)
    gateway = gateway_factory(prepared.trace)
    pass_zero = run_period_insight(
        prepared,
        requester=gateway,
        sheet_output_dir=sheet_output_dir,
        frame_cache_dir=frame_cache_dir,
        limits=limits,
    )
    pass_one = run_cull(prepared, pass_zero, review_output_dir=review_output_dir)
    return EditorialInsightCullResult(prepared, pass_zero, pass_one)
