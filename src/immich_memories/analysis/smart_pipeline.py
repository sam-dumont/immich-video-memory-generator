"""Smart pipeline for one-click video memory generation.

Orchestrates the 4-phase pipeline:
1. Clustering - Group similar clips by thumbnail
2. Filtering - Apply HDR/favorites filters, pre-select candidates
3. Analyzing - Download and analyze selected clips
4. Refining - Pick final clips and optimal segments
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.analysis import selection_trace as trace
from immich_memories.analysis.clip_analyzer import ClipAnalyzer
from immich_memories.analysis.clip_refiner import ClipRefiner
from immich_memories.analysis.clip_scaler import ClipScaler
from immich_memories.analysis.preview_builder import PreviewBuilder
from immich_memories.analysis.progress import PipelinePhase, ProgressTracker
from immich_memories.analysis.thumbnail_prefetch import ThumbnailPrefetcher
from immich_memories.config_presets import resolve_analysis_depth

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.cache.database import VideoAnalysisCache
    from immich_memories.cache.thumbnail_cache import ThumbnailCache
    from immich_memories.cache.video_cache import VideoDownloadCache
    from immich_memories.config_loader import Config
    from immich_memories.config_models import AnalysisConfig

logger = logging.getLogger(__name__)

AUTO_FULL_ANALYSIS_MAX_CACHE_MISSES = 60


def _cap_analysis_candidates(
    selected: list[VideoClipInfo], target_clips: int
) -> list[VideoClipInfo]:
    """Cap selected clips at 1.5x target to prevent over-analysis.

    Favorites are always preserved. Non-favorites are trimmed by resolution
    (highest resolution kept first).
    """
    max_candidates = int(target_clips * 1.5)
    if len(selected) <= max_candidates:
        return selected

    fav = [c for c in selected if c.asset.is_favorite]
    non_fav = [c for c in selected if not c.asset.is_favorite]
    non_fav.sort(
        key=lambda c: c.width * c.height if c.width and c.height else 0,
        reverse=True,
    )
    keep = max(0, max_candidates - len(fav))
    result = fav + non_fav[:keep] if keep > 0 else fav[:max_candidates]
    logger.info(f"Capped analysis candidates to {len(result)} (1.5x target {target_clips})")
    return result


@dataclass
class PipelineConfig:
    """Configuration for the smart pipeline."""

    # Selection settings
    target_clips: int = 120  # Target number of clips to select
    # Verify passes (#468): re-analyze shipped fallback-scored clips and
    # re-select, until nothing shipping is a guess or the budget is spent.
    max_refinement_passes: int = 10
    # The judge (#468/#463): a selected clip below the floor never ships,
    # and the chronological ending cannot be the one weak clip in the
    # timeline (weakest member AND below this share of the mean score).
    judge_floor_score: float = 0.30
    judge_boundary_ratio: float = 0.6
    avg_clip_duration: float = 5.0  # Average clip duration in final video
    target_duration_seconds: float | None = None  # Explicit strict content budget
    hdr_only: bool = False  # Only select HDR clips
    prioritize_favorites: bool = True  # Prioritize favorite clips
    max_non_favorite_ratio: float = 0.70  # Max ratio of non-favorites (0.70 = at most 70%)

    # Resolution filtering - exclude small videos that would look bad upscaled
    # Set to 0 to disable, or specify minimum resolution
    # If output_resolution is set, min_resolution defaults to output/2 (4K->1080, 1080->720)
    min_resolution: int = 0  # 0 = auto based on output, or explicit minimum
    output_resolution: int = 2160  # Output resolution (2160=4K, 1080=HD) for auto min calc

    # Analysis settings
    analyze_all: bool = False  # Analyze all clips (slow but better selection)
    segment_duration: float = 3.0  # Duration for segment sampling

    # Duplicate detection
    # Higher threshold = more lenient clustering (catches different framings of same scene)
    # 6 = very strict (only near-identical), 8-10 = strict, 12-16 = moderate, 20+ = aggressive
    cluster_threshold: int = 10  # Hamming distance threshold - balanced

    # Temporal deduplication - when multiple favorites are within this time window,
    # keep only the best-scored one (they're likely the same moment)
    temporal_dedup_window_minutes: float = 5.0  # Time window in minutes (0 to disable)

    # Birthday boost - if set, clips from this month get extra priority
    # Used to ensure birthday week is well represented
    birthday_month: int | None = None  # 1-12 for month, None to disable

    # Trip segment distribution - if set, clips are distributed proportionally
    # across overnight stop segments instead of purely by date
    overnight_bases: list | None = None  # list[OvernightBase] from trip detection

    # Photo ratio cap — max fraction of selected clips that can be photos
    photo_max_ratio: float = 0.50  # 0.50 = at most 50% photos

    # Analysis depth: auto budgets misses, fast favors speed, thorough analyzes all.
    analysis_depth: str = "auto"

    @property
    def duration_target(self) -> float:
        """Return explicit seconds when final planning supplied them."""
        if self.target_duration_seconds is not None:
            return self.target_duration_seconds
        return self.target_clips * self.avg_clip_duration


@dataclass
class PipelineResult:
    """Result of the smart pipeline."""

    selected_clips: list[VideoClipInfo]
    clip_segments: dict[str, tuple[float, float]]  # asset_id -> (start, end)
    errors: list[dict]  # List of {clip_id, error}
    stats: dict = field(default_factory=dict)


@dataclass
class ClipWithSegment:
    """A clip with its optimal segment."""

    clip: VideoClipInfo
    start_time: float
    end_time: float
    score: float
    # WHY: the verify pass (#468) must tell real analysis from a metadata
    # guess — a fallback score is a placeholder, not a rank.
    analyzed: bool = True


class SmartPipeline:
    """Smart pipeline for one-click memory generation.

    Composes 4 services via constructor injection:
    - ClipAnalyzer: downloads, analyzes, and scores clips
    - PreviewBuilder: extracts preview segments
    - ClipRefiner: selects and distributes final clips
    - ClipScaler: scales to target duration and deduplicates

    Runs 4 phases:
    1. Cluster thumbnails to detect duplicates
    2. Filter and pre-select candidate clips
    3. Analyze selected clips (download + score)
    4. Refine final selection with optimal segments
    """

    def __init__(
        self,
        client: SyncImmichClient,
        analysis_cache: VideoAnalysisCache,
        thumbnail_cache: ThumbnailCache,
        config: PipelineConfig | None = None,
        run_id: str | None = None,
        *,
        analysis_config: AnalysisConfig,
        app_config: Config,
    ):
        self.client = client
        self.analysis_cache = analysis_cache
        self.thumbnail_cache = thumbnail_cache
        self.config = config or PipelineConfig()
        self.tracker = ProgressTracker(total_phases=4)
        self.run_id = run_id
        self._analysis_config = analysis_config
        self._app_config = app_config
        from immich_memories.analysis.provider_health import ProviderCircuit

        self.provider_circuit = ProviderCircuit()
        self.last_deep_analysis_count = 0
        self._video_cache: VideoDownloadCache | None = None
        cache_config = app_config.cache
        if cache_config.video_cache_enabled and isinstance(cache_config.video_cache_path, Path):
            from immich_memories.cache.video_cache import VideoDownloadCache

            self._video_cache = VideoDownloadCache(
                cache_dir=cache_config.video_cache_path,
                max_size_gb=cache_config.video_cache_max_size_gb,
                max_age_days=cache_config.video_cache_max_age_days,
            )

        # Wire composed services
        self.previewer = PreviewBuilder(
            client,
            cache_config=app_config.cache,
            analysis_config=analysis_config,
            content_analysis_config=app_config.content_analysis,
            video_cache=self._video_cache,
            hardware_enabled=app_config.hardware.enabled,
        )
        self.analyzer = ClipAnalyzer(
            self.config,
            client,
            analysis_cache,
            self.previewer,
            app_config=app_config,
            video_cache=self._video_cache,
            provider_circuit=self.provider_circuit,
        )
        self.scaler = ClipScaler()
        self.refiner = ClipRefiner(self.config, self.scaler)
        self.thumbnail_prefetcher = ThumbnailPrefetcher.from_client(
            client,
            thumbnail_cache,
            api_policy=app_config.immich.api_version,
            max_workers=analysis_config.download_workers,
        )

    def run(
        self,
        clips: list[VideoClipInfo],
        progress_callback: Callable[[dict], None] | None = None,
    ) -> PipelineResult:
        """Run the full pipeline (phases 1-4)."""
        analyzed = self.run_analysis(clips, progress_callback)
        result = self.run_selection(analyzed)
        return result

    def run_analysis(
        self,
        clips: list[VideoClipInfo],
        progress_callback: Callable[[dict], None] | None = None,
    ) -> list[ClipWithSegment]:
        """Run phases 1-3 (cluster, filter, analyze). Returns analyzed clips.

        Does NOT call tracker.finish() — the caller (run() or external code)
        is responsible for finishing the tracker after run_selection().
        """
        if progress_callback:
            self.tracker.add_callback(
                lambda _: progress_callback(self.tracker.get_status_summary())
            )

        self.tracker.start()

        try:
            # Phase 1: Cluster by thumbnail
            deduplicated = self._phase_cluster(clips)

            # Phase 2: hard eligibility + a cost-bounded analysis shortlist.
            eligible = self._hard_eligible_clips(deduplicated)
            candidates = self._analysis_candidates(eligible)
            self.last_deep_analysis_count = len(candidates)

            # Phase 3: one cache batch covers every candidate download.
            analyzed = self._analyze_with_cache_batch(candidates)
            candidate_ids = {clip.asset.id for clip in candidates}
            leftovers = [clip for clip in eligible if clip.asset.id not in candidate_ids]
            fallbacks = self.analyzer.plan_cached_or_metadata(leftovers)

            return [*analyzed, *fallbacks]

        except (
            Exception
        ) as e:  # WHY: top-level pipeline boundary — logs + cleans up tracker before re-raise
            logger.error(f"Pipeline failed: {e}")
            raise
        finally:
            # Analysis owns native captures/models regardless of cache mode or failure.
            with contextlib.suppress(Exception):
                self.analyzer.close()
            with contextlib.suppress(Exception):
                self.previewer.close()

    def _analysis_candidates(self, eligible: list[VideoClipInfo]) -> list[VideoClipInfo]:
        """Resolve user-facing analysis depth into the concrete candidate set."""
        requested_depth = resolve_analysis_depth(
            self.config.analysis_depth, self._app_config.preset
        )
        # The analyzer reads the same PipelineConfig, so the resolved depth has to land there.
        self.config.analysis_depth = requested_depth
        if requested_depth == "thorough":
            logger.info("Thorough mode: analyzing all %d eligible clips", len(eligible))
            self._complete_passthrough_filter("Thorough", len(eligible))
            return eligible

        if requested_depth == "auto":
            cache_misses = self._semantic_cache_miss_count(eligible)
            self.config.analysis_depth = "thorough"
            if cache_misses <= AUTO_FULL_ANALYSIS_MAX_CACHE_MISSES:
                logger.info(
                    "Auto mode: %d eligible clips, %d current-model cache misses; "
                    "analyzing every eligible clip",
                    len(eligible),
                    cache_misses,
                )
                self._complete_passthrough_filter("Auto", len(eligible))
                return eligible
            logger.info(
                "Auto mode: %d current-model cache misses exceeds %d; "
                "using density shortlist with LLM analysis",
                cache_misses,
                AUTO_FULL_ANALYSIS_MAX_CACHE_MISSES,
            )

        return self._phase_filter(eligible, hard_filtered=True)

    def _complete_passthrough_filter(self, mode: str, candidate_count: int) -> None:
        """Keep four-phase progress truthful when a mode deliberately skips shortlisting."""
        self.tracker.start_phase(PipelinePhase.FILTERING, 1)
        self.tracker.start_item(f"{mode} mode: keeping all {candidate_count} eligible clips")
        self.tracker.complete_item("filters")
        self.tracker.complete_phase()

    def _stabilize_selection(
        self,
        analyzed: list[ClipWithSegment],
        result: PipelineResult,
    ) -> tuple[PipelineResult, list[ClipWithSegment]]:
        """Verify and judge until the selection is stable (#468).

        Found live on the 2026-08-21 demo: a judge (or review) drop
        re-selects, the re-selection admits a NEW fallback-scored clip, and
        a straight verify→judge sequence ships it unverified. The stages
        iterate together until a pass changes nothing or the budget is spent.
        """
        for _ in range(max(1, self.config.max_refinement_passes)):
            result, analyzed = self._verify_selection(analyzed, result)
            result, analyzed, dropped = self._judge_selection(analyzed, result)
            if not dropped:
                break
        return result, analyzed

    def _verify_selection(
        self,
        analyzed: list[ClipWithSegment],
        result: PipelineResult,
    ) -> tuple[PipelineResult, list[ClipWithSegment]]:
        """Re-analyze any shipped fallback-scored clip and re-select (#468).

        Heavy when cold, cheap when warm: every verified clip lands in the
        analysis cache, so later runs start from real scores. A clip whose
        analysis fails keeps its fallback score but stops being re-queued,
        so the loop always terminates.
        """
        by_id = {c.clip.asset.id: c for c in analyzed}
        attempted: set[str] = set()
        for _ in range(max(1, self.config.max_refinement_passes)):
            unverified = [
                by_id[c.asset.id]
                for c in result.selected_clips
                if c.asset.id in by_id
                and c.asset.id not in attempted
                and self._needs_a_real_look(by_id[c.asset.id])
            ]
            if not unverified:
                break
            logger.info(
                "Verify pass: analyzing %d selected clip(s) the review cannot see",
                len(unverified),
            )
            trace.record(
                "verify: analyze unseen",
                result.selected_clips,
                result.selected_clips,
                [f"{len(unverified)} clip(s) analyzed for real before judging"],
            )
            attempted.update(u.clip.asset.id for u in unverified)
            try:
                verified = self.analyzer.phase_analyze([u.clip for u in unverified], self.tracker)
            finally:
                with contextlib.suppress(Exception):
                    self.analyzer.close()
            # WHY only what we asked for: analysis can hand back more than it
            # was given (a Live Photo expands into its components), and any
            # extra id lands straight back in the pool — resurrecting a clip
            # the judge or the review had just dropped.
            requested = {u.clip.asset.id for u in unverified}
            verified = [v for v in verified if v.clip.asset.id in requested]
            verified_ids = {v.clip.asset.id for v in verified}
            for v in verified:
                by_id[v.clip.asset.id] = v
            for u in unverified:
                if u.clip.asset.id not in verified_ids:
                    by_id[u.clip.asset.id] = ClipWithSegment(
                        clip=u.clip,
                        start_time=u.start_time,
                        end_time=u.end_time,
                        score=u.score,
                        analyzed=True,
                    )
            result = self.refiner.phase_refine(list(by_id.values()), self.tracker)
        return result, list(by_id.values())

    def _final_review_drop(
        self,
        analyzed: list[ClipWithSegment],
        result: PipelineResult,
    ) -> tuple[PipelineResult, list[ClipWithSegment]]:
        """One last review that drops without refilling.

        The iterating review always leaves its own last refill unjudged: it
        stops when the budget runs out, and by then it has just re-selected.
        Refilling again would only admit more unseen clips, so this pass takes
        the cut it has and removes what does not belong.
        """
        if not self._app_config.content_analysis.enabled:
            return result, analyzed
        from immich_memories.analysis.selection_review import review_selection

        by_id = {c.clip.asset.id: c for c in analyzed}
        selected = [by_id[c.asset.id] for c in result.selected_clips if c.asset.id in by_id]
        drops = set(review_selection(selected, self._app_config.llm))
        if not drops:
            return result, analyzed

        kept = [c for c in result.selected_clips if c.asset.id not in drops]
        if not kept:
            return result, analyzed
        logger.info(
            "Selection review: budget spent, dropping %d unreviewed clip(s) "
            "rather than shipping them",
            len(result.selected_clips) - len(kept),
        )
        trimmed = replace(
            result,
            selected_clips=kept,
            clip_segments={
                asset_id: seg
                for asset_id, seg in result.clip_segments.items()
                if asset_id not in drops
            },
        )
        return trimmed, [c for c in analyzed if c.clip.asset.id not in drops]

    def _needs_a_real_look(self, member: ClipWithSegment) -> bool:
        """Would the LLM review be judging this clip blind?

        A metadata guess for a score is one way to ship unseen. The other is
        subtler and was shipping: a clip carries a real visual score, so it
        counts as analyzed, but no content analysis ever ran on it, so the
        review is handed a bare line. It is then told — correctly — never to
        drop a clip for missing information, and two near-identical hotel
        mirror selfies from consecutive days both survive as a result.
        """
        if not member.analyzed:
            return True
        if not self._app_config.content_analysis.enabled:
            return False
        return not getattr(member.clip, "llm_description", None)

    def _judge_selection(
        self,
        analyzed: list[ClipWithSegment],
        result: PipelineResult,
    ) -> tuple[PipelineResult, list[ClipWithSegment], bool]:
        """One judge sweep (#468): drop offenders, let selection refill.

        A single sweep by design — the caller re-verifies whatever the
        re-selection admitted before judging again.
        """
        by_id = {c.clip.asset.id: c for c in analyzed}
        selected = [by_id[c.asset.id] for c in result.selected_clips if c.asset.id in by_id]
        if len(selected) < 2:
            return result, analyzed, False
        offenders = self._judge_offenders(selected)
        if not offenders:
            return result, analyzed, False
        logger.info(
            "Judge: dropping %d clip(s) below the quality gate, re-selecting",
            len(offenders),
        )
        analyzed = [c for c in analyzed if c.clip.asset.id not in offenders]
        if not analyzed:
            return result, analyzed, False
        result = self.refiner.phase_refine(analyzed, self.tracker)
        return result, analyzed, True

    def _judge_offenders(self, selected: list[ClipWithSegment]) -> set[str]:
        """Members failing the gate. Favorites are exempt from both rules —
        the user explicitly chose them, and "Starting with ALL favorites" is
        the selection's oldest contract."""
        judgeable = [s for s in selected if not getattr(s.clip.asset, "is_favorite", False)]
        offenders = {s.clip.asset.id for s in judgeable if s.score < self.config.judge_floor_score}
        scores = [s.score for s in selected]
        mean_score = sum(scores) / len(scores)
        ending = max(
            selected,
            key=lambda s: s.clip.asset.file_created_at or datetime.min.replace(tzinfo=UTC),
        )
        if (
            len(selected) > 2
            and not getattr(ending.clip.asset, "is_favorite", False)
            and ending.score == min(scores)
            and ending.score < mean_score * self.config.judge_boundary_ratio
        ):
            offenders.add(ending.clip.asset.id)
        return offenders

    def _holistic_review(
        self,
        analyzed: list[ClipWithSegment],
        result: PipelineResult,
    ) -> tuple[PipelineResult, list[ClipWithSegment], bool]:
        """One LLM pass over the finished cut (#468): redundancy and feel.

        The mechanical judge sees scores; only something reading the
        descriptions can see the same birthday candles twice. Optional by
        construction — no LLM, no drops, selection unchanged.
        """
        if not self._app_config.content_analysis.enabled:
            return result, analyzed, False
        from immich_memories.analysis.selection_review import review_selection

        by_id = {c.clip.asset.id: c for c in analyzed}
        selected = [by_id[c.asset.id] for c in result.selected_clips if c.asset.id in by_id]
        drops = review_selection(selected, self._app_config.llm)
        if not drops:
            trace.record("llm review", selected, selected)
            return result, analyzed, False
        dropped = set(drops)
        trace.record(
            "llm review",
            selected,
            [c for c in selected if c.clip.asset.id not in dropped],
        )
        remaining = [c for c in analyzed if c.clip.asset.id not in dropped]
        if not remaining:
            return result, analyzed, False
        # WHY the pool shrinks too: a later stabilization re-refines from the
        # pool — returning the old one would resurrect the LLM's drops.
        return self.refiner.phase_refine(remaining, self.tracker), remaining, True

    def _semantic_cache_miss_count(self, clips: list[VideoClipInfo]) -> int:
        """Count clips that need work under the exact active semantic model."""
        from immich_memories.analysis.cache_projection import is_compatible_analysis_cache

        return sum(
            not is_compatible_analysis_cache(
                self.analysis_cache.get_analysis(clip.asset.id), self._app_config
            )
            for clip in clips
        )

    def run_planning_analysis(
        self,
        clips: list[VideoClipInfo],
        progress_callback: Callable[[dict], None] | None = None,
    ) -> list[ClipWithSegment]:
        """Run normal metadata filters with cached-only segment analysis."""
        if progress_callback:
            self.tracker.add_callback(
                lambda _: progress_callback(self.tracker.get_status_summary())
            )
        self.tracker.start()
        try:
            deduplicated = self._phase_cluster(clips)
            eligible = self._hard_eligible_clips(deduplicated)
            candidates = self._phase_filter(eligible, hard_filtered=True)
            self.last_deep_analysis_count = len(candidates)
            planned = self.analyzer.phase_plan_cached(candidates, self.tracker)
            candidate_ids = {clip.asset.id for clip in candidates}
            leftovers = [clip for clip in eligible if clip.asset.id not in candidate_ids]
            return [*planned, *self.analyzer.plan_cached_or_metadata(leftovers)]
        finally:
            with contextlib.suppress(Exception):
                self.analyzer.close()
            with contextlib.suppress(Exception):
                self.previewer.close()

    def _analyze_with_cache_batch(self, candidates: list[VideoClipInfo]) -> list[ClipWithSegment]:
        """Run analysis with one shared cache manifest when file caching is enabled."""
        if self._video_cache is None:
            return self.analyzer.phase_analyze(candidates, self.tracker)

        with self._video_cache.begin_batch() as batch:
            self.analyzer.bind_cache_batch(batch)
            self.previewer.bind_cache_batch(batch)
            try:
                return self.analyzer.phase_analyze(candidates, self.tracker)
            finally:
                self.analyzer.bind_cache_batch(None)
                self.previewer.bind_cache_batch(None)

    def run_selection(
        self,
        analyzed: list[ClipWithSegment],
        progress_callback: Callable[[dict], None] | None = None,
        *,
        verify: bool = True,
    ) -> PipelineResult:
        """Run phase 4 (refine) on pre-analyzed clips. Finishes the tracker.

        With ``verify`` (the default), selection runs the #468 verify loop:
        any selected clip whose score is a metadata guess is analyzed for
        real and selection re-runs, so nothing ships unseen. ``verify=False``
        is for planning/dry-run paths that must stay local.
        """
        if progress_callback:
            self.tracker.add_callback(
                lambda _: progress_callback(self.tracker.get_status_summary())
            )

        # WHY an environment variable rather than an argument: every caller of
        # this method — CLI, UI, scripts — would otherwise need a parameter it
        # does not use, to carry a debugging concern four layers down.
        trace_path = os.environ.get("IMMICH_MEMORIES_SELECTION_TRACE")
        with trace.tracing(Path(trace_path) if trace_path else None):
            return self._run_selection(analyzed, verify=verify)

    def _run_selection(
        self,
        analyzed: list[ClipWithSegment],
        *,
        verify: bool,
    ) -> PipelineResult:
        try:
            result = self.refiner.phase_refine(analyzed, self.tracker)
            if verify:
                result, analyzed = self._stabilize_selection(analyzed, result)
                # WHY a loop: one review pass drops what it can see, and the
                # refill puts new clips in their place that nothing has looked
                # at yet. Reviewing once leaves whatever the last refill
                # admitted unjudged, which is how a product shot survived a
                # cut that had already dropped two others.
                review_changed = True
                for _ in range(max(1, self.config.max_refinement_passes)):
                    result, analyzed, review_changed = self._holistic_review(analyzed, result)
                    if not review_changed:
                        break
                    # The review's re-selection can admit new fallback clips,
                    # exactly like a judge drop — same stabilization.
                    result, analyzed = self._stabilize_selection(analyzed, result)
                if review_changed:
                    # Every pass so far dropped something and refilled, so the
                    # last refill has never been looked at — which is how a
                    # photo of a games console ended a December cut. Judge it
                    # once more and simply drop what fails: a cut four seconds
                    # short beats a cut that ends on a shelf.
                    result, analyzed = self._final_review_drop(analyzed, result)
            self.tracker.finish()
            return result
        except (
            Exception
        ) as e:  # WHY: top-level pipeline boundary — logs + cleans up tracker before re-raise
            logger.error(f"Selection failed: {e}")
            self.tracker.finish()
            raise

    def _phase_cluster(self, clips: list[VideoClipInfo]) -> list[VideoClipInfo]:
        """Phase 1: Cluster clips by thumbnail similarity."""
        from immich_memories.analysis.thumbnail_clustering import deduplicate_by_thumbnails

        self.tracker.start_phase(PipelinePhase.CLUSTERING, len(clips))
        self.thumbnail_prefetcher.ensure_cached(clips)

        def progress(current: int, total: int) -> None:
            if current <= len(clips) and current > 0:
                clip = clips[current - 1]
                self.tracker.start_item(clip.asset.original_file_name or clip.asset.id[:8])
                self.tracker.complete_item(clip.asset.id)

        deduplicated = deduplicate_by_thumbnails(
            clips=clips,
            thumbnail_cache=self.thumbnail_cache,
            threshold=self.config.cluster_threshold,
            progress_callback=progress,
            duplicate_hash_threshold=self._analysis_config.duplicate_hash_threshold,
        )

        self.tracker.complete_phase()

        duplicates_removed = len(clips) - len(deduplicated)
        logger.info(
            f"Phase 1: Clustered {len(clips)} -> {len(deduplicated)} clips "
            f"({duplicates_removed} duplicates)"
        )

        return deduplicated

    def _apply_non_favorite_filters(
        self,
        non_favorites: list[VideoClipInfo],
        all_favorites: list[VideoClipInfo],
    ) -> list[VideoClipInfo]:
        """Apply quality filters to non-favorites only.

        Applies HDR, compilation, and resolution filters sequentially.
        """
        filtered = non_favorites

        # HDR filter
        if self.config.hdr_only:
            before = len(filtered)
            filtered = [c for c in filtered if c.is_hdr]
            logger.info(f"HDR filter on non-favorites: {before} -> {len(filtered)}")

        # Compilation filter
        before = len(filtered)
        filtered = [c for c in filtered if c.is_camera_original]
        compilations_removed = before - len(filtered)
        if compilations_removed > 0:
            logger.info(
                f"Compilation filter: removed {compilations_removed} non-camera videos "
                f"(no make/model EXIF)"
            )

        compilation_favorites = [c for c in all_favorites if not c.is_camera_original]
        if compilation_favorites:
            logger.warning(
                f"Note: {len(compilation_favorites)} favorites appear to be compilations "
                f"(no camera EXIF) - keeping them anyway"
            )

        # Resolution filter
        min_res = self.config.min_resolution
        if min_res == 0 and self.config.output_resolution > 0:
            min_res = self.config.output_resolution // 2
            min_res = max(min_res, 480)

        if min_res > 0:
            before = len(filtered)
            # WHY: 0x0 = unknown resolution (live photo video components) — let them through
            filtered = [
                c
                for c in filtered
                if max(c.width, c.height) >= min_res or max(c.width, c.height) == 0
            ]
            logger.info(
                f"Resolution filter on non-favorites: {before} -> {len(filtered)} "
                f"(min {min_res}px for {self.config.output_resolution}p output)"
            )

        return filtered

    def _select_gap_fillers(
        self,
        all_favorites: list[VideoClipInfo],
        filtered_non_favorites: list[VideoClipInfo],
    ) -> list[VideoClipInfo]:
        """Select non-favorites from weeks that have no favorites."""
        from collections import defaultdict

        weeks_with_favorites: set[str] = set()
        non_favorites_by_week: dict[str, list] = defaultdict(list)

        weeks_with_favorites.update(
            clip.asset.file_created_at.strftime("%Y-W%W") for clip in all_favorites
        )

        for clip in filtered_non_favorites:
            non_favorites_by_week[clip.asset.file_created_at.strftime("%Y-W%W")].append(clip)

        weeks_needing_fill = set(non_favorites_by_week.keys()) - weeks_with_favorites

        gap_fillers: list[VideoClipInfo] = []
        for week in sorted(weeks_needing_fill):
            week_clips = non_favorites_by_week[week]
            week_clips.sort(
                key=lambda c: (
                    c.width * c.height if c.width and c.height else 0,
                    c.bitrate or 0,
                ),
                reverse=True,
            )
            gap_fillers.extend(week_clips[:3])

        return gap_fillers

    def _adapt_target_for_content(self, clips: list[VideoClipInfo]) -> None:
        """Reduce target_clips when available content is sparse."""
        unique_count = len(clips)
        if unique_count < self.config.target_clips * 0.5:
            original = self.config.target_clips
            self.config.target_clips = max(unique_count, 5)
            logger.info(
                f"Sparse content: adapted target {original} -> {self.config.target_clips} "
                f"({unique_count} clips available)"
            )

    def _hard_eligible_clips(self, clips: list[VideoClipInfo]) -> list[VideoClipInfo]:
        """Apply only rules that must permanently exclude a source clip."""
        min_duration = self._analysis_config.min_segment_duration
        eligible = [c for c in clips if (c.duration_seconds or 0) >= min_duration]
        too_short_count = len(clips) - len(eligible)
        if too_short_count > 0:
            logger.info(
                f"Duration filter: removed {too_short_count} clips shorter than "
                f"{min_duration:.1f}s minimum"
            )

        if self.config.hdr_only:
            before = len(eligible)
            eligible = [clip for clip in eligible if clip.is_hdr]
            logger.info("HDR eligibility filter: %d -> %d clips", before, len(eligible))
        return eligible

    def _phase_filter(
        self,
        clips: list[VideoClipInfo],
        *,
        hard_filtered: bool = False,
    ) -> list[VideoClipInfo]:
        """Phase 2: Select clips for analysis using density-proportional budget.

        Uses density budget to distribute raw footage quotas across time
        buckets. Favorites fill first, gap-fillers fill remaining quotas.
        Analyze-all mode bypasses the budget entirely.
        """
        from immich_memories.analysis.density_budget import (
            AssetEntry,
            compute_density_budget,
            log_budget_summary,
        )

        self.tracker.start_phase(PipelinePhase.FILTERING, 1)
        self.tracker.start_item("Computing density budget")

        if not hard_filtered:
            clips = self._hard_eligible_clips(clips)

        self._adapt_target_for_content(clips)

        # Analyze-all mode: skip budget, send everything
        if self.config.analyze_all:
            self.tracker.complete_item("filters")
            self.tracker.complete_phase()
            logger.info(f"Phase 2: Analyze-all mode — sending all {len(clips)} clips to analysis")
            return clips

        # Build asset entries for density budget
        entries = [
            AssetEntry(
                asset_id=c.asset.id,
                asset_type="video",
                date=c.asset.file_created_at,
                duration=min(
                    c.duration_seconds or self.config.avg_clip_duration,
                    self.config.avg_clip_duration,
                ),
                is_favorite=c.asset.is_favorite,
                score=c.quality_score,
                width=c.width,
                height=c.height,
                is_camera_original=c.is_camera_original,
            )
            for c in clips
        ]

        entries = self._apply_budget_quality_gate(entries)

        # Compute density budget
        target_seconds = self.config.duration_target
        buckets = compute_density_budget(
            assets=entries,
            target_duration_seconds=target_seconds,
            raw_multiplier=1.3,
        )

        effective_raw_budget = sum(bucket.quota_seconds for bucket in buckets)
        log_budget_summary(buckets, effective_raw_budget)

        # Collect selected asset IDs from budget
        selected_ids: set[str] = set()
        for bucket in buckets:
            selected_ids.update(bucket.favorite_ids)
            selected_ids.update(bucket.gap_fill_ids)

        # Build clip lists
        clip_map = {c.asset.id: c for c in clips}
        selected = [clip_map[aid] for aid in selected_ids if aid in clip_map]
        selected = _cap_analysis_candidates(selected, self.config.target_clips)

        fav_count = sum(1 for c in selected if c.asset.is_favorite)
        gap_count = len(selected) - fav_count

        self.tracker.complete_item("filters")
        self.tracker.complete_phase()

        logger.info(
            f"Phase 2: Density budget selected {len(selected)} clips "
            f"({fav_count} favorites + {gap_count} gap-fillers)"
        )

        return selected

    def _apply_budget_quality_gate(self, entries: list) -> list:
        """Filter non-camera and low-res clips from density budget entries.

        Favorites always pass. Non-favorites must have camera EXIF and meet
        the resolution threshold (1/2 of output height, floor 540px).
        Clips with unknown resolution (0x0, common for live photo video
        components) pass the resolution check — their parent photo is hi-res.
        """
        before = len(entries)
        min_res = max(540, int(self.config.output_resolution * 0.50))
        filtered = [
            e
            for e in entries
            if e.is_favorite
            or (
                e.is_camera_original
                # WHY: 0x0 = unknown resolution (live photo video components).
                # These come from real camera shots — don't filter them.
                and (max(e.width, e.height) >= min_res or max(e.width, e.height) == 0)
            )
        ]
        removed = before - len(filtered)
        if removed > 0:
            logger.info(
                f"Quality gate: removed {removed} clips from density budget "
                f"(non-camera or below {min_res}px for {self.config.output_resolution}p output)"
            )
        return filtered
