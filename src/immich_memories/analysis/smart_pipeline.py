"""Smart pipeline for one-click video memory generation.

Orchestrates the 4-phase pipeline:
1. Clustering - Group similar clips by thumbnail
2. Filtering - Apply HDR/favorites filters, pre-select candidates
3. Analyzing - Download and analyze selected clips
4. Refining - Pick final clips and optimal segments
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from immich_memories.analysis.clip_analyzer import ClipAnalyzer
from immich_memories.analysis.clip_refiner import ClipRefiner
from immich_memories.analysis.clip_scaler import ClipScaler
from immich_memories.analysis.preview_builder import PreviewBuilder
from immich_memories.analysis.progress import PipelinePhase, ProgressTracker
from immich_memories.tracking.run_database import RunDatabase

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.cache.database import VideoAnalysisCache
    from immich_memories.cache.thumbnail_cache import ThumbnailCache
    from immich_memories.config_loader import Config
    from immich_memories.config_models import AnalysisConfig

logger = logging.getLogger(__name__)


class JobCancelledException(Exception):
    """Raised when a job is cancelled by user request."""

    pass


@dataclass
class PipelineConfig:
    """Configuration for the smart pipeline."""

    # Selection settings
    target_clips: int = 120  # Target number of clips to select
    avg_clip_duration: float = 5.0  # Average clip duration in final video
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

    # Analysis depth: "fast" = metadata gap-fill, "thorough" = LLM gap-fill
    analysis_depth: str = "fast"


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
        self._run_db: RunDatabase | None = None
        self._analysis_config = analysis_config
        self._app_config = app_config

        # Wire composed services
        self.previewer = PreviewBuilder(
            client,
            cache_config=app_config.cache,
            analysis_config=analysis_config,
            content_analysis_config=app_config.content_analysis,
        )
        self.analyzer = ClipAnalyzer(
            self.config, client, analysis_cache, self.previewer, app_config=app_config
        )
        self.scaler = ClipScaler()
        self.refiner = ClipRefiner(self.config, self.scaler)

    def _check_cancelled(self) -> None:
        """Check if job cancellation was requested and raise if so."""
        if not self.run_id:
            return
        if self._run_db is None:
            self._run_db = RunDatabase(db_path=self._app_config.cache.database_path)
        if self._run_db.is_cancel_requested(self.run_id):
            logger.info(f"Job {self.run_id} cancelled by user request")
            raise JobCancelledException(f"Job {self.run_id} cancelled")

    def run(
        self,
        clips: list[VideoClipInfo],
        progress_callback: Callable[[dict], None] | None = None,
    ) -> PipelineResult:
        """Run the full pipeline."""
        if progress_callback:
            self.tracker.add_callback(
                lambda _: progress_callback(self.tracker.get_status_summary())
            )

        self.tracker.start()

        try:
            self._check_cancelled()

            # Phase 1: Cluster by thumbnail
            deduplicated = self._phase_cluster(clips)
            self._check_cancelled()

            # Phase 2: Filter and pre-select
            candidates = self._phase_filter(deduplicated)
            self._check_cancelled()

            # Phase 3: Analyze selected clips
            analyzed = self.analyzer.phase_analyze(candidates, self.tracker, self._check_cancelled)
            self._check_cancelled()

            # Phase 4: Refine final selection
            result = self.refiner.phase_refine(analyzed, self.tracker)

            self.tracker.finish()
            return result

        except JobCancelledException:
            logger.info("Pipeline cancelled by user")
            self.tracker.finish()
            raise
        except Exception as e:
            logger.error(f"Pipeline failed: {e}")
            self.tracker.finish()
            raise

    def _phase_cluster(self, clips: list[VideoClipInfo]) -> list[VideoClipInfo]:
        """Phase 1: Cluster clips by thumbnail similarity."""
        from immich_memories.analysis.thumbnail_clustering import deduplicate_by_thumbnails

        self.tracker.start_phase(PipelinePhase.CLUSTERING, len(clips))

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
            filtered = [c for c in filtered if max(c.width, c.height) >= min_res]
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

    def _phase_filter(self, clips: list[VideoClipInfo]) -> list[VideoClipInfo]:
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

        a_config = self._analysis_config
        min_duration = a_config.min_segment_duration

        # Filter too-short clips (applies to ALL clips)
        before_count = len(clips)
        clips = [c for c in clips if (c.duration_seconds or 0) >= min_duration]
        too_short_count = before_count - len(clips)
        if too_short_count > 0:
            logger.info(
                f"Duration filter: removed {too_short_count} clips shorter than "
                f"{min_duration:.1f}s minimum"
            )

        # Analyze-all mode: skip budget, send everything
        if self.config.analyze_all:
            self._available_non_favorites = []
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
                duration=c.duration_seconds or 5.0,
                is_favorite=c.asset.is_favorite,
                width=c.width,
                height=c.height,
                is_camera_original=c.is_camera_original,
            )
            for c in clips
        ]

        entries = self._apply_budget_quality_gate(entries)

        # Compute density budget
        target_seconds = self.config.target_clips * self.config.avg_clip_duration
        buckets = compute_density_budget(
            assets=entries,
            target_duration_seconds=target_seconds,
        )

        raw_budget = (target_seconds - 50) * 2.0
        log_budget_summary(buckets, raw_budget)

        # Collect selected asset IDs from budget
        selected_ids: set[str] = set()
        for bucket in buckets:
            selected_ids.update(bucket.favorite_ids)
            selected_ids.update(bucket.gap_fill_ids)

        # Build clip lists
        clip_map = {c.asset.id: c for c in clips}
        selected = [clip_map[aid] for aid in selected_ids if aid in clip_map]
        self._available_non_favorites = [c for c in clips if c.asset.id not in selected_ids]

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
        the resolution threshold (2/3 of output height, floor 540px).
        """
        before = len(entries)
        min_res = max(540, int(self.config.output_resolution * 0.66))
        filtered = [
            e
            for e in entries
            if e.is_favorite or (e.is_camera_original and max(e.width, e.height) >= min_res)
        ]
        removed = before - len(filtered)
        if removed > 0:
            logger.info(
                f"Quality gate: removed {removed} clips from density budget "
                f"(non-camera or below {min_res}px for {self.config.output_resolution}p output)"
            )
        return filtered
