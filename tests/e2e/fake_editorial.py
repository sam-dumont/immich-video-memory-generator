"""A hermetic stand-in for the story-first editorial route.

The launch smoke runs the real NiceGUI app against the fake Immich service in
``fake_immich.py``. The sole production selector needs two things a hermetic
launch cannot have: a text model to read the period with, and an annotation
store already prepared for this library. This module supplies exactly what
those two boundaries produce -- a durable attempt tree on disk and one
``(candidates, PipelineResult)`` pair -- so everything downstream of selection
(the Step 2 summary, the review page, Step 4 and the real FFmpeg render) runs
against unmodified production code.

Nothing here decides anything an editor would decide: the six fake sources all
ship, in capture order, each held for the duration its own metadata allows.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from immich_memories.security import write_secret_file

# The launch fixture owns one attempt, and it is the same one on every run:
# a repeated launch must not leave a growing pile of attempt directories in
# the workspace, and a fixed ID makes the tree easy to read after a failure.
ATTEMPT_KEY = "launch-smoke-editorial"
ATTEMPT_ID = "20240630T120000Z-0e2e0e2e0e2e"

# The exact labels `RuntimeEditorialPlanner` reports through `on_stage`, in the
# order the real route reaches them. Step 2 shows the current one, so a wrong
# label here would hide a real regression in the progress surface.
STAGES = (
    "Preparing source metadata",
    "Reading event evidence",
    "Reading the period account",
    "Building editorial cards",
    "Editing the memory",
    "Validating selected source timing",
)

# Long enough that a reload lands on a running cut, short enough that the whole
# scripted selection costs under a second.
_STAGE_SECONDS = 0.15

# Both from the production carrier vocabulary: motion is capped, a still is held.
_MOTION_CAP_SECONDS = 6.0
_STILL_SECONDS = 4.0

_ACCEPTED_SHORTFALL_FRACTION = 0.15
_FIXTURE_TIME = "2024-06-30T12:00:00+00:00"

_THESIS = (
    "A month of short test-pattern captures, three separate days apart, "
    "recorded by one device with nothing else going on around them."
)

# Three weighed stories over the same period: one carries the memory, one
# supports it, one is texture. The weights are the output vocabulary.
_EPISODES = (
    {
        "key": "S0001",
        "title": "The first captures",
        "weight": "dominant",
        "role": "central",
        "purpose": "Opens the month and shows what the device was recording",
    },
    {
        "key": "S0002",
        "title": "The midmonth captures",
        "weight": "major",
        "role": "central",
        "purpose": "Carries the middle of the month at the same cadence",
    },
    {
        "key": "S0003",
        "title": "The closing captures",
        "weight": "glimpse",
        "role": "texture",
        "purpose": "Closes the month with one last pair",
    },
)


def _taken(value: str) -> str:
    """Normalize an Immich wire timestamp to the ISO form carrier rows carry."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()


def _carrier_seconds(asset: Mapping[str, Any]) -> float:
    if asset["type"] != "VIDEO":
        return _STILL_SECONDS
    return round(min(float(asset["duration"] or 0) / 1000.0, _MOTION_CAP_SECONDS), 2)


