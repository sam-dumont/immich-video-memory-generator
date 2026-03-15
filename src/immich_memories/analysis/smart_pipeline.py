"""Smart pipeline for one-click video memory generation.

Orchestrates the 4-phase pipeline:
1. Clustering - Group similar clips by thumbnail
2. Filtering - Apply HDR/favorites filters, pre-select candidates
3. Analyzing - Download and analyze selected clips
4. Refining - Pick final clips and optimal segments
"""

from __future__ import annotations

import gc
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from immich_memories.analysis.pipeline_analysis import AnalysisMixin
from immich_memories.analysis.pipeline_preview import PreviewMixin
from immich_memories.analysis.pipeline_refinement import RefinementMixin
from immich_memories.analysis.pipeline_scaling import ScalingMixin
from immich_memories.analysis.progress import PipelinePhase, ProgressTracker
from immich_memories.tracking.run_database import RunDatabase

if TYPE_CHECKING:
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.cache.database import VideoAnalysisCache
    from immich_memories.cache.thumbnail_cache import ThumbnailCache

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
    # If output_resolution is set, min_resolution defaults to output/2 (4K→1080, 1080→720)
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


class SmartPipeline(AnalysisMixin, PreviewMixin, RefinementMixin, ScalingMixin):
    """Smart pipeline for one-click memory generation.

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
    ):
        """Initialize the pipeline.

        Args:
            client: Immich API client.
            analysis_cache: Cache for video analysis results.
            thumbnail_cache: Cache for thumbnails.
            config: Pipeline configuration.
            run_id: Optional run ID for job tracking and cancellation support.
        """
        self.client = client
        self.analysis_cache = analysis_cache
        self.thumbnail_cache = thumbnail_cache
        self.config = config or PipelineConfig()
        self.tracker = ProgressTracker(total_phases=4)
        self.run_id = run_id
        self._run_db: RunDatabase | None = None

    def _check_cancelled(self) -> None:
        """Check if job cancellation was requested and raise if so."""
        if not self.run_id:
            return
        if self._run_db is None:
            self._run_db = RunDatabase()
        if self._run_db.is_cancel_requested(self.run_id):
            logger.info(f"Job {self.run_id} cancelled by user request")
            raise JobCancelledException(f"Job {self.run_id} cancelled")

    def run(
        self,
        clips: list[VideoClipInfo],
        progress_callback: Callable[[dict], None] | None = None,
    ) -> PipelineResult:
        """Run the full pipeline.

        Args:
            clips: All available clips.
            progress_callback: Callback receiving status dict updates.

        Returns:
            Pipeline result with selected clips and segments.
        """
        if progress_callback:
            self.tracker.add_callback(
                lambda _: progress_callback(self.tracker.get_status_summary())
            )

        self.tracker.start()

        try:
            # Check for cancellation before starting
            self._check_cancelled()

            # Phase 1: Cluster by thumbnail
            deduplicated = self._phase_cluster(clips)
            gc.collect()  # Memory optimization: cleanup after phase
            self._check_cancelled()

            # Phase 2: Filter and pre-select
            candidates = self._phase_filter(deduplicated)
            gc.collect()  # Memory optimization: cleanup after phase
            self._check_cancelled()

            # Phase 3: Analyze selected clips
            analyzed = self._phase_analyze(candidates)
            gc.collect()  # Memory optimization: cleanup after phase
            self._check_cancelled()

            # Phase 4: Refine final selection
            result = self._phase_refine(analyzed)
            gc.collect()  # Memory optimization: final cleanup

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
        """Phase 1: Cluster clips by thumbnail similarity.

        Args:
            clips: All clips.

        Returns:
            Deduplicated clips (one per cluster).
        """
        from immich_memories.analysis.duplicates import deduplicate_by_thumbnails

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

        Args:
            non_favorites: Non-favorite clips to filter.
            all_favorites: Favorites (for compilation warning only).

        Returns:
            Filtered non-favorites.
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
        """Select non-favorites from weeks that have no favorites.

        Args:
            all_favorites: All favorite clips.
            filtered_non_favorites: Non-favorites after quality filtering.

        Returns:
            Best non-favorites from gap weeks (up to 3 per week).
        """
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
                key=lambda c: (c.width * c.height if c.width and c.height else 0, c.bitrate or 0),
                reverse=True,
            )
            gap_fillers.extend(week_clips[:3])

        return gap_fillers

    def _phase_filter(self, clips: list[VideoClipInfo]) -> list[VideoClipInfo]:
        """Phase 2: Prepare clips for analysis.

        Favorites-first pipeline: ALL favorites get analyzed, filters only
        apply to non-favorites, non-favorites kept for gap-filling.

        Args:
            clips: Deduplicated clips.

        Returns:
            Clips to analyze (ALL favorites + some non-favorites for gaps).
        """
        self.tracker.start_phase(PipelinePhase.FILTERING, 1)
        self.tracker.start_item("Preparing analysis candidates")

        from immich_memories.config import get_config

        config = get_config()
        min_duration = config.analysis.min_segment_duration

        # Filter too-short clips (applies to ALL clips)
        before_count = len(clips)
        clips = [c for c in clips if (c.duration_seconds or 0) >= min_duration]
        too_short_count = before_count - len(clips)
        if too_short_count > 0:
            logger.info(
                f"Duration filter: removed {too_short_count} clips shorter than "
                f"{min_duration:.1f}s minimum"
            )

        # Analyze-all mode: skip smart filtering, send all clips to analysis
        if self.config.analyze_all:
            self._available_non_favorites = []
            self.tracker.complete_item("filters")
            self.tracker.complete_phase()
            logger.info(f"Phase 2: Analyze-all mode — sending all {len(clips)} clips to analysis")
            return clips

        all_favorites = [c for c in clips if c.asset.is_favorite]
        all_non_favorites = [c for c in clips if not c.asset.is_favorite]
        logger.info(
            f"Initial split: {len(all_favorites)} favorites, {len(all_non_favorites)} non-favorites"
        )

        # Apply quality filters to non-favorites only
        filtered_non_favorites = self._apply_non_favorite_filters(all_non_favorites, all_favorites)

        # Select gap fillers from weeks without favorites
        gap_fillers = self._select_gap_fillers(all_favorites, filtered_non_favorites)

        # Build final list: ALL favorites + gap fillers
        selected: list[VideoClipInfo] = []
        selected_ids: set[str] = set()

        for clip in all_favorites:
            selected.append(clip)
            selected_ids.add(clip.asset.id)

        for clip in gap_fillers:
            if clip.asset.id not in selected_ids:
                selected.append(clip)
                selected_ids.add(clip.asset.id)

        self._available_non_favorites = [
            c for c in filtered_non_favorites if c.asset.id not in selected_ids
        ]

        self.tracker.complete_item("filters")
        self.tracker.complete_phase()

        logger.info(
            f"Phase 2: Analyzing {len(selected)} clips "
            f"(ALL {len(all_favorites)} favorites + {len(gap_fillers)} gap-fillers)"
        )

        return selected


# Re-export standalone functions from clip_selection for backwards compatibility
# smart_select_clips and analyze_clip_for_highlight are imported at the top of this module