def _carrier_rows(assets: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One carrier per fake source, in capture order, weighed into three stories."""
    ordered = sorted(assets, key=lambda asset: (asset["fileCreatedAt"], asset["id"]))
    per_episode = max(1, -(-len(ordered) // len(_EPISODES)))
    rows = []
    for index, asset in enumerate(ordered):
        episode = _EPISODES[min(index // per_episode, len(_EPISODES) - 1)]
        is_video = asset["type"] == "VIDEO"
        seconds = _carrier_seconds(asset)
        rows.append(
            {
                "asset_id": asset["id"],
                "kind": "video" if is_video else "still",
                "seconds": seconds,
                "taken": _taken(asset["fileCreatedAt"]),
                "story_episode": episode["key"],
                "story_weight": episode["weight"],
                "story_role": episode["role"],
                "why": f"{episode['title']}: {'a moving' if is_video else 'a still'} "
                "test pattern, the only capture of its day",
                "standing": "remarkable" if is_video else "maybe",
                "start_time": 0.0,
                "end_time": seconds,
            }
        )
    return rows


def _duration_realization(*, requested: float, budget: float, content: float, slots: int) -> dict:
    """The production shape, computed from this fixture's own numbers."""
    shortfall = round(max(0.0, budget - content), 2)
    tolerance = round(max(0.0, budget * _ACCEPTED_SHORTFALL_FRACTION), 2)
    return {
        "requested_seconds": requested,
        "content_budget_seconds": budget,
        "selected_content_seconds": content,
        "shortfall_seconds": shortfall,
        "near_target_tolerance_seconds": tolerance,
        "accepted_shortfall_fraction": _ACCEPTED_SHORTFALL_FRACTION,
        "requested_picture_slots": slots,
        "status": "near_target" if shortfall <= tolerance else "editorial_shortfall",
    }


def _intent_report(content: float) -> dict:
    return {
        "status": "ok",
        "requested_seconds": content,
        "usable_seconds": content,
        "shortfall_seconds": 0.0,
        "reason": None,
        "coverage": {episode["key"]: 0 for episode in _EPISODES},
        "violations": [],
    }


def write_attempt_tree(root: Path, assets: Sequence[Mapping[str, Any]]) -> Path:
    """Write one complete, already finished attempt and return its directory.

    Mirrors what `EditorialAttempt` plus the structure record leave behind after
    a successful run, so the workspace a launch smoke inspects looks like a
    workspace a real run produced.
    """
    run_dir = Path(root) / "cache" / "editorial-runs" / ATTEMPT_KEY
    attempt_dir = run_dir / "attempts" / ATTEMPT_ID
    attempt_dir.mkdir(parents=True, exist_ok=True)

    carriers = _carrier_rows(assets)
    content = round(sum(row["seconds"] for row in carriers), 2)
    realization = _duration_realization(
        requested=content, budget=content, content=content, slots=len(carriers)
    )
    write_secret_file(
        attempt_dir / "status.private.json",
        json.dumps(
            {
                "schema": "editorial-attempt-v1",
                "attempt_id": ATTEMPT_ID,
                "status": "complete",
                "started_at": _FIXTURE_TIME,
                "updated_at": _FIXTURE_TIME,
                "finished_at": _FIXTURE_TIME,
                "stage": STAGES[-1],
                "request": {
                    "key": ATTEMPT_KEY,
                    "product": "monthly",
                    "target_seconds": content,
                    "audience": "family",
                    "hemisphere": "north",
                    "date_ranges": [["2024-06-01", "2024-06-30"]],
                    "requested_assets": [row["asset_id"] for row in carriers],
                    "include_live_photos": True,
                    "hdr_only": False,
                },
                "restart": "Run the same request; completed exact judgments remain reusable.",
                "outcome": "selected",
                "selected_carriers": len(carriers),
                "duration_realization": realization,
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    write_secret_file(
        attempt_dir / "plan.private.json",
        json.dumps(
            {
                "story": {"thesis": _THESIS, "episodes": list(_EPISODES)},
                "carriers": carriers,
                "content_seconds": content,
                "duration_realization": realization,
                "intent_report": _intent_report(content),
                "status": "selection-awaiting-owner-review-not-rendered",
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    write_secret_file(
        attempt_dir / "render-projection.private.json",
        json.dumps(
            {
                "format": "editorial-source-rendering-v1",
                "selected_ids": [row["asset_id"] for row in carriers],
                "adjustments": [],
                "allow_live_motion": True,
                "intervals": {
                    row["asset_id"]: [row["start_time"], row["end_time"]] for row in carriers
                },
            },
            indent=2,
        ),
    )
    write_secret_file(
        run_dir / "latest-attempt.private.json",
        json.dumps({"attempt_id": ATTEMPT_ID, "directory": str(attempt_dir)}),
    )
    return attempt_dir


def _candidates(sources: Sequence[Any], *, photo_seconds: float) -> tuple[Any, ...]:
    """Demand every requested source, timed from its own metadata, in capture order."""
    from immich_memories.analysis.smart_pipeline import ClipWithSegment
    from immich_memories.api.models import VideoClipInfo

    rows = []
    for source in sorted(
        sources,
        key=lambda item: (
            (item.asset if isinstance(item, VideoClipInfo) else item).file_created_at,
            (item.asset if isinstance(item, VideoClipInfo) else item).id,
        ),
    ):
        if isinstance(source, VideoClipInfo):
            clip = source
            seconds = round(min(float(source.duration_seconds or 0.0), _MOTION_CAP_SECONDS), 2)
        else:
            seconds = float(photo_seconds)
            clip = VideoClipInfo(
                asset=source,
                duration_seconds=seconds,
                width=source.width,
                height=source.height,
            )
        rows.append(
            ClipWithSegment(clip=clip, start_time=0.0, end_time=seconds, score=0.0, analyzed=False)
        )
    return tuple(rows)


class _FakeEditorialPipeline:
    """The pipeline surface Step 2 drives, with a scripted cut behind it."""

    def __init__(self, *, app_config: Any, context: Any, attempt_directory: Path) -> None:
        from immich_memories.analysis.progress import ProgressTracker

        self._app_config = app_config
        self._context = context
        self._attempt_directory = attempt_directory
        self.tracker = ProgressTracker()
        # Nothing was downloaded or looked at, and Step 2 says so out loud.
        self.last_deep_analysis_count = 0

    def run_editorial_source(
        self,
        sources: Sequence[Any],
        progress_callback: Any = None,
        *,
        include_live_photos: bool = True,
    ) -> tuple[list[Any], Any]:
        """Announce the real stages, then return the shape the real route returns.

        ``include_live_photos`` changes nothing here: no fake source carries a
        Live Photo companion, so there is no motion for the flag to admit.
        """
        from immich_memories.analysis.editorial_projection import EditorialStageReporter
        from immich_memories.operations.cancellation import (
            PipelineCancelled,
            cancellation_scope,
            check_cancelled,
        )

        report_stage = EditorialStageReporter(self.tracker, progress_callback)
        self.tracker.start()
        try:
            with cancellation_scope(report_stage.repeat):
                for label in STAGES:
                    report_stage(label)
                    time.sleep(_STAGE_SECONDS)
                    check_cancelled()
                candidates = _candidates(sources, photo_seconds=self._app_config.photos.duration)
                result = self._result(candidates)
                report_stage("Editorial selection complete", status="complete")
                with contextlib.suppress(Exception):
                    self.tracker.finish()
                return list(candidates), result
        except PipelineCancelled:
            with contextlib.suppress(PipelineCancelled):
                report_stage("Editorial selection cancelled", status="cancelled")
            raise

    def _result(self, candidates: tuple[Any, ...]) -> Any:
        from immich_memories.analysis.editorial_planner import EditorialSelection
        from immich_memories.analysis.selection_coverage import coverage_of
        from immich_memories.analysis.smart_pipeline import PipelineResult
        from immich_memories.api.models import AssetType

        selections = tuple(
            EditorialSelection(
                asset_id=row.clip.asset.id,
                start_time=row.start_time,
                end_time=row.end_time,
                render_mode="motion" if row.clip.asset.type == AssetType.VIDEO else "still",
            )
            for row in candidates
        )
        segments = {row.asset_id: (row.start_time, row.end_time) for row in selections}
        content = round(sum(end - start for start, end in segments.values()), 2)
        stats: dict[str, Any] = {
            "selection_route": "editorial-source",
            "source_evidence": "hermetic launch fixture; no model and no annotation store",
            "legacy_deep_analysis_count": 0,
            "total_analyzed": 0,
            "source_candidate_count": len(candidates),
            "selected_count": len(selections),
            "error_count": 0,
            "editorial_render_adjustments": [],
            "editorial_attempt_directory": str(self._attempt_directory),
            "elapsed_seconds": self.tracker.progress.elapsed_seconds,
        }
        stats.update(self._timing(selections, candidates, content=content))
        return PipelineResult(
            selected_clips=[row.clip for row in candidates],
            clip_segments=segments,
            errors=[],
            stats=stats,
            coverage=coverage_of(candidates),
            editorial_selections=selections,
        )

    def _timing(
        self, selections: tuple[Any, ...], candidates: tuple[Any, ...], *, content: float
    ) -> dict[str, Any]:
        """Certify the render budget exactly as the structure planner does."""
        from immich_memories.processing.editorial_timing import bind_editorial_timeline

        policy = self._context.render_timing
        if policy is None:
            return {
                "editorial_duration_realization": _duration_realization(
                    requested=self._context.target_seconds,
                    budget=content,
                    content=content,
                    slots=len(selections),
                )
            }
        carriers = [
            {"asset_id": row.asset_id, "seconds": row.end_time - row.start_time}
            for row in selections
        ]
        assets = {row.clip.asset.id: row.clip.asset for row in candidates}
        timeline = policy.resolve(carriers, assets)
        return {
            "editorial_render_timing": bind_editorial_timeline(
                policy, timeline, [row["asset_id"] for row in carriers]
            ),
            "editorial_duration_realization": _duration_realization(
                requested=policy.target_seconds,
                budget=timeline.content_budget,
                content=content,
                slots=len(selections),
            ),
        }


def install_fake_editorial_route(attempt_directory: Path) -> None:
    """Point the production pipeline builder at the scripted route.

    Replaces one seam, ``editorial_runtime.build_smart_pipeline``, which both
    the UI and the CLI import at call time.
    """
    import immich_memories.analysis.editorial_runtime as editorial_runtime

    def build_smart_pipeline(
        client: Any,
        analysis_cache: Any,
        thumbnail_cache: Any,
        config: Any = None,
        run_id: str | None = None,
        *,
        analysis_config: Any,
        app_config: Any,
        editorial_context: Any,
        dry_run: bool = False,
        triage: Any = None,
        editorial_ports: Any = None,
    ) -> _FakeEditorialPipeline:
        return _FakeEditorialPipeline(
            app_config=app_config,
            context=editorial_context,
            attempt_directory=attempt_directory,
        )

    # WHY: the two boundaries a hermetic launch has no way to provide. The real
    # builder opens an annotation store this library has never had prepared and
    # a text model provider that is not running, and it is the one seam both
    # entry points go through, so replacing it leaves every other production
    # module -- projection, review, timing, assembly -- running for real.
    editorial_runtime.build_smart_pipeline = build_smart_pipeline
