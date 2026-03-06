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
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.analysis.progress import PipelinePhase, ProgressTracker
from immich_memories.processing.downscaler import cleanup_downscaled
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


def _get_fast_encoder_args() -> list[str]:
    """Get fast encoder arguments with GPU acceleration when available.

    Returns encoder optimized for speed (preview temp files).
    """
    import subprocess
    import sys

    # macOS: Use VideoToolbox hardware encoder
    if sys.platform == "darwin":
        return [
            "-c:v", "h264_videotoolbox",
            "-q:v", "65",  # Lower quality OK for previews (faster)
        ]

    # Other platforms: Check for available encoders
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        encoders = result.stdout

        # Try NVIDIA NVENC (GPU accelerated)
        if "h264_nvenc" in encoders:
            return [
                "-c:v", "h264_nvenc",
                "-preset", "p1",  # Fastest preset
                "-rc", "constqp", "-qp", "23",
            ]

        # Try VAAPI (Linux GPU)
        if "h264_vaapi" in encoders:
            return [
                "-c:v", "h264_vaapi",
                "-qp", "23",
            ]

        # Try Intel QSV
        if "h264_qsv" in encoders:
            return [
                "-c:v", "h264_qsv",
                "-preset", "veryfast",
            ]
    except Exception:
        pass

    # Fallback to CPU libx264
    return [
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
    ]


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

    def _phase_filter(self, clips: list[VideoClipInfo]) -> list[VideoClipInfo]:
        """Phase 2: Prepare clips for analysis.

        NEW APPROACH: Favorites-first pipeline
        - ALL favorites get analyzed (never filtered out!)
        - HDR/resolution filters only apply to NON-favorites
        - Non-favorites are kept as backup for gap-filling

        Args:
            clips: Deduplicated clips.

        Returns:
            Clips to analyze (ALL favorites + some non-favorites for gaps).
        """
        self.tracker.start_phase(PipelinePhase.FILTERING, 1)
        self.tracker.start_item("Preparing analysis candidates")

        # Get minimum duration from config
        from immich_memories.config import get_config
        config = get_config()
        min_duration = config.analysis.min_segment_duration

        # Filter out clips shorter than minimum duration FIRST
        # This applies to ALL clips - a clip too short is useless regardless of favorite status
        before_count = len(clips)
        clips = [c for c in clips if (c.duration_seconds or 0) >= min_duration]
        too_short_count = before_count - len(clips)
        if too_short_count > 0:
            logger.info(
                f"Duration filter: removed {too_short_count} clips shorter than "
                f"{min_duration:.1f}s minimum"
            )

        # CRITICAL: Separate favorites FIRST before any filtering!
        # Favorites are NEVER filtered out - they always get analyzed
        all_favorites = [c for c in clips if c.asset.is_favorite]
        all_non_favorites = [c for c in clips if not c.asset.is_favorite]

        logger.info(
            f"Initial split: {len(all_favorites)} favorites, "
            f"{len(all_non_favorites)} non-favorites"
        )

        # Apply filters ONLY to non-favorites
        filtered_non_favorites = all_non_favorites

        # HDR filter (only for non-favorites)
        if self.config.hdr_only:
            before = len(filtered_non_favorites)
            filtered_non_favorites = [c for c in filtered_non_favorites if c.is_hdr]
            logger.info(f"HDR filter on non-favorites: {before} -> {len(filtered_non_favorites)}")

        # Filter out compilations (FamilyAlbum, etc.) - videos without camera EXIF
        # Only apply to non-favorites; favorites are kept even if they're compilations
        before = len(filtered_non_favorites)
        filtered_non_favorites = [c for c in filtered_non_favorites if c.is_camera_original]
        compilations_removed = before - len(filtered_non_favorites)
        if compilations_removed > 0:
            logger.info(
                f"Compilation filter: removed {compilations_removed} non-camera videos "
                f"(no make/model EXIF)"
            )

        # Also filter compilations from favorites but just log a warning
        compilation_favorites = [c for c in all_favorites if not c.is_camera_original]
        if compilation_favorites:
            logger.warning(
                f"Note: {len(compilation_favorites)} favorites appear to be compilations "
                f"(no camera EXIF) - keeping them anyway"
            )

        # Resolution filter (only for non-favorites)
        # Auto-calculate min resolution based on output: 4K→1080, 1080→720, 720→480
        min_res = self.config.min_resolution
        if min_res == 0 and self.config.output_resolution > 0:
            min_res = self.config.output_resolution // 2  # 4K(2160)→1080, 1080→540
            min_res = max(min_res, 480)  # Floor at 480p

        if min_res > 0:
            before = len(filtered_non_favorites)
            filtered_non_favorites = [
                c for c in filtered_non_favorites
                if max(c.width, c.height) >= min_res
            ]
            logger.info(
                f"Resolution filter on non-favorites: {before} -> {len(filtered_non_favorites)} "
                f"(min {min_res}px for {self.config.output_resolution}p output)"
            )

        # Calculate how many non-favorites to analyze for gap-filling
        from collections import defaultdict

        # Find days/weeks that have favorites vs days that don't
        weeks_with_favorites: set[str] = set()
        non_favorites_by_week: dict[str, list] = defaultdict(list)

        for clip in all_favorites:
            week = clip.asset.file_created_at.strftime("%Y-W%W")
            weeks_with_favorites.add(week)

        for clip in filtered_non_favorites:
            week = clip.asset.file_created_at.strftime("%Y-W%W")
            non_favorites_by_week[week].append(clip)

        # Find weeks without favorites that need gap-filling
        weeks_needing_fill = set(non_favorites_by_week.keys()) - weeks_with_favorites

        # Select best non-favorites from gap weeks (up to 3 per week)
        gap_fillers: list[VideoClipInfo] = []
        for week in sorted(weeks_needing_fill):
            week_clips = non_favorites_by_week[week]
            # Sort by quality (resolution * bitrate)
            week_clips.sort(
                key=lambda c: (c.width * c.height if c.width and c.height else 0, c.bitrate or 0),
                reverse=True,
            )
            gap_fillers.extend(week_clips[:3])

        # Build final list: ALL favorites + gap fillers
        selected: list[VideoClipInfo] = []
        selected_ids: set[str] = set()

        # 1. ALL favorites (no filtering, no cap!)
        for clip in all_favorites:
            selected.append(clip)
            selected_ids.add(clip.asset.id)

        # 2. Gap fillers from weeks without favorites
        for clip in gap_fillers:
            if clip.asset.id not in selected_ids:
                selected.append(clip)
                selected_ids.add(clip.asset.id)

        # Store non-favorites for potential later use (gap filling after selection)
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

    def _phase_analyze(self, clips: list[VideoClipInfo]) -> list[ClipWithSegment]:
        """Phase 3: Analyze clips for best segments.

        Args:
            clips: Candidate clips.

        Returns:
            Clips with their optimal segments.
        """
        # Filter out clips that are too short (minimum 1.5 seconds)
        MIN_DURATION = 1.5
        valid_clips = [c for c in clips if (c.duration_seconds or 0) >= MIN_DURATION]
        skipped = len(clips) - len(valid_clips)
        if skipped > 0:
            logger.info(f"Skipping {skipped} clips shorter than {MIN_DURATION}s")

        self.tracker.start_phase(PipelinePhase.ANALYZING, len(valid_clips))

        results: list[ClipWithSegment] = []

        for clip in valid_clips:
            name = clip.asset.original_file_name or clip.asset.id[:8]
            self.tracker.start_item(name, asset_id=clip.asset.id)

            try:
                start, end, score, preview_path, llm_analysis = self._analyze_clip_with_preview(clip)

                # Store LLM analysis in clip for UI display
                if llm_analysis:
                    from typing import cast
                    clip.llm_description = cast(str | None, llm_analysis.get("description"))
                    clip.llm_emotion = cast(str | None, llm_analysis.get("emotion"))
                    clip.llm_setting = cast(str | None, llm_analysis.get("setting"))
                    clip.llm_activities = cast(list[str] | None, llm_analysis.get("activities"))
                    clip.llm_subjects = cast(list[str] | None, llm_analysis.get("subjects"))
                    clip.llm_interestingness = cast(float | None, llm_analysis.get("interestingness"))
                    clip.llm_quality = cast(float | None, llm_analysis.get("quality"))

                results.append(
                    ClipWithSegment(
                        clip=clip,
                        start_time=start,
                        end_time=end,
                        score=score,
                    )
                )
                self.tracker.complete_item(
                    clip.asset.id,
                    video_duration=clip.duration_seconds,
                    segment=(start, end),
                    score=score,
                    preview_path=preview_path,  # Pass preview path for UI display
                    llm_description=cast(str | None, llm_analysis.get("description")) if llm_analysis else None,
                    llm_emotion=cast(str | None, llm_analysis.get("emotion")) if llm_analysis else None,
                    llm_interestingness=cast(float | None, llm_analysis.get("interestingness")) if llm_analysis else None,
                    llm_quality=cast(float | None, llm_analysis.get("quality")) if llm_analysis else None,
                )

                # Previews are now stored in persistent directory (~/.cache/immich-memories/previews/)
                # Don't delete them - they're needed for UI preview display

            except Exception as e:
                error_msg = str(e)
                logger.error(f"Failed to analyze {clip.asset.id}: {error_msg}")
                self.tracker.complete_item(clip.asset.id, success=False, error=error_msg)

                # Fall back to first N seconds
                duration = clip.duration_seconds or 10
                results.append(
                    ClipWithSegment(
                        clip=clip,
                        start_time=0.0,
                        end_time=min(duration, self.config.avg_clip_duration),
                        score=0.0,
                    )
                )

            # Memory optimization: aggressive cleanup after EACH clip analysis
            # This is critical to prevent OOM during long analysis phases
            gc.collect()

            # Check for cancellation after each clip
            self._check_cancelled()

        self.tracker.complete_phase()

        logger.info(f"Phase 3: Analyzed {len(results)} clips")

        # Log session summary for token tracking
        from immich_memories.analysis.content_analyzer import ContentAnalyzer
        ContentAnalyzer.log_session_summary()

        return results

    def _analyze_clip(
        self,
        clip: VideoClipInfo,
    ) -> tuple[float, float, float]:
        """Analyze a single clip for best segment.

        Args:
            clip: Clip to analyze.

        Returns:
            Tuple of (start_time, end_time, score).
        """
        from immich_memories.analysis.scoring import SceneScorer
        from immich_memories.cache.video_cache import VideoDownloadCache
        from immich_memories.config import get_config

        config = get_config()

        # Check if we have cached analysis
        cached = self.analysis_cache.get_analysis(clip.asset.id)
        if cached and cached.segments:
            # Use cached best segment
            best = max(cached.segments, key=lambda s: s.total_score or 0.0)
            return best.start_time, best.end_time, best.total_score or 0.0

        # Download video
        video_path: Path | None = None
        temp_file: Path | None = None

        try:
            if config.cache.video_cache_enabled:
                video_cache = VideoDownloadCache(
                    cache_dir=config.cache.video_cache_path,
                    max_size_gb=config.cache.video_cache_max_size_gb,
                    max_age_days=config.cache.video_cache_max_age_days,
                )
                video_path = video_cache.download_or_get(self.client, clip.asset)
            else:
                suffix = Path(clip.asset.original_file_name or "video.mp4").suffix or ".mp4"
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    temp_file = Path(tmp.name)
                self.client.download_asset(clip.asset.id, temp_file)
                video_path = temp_file

            if not video_path or not video_path.exists():
                raise ValueError("Failed to download video")

            # Score segments
            scorer = SceneScorer()
            moments = scorer.sample_and_score_video(
                video_path,
                segment_duration=self.config.segment_duration,
                overlap=0.5,
                sample_frames=5,
            )

            if not moments:
                # Fall back to first N seconds
                duration = clip.duration_seconds or 10
                return 0.0, min(duration, self.config.avg_clip_duration), 0.0

            # Find best moment
            best_moment = max(moments, key=lambda m: m.total_score)

            # Clamp to target duration
            segment_duration = min(best_moment.duration, self.config.avg_clip_duration)
            start = best_moment.start_time
            end = start + segment_duration

            # Save to cache for future use
            self.analysis_cache.save_analysis(
                asset=clip.asset,
                video_info=clip,
                perceptual_hash=None,
                segments=moments,
            )

            return start, end, best_moment.total_score

        finally:
            if temp_file:
                try:
                    temp_file.unlink(missing_ok=True)
                except Exception:
                    pass

    def _analyze_clip_with_preview(
        self,
        clip: VideoClipInfo,
    ) -> tuple[float, float, float, str | None, dict[str, object] | None]:
        """Analyze a clip and extract a preview segment.

        Args:
            clip: Clip to analyze.

        Returns:
            Tuple of (start_time, end_time, score, preview_path, llm_analysis).
            llm_analysis is a dict with description, emotion, interestingness, quality.
        """
        from immich_memories.analysis.scoring import SceneScorer
        from immich_memories.cache.video_cache import VideoDownloadCache
        from immich_memories.config import get_config

        config = get_config()

        # Check if we have cached analysis
        cached = self.analysis_cache.get_analysis(clip.asset.id)
        use_cached = cached is not None and cached.segments is not None and len(cached.segments) > 0

        if use_cached and cached is not None and cached.segments:
            # Use cached best segment - SKIP video download entirely for speed
            best = max(cached.segments, key=lambda s: s.total_score or 0.0)
            start, end, score = best.start_time, best.end_time, best.total_score or 0.0

            # Extract LLM analysis from cached data if available
            llm_analysis = None
            if hasattr(best, 'llm_description') and (best.llm_description or getattr(best, 'llm_emotion', None)):
                llm_analysis = {
                    "description": getattr(best, 'llm_description', None),
                    "emotion": getattr(best, 'llm_emotion', None),
                    "setting": getattr(best, 'llm_setting', None),
                    "activities": getattr(best, 'llm_activities', None),
                    "subjects": getattr(best, 'llm_subjects', None),
                    "interestingness": getattr(best, 'llm_interestingness', None),
                    "quality": getattr(best, 'llm_quality', None),
                }

            logger.info(
                f"Using cached analysis for {clip.asset.id}: "
                f"{start:.1f}s - {end:.1f}s (score={score:.2f})"
            )
            # Return immediately - no need to download video or extract preview
            return start, end, score, None, llm_analysis

        # Not cached - need to download and analyze
        start, end, score = 0.0, 0.0, 0.0

        # Download video for analysis and preview extraction
        analysis_video: Path | None = None  # Potentially downscaled for speed
        original_video: Path | None = None  # Full quality for preview extraction
        temp_file: Path | None = None
        preview_path: str | None = None
        llm_analysis: dict[str, object] | None = None  # LLM content analysis results

        try:
            if config.cache.video_cache_enabled:
                video_cache = VideoDownloadCache(
                    cache_dir=config.cache.video_cache_path,
                    max_size_gb=config.cache.video_cache_max_size_gb,
                    max_age_days=config.cache.video_cache_max_age_days,
                )
                # Speed optimization: get downscaled video for analysis, original for preview
                analysis_video, original_video = video_cache.get_analysis_video(
                    self.client,
                    clip.asset,
                    target_height=config.analysis.analysis_resolution,
                    enable_downscaling=config.analysis.enable_downscaling,
                )
            else:
                suffix = Path(clip.asset.original_file_name or "video.mp4").suffix or ".mp4"
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    temp_file = Path(tmp.name)
                self.client.download_asset(clip.asset.id, temp_file)
                analysis_video = temp_file
                original_video = temp_file  # No downscaling for temp files

            if not analysis_video or not analysis_video.exists():
                raise ValueError("Failed to download video")

            # Log whether we're using downscaled video
            if analysis_video != original_video:
                logger.info(
                    f"Using downscaled video for analysis: {analysis_video.name} "
                    f"(original: {original_video.name if original_video else 'N/A'})"
                )
            else:
                logger.info(f"Using original video (no downscaling): {analysis_video.name}")

            # Analyze the video using potentially downscaled version
            # (we already returned early if cached, so this always runs)
            min_segment = config.analysis.min_segment_duration
            max_segment = config.analysis.max_segment_duration
            video_duration = clip.duration_seconds or 30

            # Try unified analysis first (audio-aware boundaries)
            if config.analysis.use_unified_analysis:
                try:
                    from immich_memories.analysis.unified_analyzer import (
                        UnifiedSegmentAnalyzer,
                    )

                    # Initialize content analyzer if enabled
                    content_analyzer = None
                    content_weight = 0.0
                    if config.content_analysis.enabled:
                        try:
                            from immich_memories.analysis.content_analyzer import (
                                get_content_analyzer,
                            )

                            content_analyzer = get_content_analyzer(
                                ollama_url=config.llm.ollama_url,
                                ollama_model=config.llm.ollama_model,
                                openai_api_key=config.llm.openai_api_key,
                                openai_model=config.llm.openai_model,
                                openai_base_url=config.llm.openai_base_url,
                                openai_image_detail=config.content_analysis.openai_image_detail,
                                max_height=config.content_analysis.frame_max_height,
                                provider=config.llm.provider,
                            )
                            content_weight = config.content_analysis.weight
                            if content_analyzer:
                                logger.info(
                                    f"LLM content analysis enabled "
                                    f"(provider={config.llm.provider}, weight={content_weight:.0%})"
                                )
                            else:
                                logger.warning("Content analysis enabled but no analyzer available")
                        except Exception as e:
                            logger.warning(f"Failed to initialize content analyzer: {e}")

                    unified_analyzer = UnifiedSegmentAnalyzer(
                        scorer=SceneScorer(),
                        content_analyzer=content_analyzer,
                        min_segment_duration=min_segment,
                        max_segment_duration=max_segment,
                        silence_threshold_db=config.analysis.silence_threshold_db,
                        cut_point_merge_tolerance=config.analysis.cut_point_merge_tolerance,
                        content_weight=content_weight,
                        audio_content_enabled=config.audio_content.enabled,
                        audio_content_weight=config.audio_content.weight,
                    )

                    # Analyze with audio-aware boundaries
                    # - Use downscaled video for visual analysis (faster)
                    # - Use original video for audio analysis (needs audio track)
                    segments = unified_analyzer.analyze(
                        analysis_video,  # Downscaled for visual (~3-5x faster)
                        video_duration=video_duration,
                        audio_video_path=original_video,  # Original for audio analysis
                    )

                    if segments:
                        best_segment = segments[0]  # Already sorted by score
                        start = best_segment.start_time
                        end = best_segment.end_time
                        score = best_segment.total_score

                        # Extract LLM analysis if available
                        llm_analysis = None
                        if best_segment.llm_description or best_segment.llm_emotion:
                            llm_analysis = {
                                "description": best_segment.llm_description,
                                "emotion": best_segment.llm_emotion,
                                "setting": best_segment.llm_setting,
                                "activities": best_segment.llm_activities,
                                "subjects": best_segment.llm_subjects,
                                "interestingness": best_segment.llm_interestingness,
                                "quality": best_segment.llm_quality,
                            }

                        # Convert to MomentScore for caching compatibility
                        moments = [seg.to_moment_score() for seg in segments]

                        # Save to cache for future use
                        self.analysis_cache.save_analysis(
                            asset=clip.asset,
                            video_info=clip,
                            perceptual_hash=None,
                            segments=moments,
                        )

                        logger.info(
                            f"Unified analysis: segment {start:.1f}s - {end:.1f}s "
                            f"(score={score:.2f}, cut_quality={best_segment.cut_quality:.2f})"
                        )

                        # Memory cleanup - CRITICAL to prevent OOM
                        del segments
                        del moments
                        # Clear caches and release analyzer resources
                        unified_analyzer.clear_cache()
                        unified_analyzer.scorer.release_capture()  # Release video capture
                        del unified_analyzer
                        if content_analyzer:
                            del content_analyzer
                        gc.collect()
                    else:
                        logger.warning("Unified analysis returned no segments, using legacy")
                        # Cleanup even when no segments
                        unified_analyzer.clear_cache()
                        unified_analyzer.scorer.release_capture()  # Release video capture
                        del unified_analyzer
                        if content_analyzer:
                            del content_analyzer
                        gc.collect()
                        # Fall through to legacy analysis
                        config.analysis.use_unified_analysis = False
                except Exception as e:
                    logger.warning(f"Unified analysis failed: {e}, using legacy approach")
                    # Cleanup on exception
                    try:
                        if 'unified_analyzer' in locals():
                            unified_analyzer.clear_cache()
                            unified_analyzer.scorer.release_capture()  # Release video capture
                            del unified_analyzer
                        if 'content_analyzer' in locals() and content_analyzer:
                            del content_analyzer
                        gc.collect()
                    except Exception:
                        pass
                    # Fall through to legacy analysis

            # Legacy analysis (visual scoring with post-hoc silence adjustment)
            if not config.analysis.use_unified_analysis or score == 0.0:
                # Score segments using downscaled video for speed
                scorer = SceneScorer()
                moments = scorer.sample_and_score_video(
                    analysis_video,  # Use downscaled for ~3-5x speedup
                    segment_duration=self.config.segment_duration,
                    overlap=0.5,
                    sample_frames=5,
                )

                if not moments:
                    # Fall back to first N seconds
                    duration = clip.duration_seconds or 10
                    return 0.0, min(duration, self.config.avg_clip_duration), 0.0, None, None

                # Find best moment
                best_moment = max(moments, key=lambda m: m.total_score)

                # Calculate segment duration with min/max constraints
                segment_duration = max(min_segment, min(best_moment.duration, max_segment))

                start = best_moment.start_time
                end = start + segment_duration
                score = best_moment.total_score

                # Ensure we don't exceed video bounds
                if end > video_duration:
                    # Shift segment to fit within video
                    end = video_duration
                    start = max(0, end - segment_duration)

                # Try to adjust boundaries to silence gaps (avoid cutting mid-sentence)
                # Note: Use original video for audio analysis, not downscaled (which has no audio)
                try:
                    from immich_memories.analysis.silence_detection import (
                        adjust_segment_to_silence,
                        detect_silence_gaps,
                    )

                    silence_gaps = detect_silence_gaps(original_video or analysis_video)
                    if silence_gaps:
                        start, end = adjust_segment_to_silence(
                            start,
                            end,
                            silence_gaps,
                            max_adjustment=1.0,
                            min_duration=min_segment,
                        )
                        logger.debug(f"Adjusted segment to silence: {start:.1f}s - {end:.1f}s")
                except Exception as e:
                    logger.debug(f"Silence detection skipped: {e}")

                # Save to cache for future use
                self.analysis_cache.save_analysis(
                    asset=clip.asset,
                    video_info=clip,
                    perceptual_hash=None,
                    segments=moments,
                )

                # Memory optimization: release scoring objects after caching
                del moments
                scorer.release_capture()  # Release video capture before deleting
                del scorer
                gc.collect()

            # Extract preview segment for UI display (always try this)
            # Note: Use original video for quality, not downscaled version
            try:
                preview_source = original_video or analysis_video
                logger.info(f"Extracting preview for {clip.asset.id}: {start:.1f}s - {end:.1f}s")
                preview_path = self._extract_preview_segment(
                    preview_source, start, end, asset_id=clip.asset.id
                )
                if preview_path and Path(preview_path).exists():
                    file_size = Path(preview_path).stat().st_size
                    logger.info(f"Preview extracted: {preview_path} ({file_size / 1024:.1f} KB)")
                else:
                    logger.warning(f"Preview file not created for {clip.asset.id}")
                    preview_path = None
            except Exception as e:
                logger.warning(f"Failed to extract preview for {clip.asset.id}: {e}")
                preview_path = None

            return start, end, score, preview_path, llm_analysis

        finally:
            # Clean up temp file if not using cache
            if temp_file:
                try:
                    temp_file.unlink(missing_ok=True)
                except Exception:
                    pass
            # Clean up downscaled video to free disk space (original stays in cache)
            if analysis_video and original_video and analysis_video != original_video:
                try:
                    cleanup_downscaled(original_video)
                    logger.debug(f"Cleaned up downscaled video for: {original_video.name}")
                except Exception:
                    pass

            # CRITICAL: Per-clip memory cleanup to prevent OOM on long runs
            # This ensures memory is released after each clip regardless of code path
            gc.collect()

    def _extract_preview_segment(
        self,
        video_path: Path,
        start: float,
        end: float,
        min_duration: float = 2.0,
        max_duration: float = 15.0,
        asset_id: str | None = None,
    ) -> str:
        """Extract a preview segment from a video.

        Uses ffmpeg directly for better compatibility with iPhone videos
        that have spatial audio (apac codec) which moviepy can't handle.

        Previews are stored in a persistent cache directory to survive
        temp file cleanup and allow preview display.

        Args:
            video_path: Path to source video.
            start: Start time in seconds.
            end: End time in seconds.
            min_duration: Minimum preview duration (default 2s).
            max_duration: Maximum preview duration (default 15s).
            asset_id: Optional asset ID for persistent storage.

        Returns:
            Path to extracted preview file.
        """
        import subprocess
        import time

        # Use persistent preview directory instead of temp files
        # Keep recent previews (Streamlit caches file references internally)
        preview_dir = Path.home() / ".cache" / "immich-memories" / "previews"
        preview_dir.mkdir(parents=True, exist_ok=True)

        # Only delete old previews if we have too many (keep last 20)
        # This prevents Streamlit's internal media cache from breaking
        MAX_PREVIEWS = 20
        existing_previews = sorted(preview_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
        if len(existing_previews) > MAX_PREVIEWS:
            # Delete oldest previews, keeping the most recent ones
            for old_preview in existing_previews[:-MAX_PREVIEWS]:
                try:
                    old_preview.unlink()
                except Exception:
                    pass

        # Use timestamp-based filename to bust browser cache
        timestamp = int(time.time() * 1000)
        preview_path = str(preview_dir / f"preview_{timestamp}.mp4")

        # Get video duration using ffprobe
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(video_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            video_duration = float(result.stdout.strip())
        except Exception:
            video_duration = 60.0  # Default fallback

        # Calculate segment duration and enforce min/max
        segment_duration = end - start

        if segment_duration < min_duration:
            # Extend to minimum duration, centered on original segment
            extension = (min_duration - segment_duration) / 2
            start = start - extension
            end = end + extension
            segment_duration = min_duration

        if segment_duration > max_duration:
            # Trim to maximum duration, keeping start
            end = start + max_duration
            segment_duration = max_duration

        # Clamp to video bounds
        if start < 0:
            end = end - start
            start = 0
        if end > video_duration:
            start = max(0, start - (end - video_duration))
            end = video_duration

        # Final safety check
        start = max(0, start)
        end = min(video_duration, end)
        duration = end - start
        if duration < 0.5:
            start = 0
            duration = min(video_duration, max_duration)

        logger.debug(
            f"Preview segment: {start:.1f}s - {start + duration:.1f}s "
            f"(duration: {duration:.1f}s, video: {video_duration:.1f}s)"
        )

        # Use ffmpeg directly - more reliable for iPhone videos with spatial audio
        # -map 0:v:0 selects only the first video stream
        # -map 0:a:0 selects only the first audio stream (AAC), ignoring spatial audio
        # Use GPU-accelerated encoding when available
        encoder_args = _get_fast_encoder_args()
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start),
            "-i",
            str(video_path),
            "-t",
            str(duration),
            "-map",
            "0:v:0",  # First video stream
            "-map",
            "0:a:0?",  # First audio stream (optional - '?' means don't fail if missing)
            *encoder_args,
            "-c:a",
            "aac",  # Re-encode audio to AAC for compatibility
            "-b:a",
            "128k",
            "-threads",
            "2",  # For CPU fallback
            "-loglevel",
            "error",
            preview_path,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {result.stderr}")

        if not Path(preview_path).exists():
            raise RuntimeError("Preview file not created")

        return preview_path

    def _detect_density_hotspots(
        self,
        favorites_by_week: dict[str, int],
    ) -> dict[str, float]:
        """Detect weeks with unusually high favorites density.

        Returns boost multiplier for each week based on relative favorites concentration.
        This automatically detects important periods (holidays, birthdays, events)
        without hardcoding any dates.

        Args:
            favorites_by_week: Count of favorites per week.

        Returns:
            Dict of week -> boost multiplier.
        """
        if not favorites_by_week:
            return {}

        total_favorites = sum(favorites_by_week.values())
        num_weeks = len(favorites_by_week)

        if total_favorites == 0 or num_weeks == 0:
            return {}

        # Average favorites per week
        avg_favorites = total_favorites / num_weeks

        # Calculate boost based on how much above average each week is
        boosts = {}
        for week, fav_count in favorites_by_week.items():
            if fav_count == 0:
                boosts[week] = 1.0
                continue

            # How many times above average?
            ratio = fav_count / max(avg_favorites, 0.5)

            if ratio >= 4.0:
                # 4x+ above average = major event
                boosts[week] = 3.0
            elif ratio >= 2.5:
                # 2.5x+ above average = significant period
                boosts[week] = 2.0
            elif ratio >= 1.5:
                # 1.5x+ above average = notable week
                boosts[week] = 1.5
            else:
                boosts[week] = 1.0

        # Log detected hotspots
        hotspots = [(w, b) for w, b in boosts.items() if b > 1.0]
        if hotspots:
            logger.info(f"Detected {len(hotspots)} density hotspots: {hotspots[:5]}...")

        return boosts

    def _select_clips_distributed_by_date(
        self,
        clips: list[ClipWithSegment],
        target_count: int,
    ) -> list[ClipWithSegment]:
        """Select clips using density-aware favorites-first approach.

        NEW ALGORITHM:
        1. Start with ALL analyzed favorites
        2. Calculate density per week (high density = events/holidays)
        3. If over duration budget, scale down - but preserve high-density weeks
        4. If under budget, fill gaps with non-favorites

        Args:
            clips: Analyzed clips with scores.
            target_count: Target number of clips to select.

        Returns:
            Selected clips distributed by density.
        """
        from collections import defaultdict
        import math

        if not clips:
            return []

        # Separate favorites and non-favorites
        favorites = [c for c in clips if c.clip.asset.is_favorite]
        non_favorites = [c for c in clips if not c.clip.asset.is_favorite]

        # Sort by date
        favorites.sort(key=lambda c: c.clip.asset.file_created_at or datetime.min)
        non_favorites.sort(key=lambda c: c.clip.asset.file_created_at or datetime.min)

        logger.info(f"Distribution input: {len(favorites)} favorites, {len(non_favorites)} non-favorites")

        if not favorites:
            # No favorites - just take top-scored non-favorites
            non_favorites.sort(key=lambda c: c.score, reverse=True)
            return non_favorites[:target_count]

        # STEP 1: Start with ALL favorites - calculate density per week
        favorites_by_week: dict[str, list[ClipWithSegment]] = defaultdict(list)
        for fav in favorites:
            week_key = fav.clip.asset.file_created_at.strftime("%Y-W%W")
            favorites_by_week[week_key].append(fav)

        sorted_weeks = sorted(favorites_by_week.keys())
        num_weeks = len(sorted_weeks)

        # Calculate density: favorites per week
        avg_per_week = len(favorites) / max(num_weeks, 1)
        logger.info(f"Density: {len(favorites)} favorites across {num_weeks} weeks (avg {avg_per_week:.1f}/week)")

        # Identify HIGH DENSITY weeks (likely events/holidays)
        # High density = more than 1.5x average
        high_density_weeks: set[str] = set()
        for week, week_favs in favorites_by_week.items():
            if len(week_favs) >= avg_per_week * 1.5:
                high_density_weeks.add(week)

        # Also mark special weeks: first, last, birthday
        special_weeks: set[str] = set()
        if sorted_weeks:
            special_weeks.add(sorted_weeks[0])  # First week
            special_weeks.add(sorted_weeks[-1])  # Last week

        # Birthday week detection
        if hasattr(self.config, 'birthday_month') and self.config.birthday_month:
            for fav in favorites:
                fav_date = fav.clip.asset.file_created_at
                if fav_date.month == self.config.birthday_month and abs(fav_date.day - 7) <= 10:
                    birthday_week = fav_date.strftime("%Y-W%W")
                    special_weeks.add(birthday_week)
                    high_density_weeks.add(birthday_week)  # Birthday always high priority
                    logger.info(f"Birthday week: {birthday_week}")
                    break

        protected_weeks = high_density_weeks | special_weeks
        logger.info(f"Protected weeks (high density + special): {sorted(protected_weeks)}")

        # STEP 2: Select ALL favorites initially
        selected_favorites: list[ClipWithSegment] = list(favorites)
        selected_ids: set[str] = {c.clip.asset.id for c in favorites}

        logger.info(f"Starting with ALL {len(selected_favorites)} favorites")

        # STEP 3: Check if we're over budget and need to scale down
        total_duration = sum(c.end_time - c.start_time for c in selected_favorites)
        target_duration = target_count * 5.0  # Estimate: target_count * 5s avg
        max_duration = target_duration * 1.25  # Allow 25% buffer

        if total_duration > max_duration:
            logger.info(
                f"Duration {total_duration:.0f}s exceeds max {max_duration:.0f}s, "
                f"scaling down favorites from low-density weeks..."
            )

            # Scale down by removing lowest-scored favorites from LOW-DENSITY weeks only
            # Protected weeks (high density + special) keep all clips
            removable: list[ClipWithSegment] = []
            protected: list[ClipWithSegment] = []

            for clip in selected_favorites:
                week = clip.clip.asset.file_created_at.strftime("%Y-W%W")
                if week in protected_weeks:
                    protected.append(clip)
                else:
                    removable.append(clip)

            # Sort removable by score (lowest first)
            removable.sort(key=lambda c: c.score)

            # Remove until under budget (but keep at least 1 per week)
            removed_count = 0
            while total_duration > max_duration and removable:
                candidate = removable[0]
                candidate_week = candidate.clip.asset.file_created_at.strftime("%Y-W%W")

                # Check if this week would have 0 clips after removal
                week_count = sum(1 for c in removable if c.clip.asset.file_created_at.strftime("%Y-W%W") == candidate_week)
                if week_count <= 1:
                    # Don't remove the last clip from a week - move to protected
                    protected.append(removable.pop(0))
                    continue

                removed = removable.pop(0)
                total_duration -= (removed.end_time - removed.start_time)
                selected_ids.discard(removed.clip.asset.id)
                removed_count += 1

            selected_favorites = protected + removable
            logger.info(f"Scaled down: removed {removed_count} favorites, kept {len(selected_favorites)}")

        # STEP 4: Fill gaps with non-favorites
        # Group non-favorites by week
        non_favs_by_week: dict[str, list[ClipWithSegment]] = defaultdict(list)
        for clip in non_favorites:
            week_key = clip.clip.asset.file_created_at.strftime("%Y-W%W")
            non_favs_by_week[week_key].append(clip)

        # Find all weeks in the date range
        first_date = favorites[0].clip.asset.file_created_at
        last_date = favorites[-1].clip.asset.file_created_at

        from datetime import timedelta
        all_weeks_in_range: list[str] = []
        current = first_date
        while current <= last_date:
            week_key = current.strftime("%Y-W%W")
            if week_key not in all_weeks_in_range:
                all_weeks_in_range.append(week_key)
            current += timedelta(days=7)

        # Find weeks with no favorites
        selected_by_week: dict[str, list[ClipWithSegment]] = defaultdict(list)
        for clip in selected_favorites:
            week_key = clip.clip.asset.file_created_at.strftime("%Y-W%W")
            selected_by_week[week_key].append(clip)

        # Fill gaps with non-favorites
        gap_fillers: list[ClipWithSegment] = []
        for week in all_weeks_in_range:
            if len(selected_by_week.get(week, [])) == 0:
                week_non_favs = non_favs_by_week.get(week, [])
                if week_non_favs:
                    week_non_favs.sort(key=lambda c: c.score, reverse=True)
                    for clip in week_non_favs[:2]:
                        if clip.clip.asset.id not in selected_ids:
                            gap_fillers.append(clip)
                            selected_ids.add(clip.clip.asset.id)

        logger.info(f"Added {len(gap_fillers)} gap-fillers from non-favorites")

        # STEP 5: If still under target, add more non-favorites (spread across weeks)
        selected = selected_favorites + gap_fillers
        remaining_slots = target_count - len(selected)

        if remaining_slots > 0:
            clips_per_week = defaultdict(int)
            for clip in selected:
                week_key = clip.clip.asset.file_created_at.strftime("%Y-W%W")
                clips_per_week[week_key] += 1

            remaining_non_favs = [c for c in non_favorites if c.clip.asset.id not in selected_ids]
            for clip in remaining_non_favs:
                week_key = clip.clip.asset.file_created_at.strftime("%Y-W%W")
                existing = clips_per_week.get(week_key, 0)
                clip._distribution_score = clip.score - (existing * 0.1)

            remaining_non_favs.sort(key=lambda c: c._distribution_score, reverse=True)

            for clip in remaining_non_favs[:remaining_slots]:
                selected.append(clip)
                selected_ids.add(clip.clip.asset.id)

            logger.info(f"Added {min(remaining_slots, len(remaining_non_favs))} additional non-favorites")

        # Final stats
        final_favorites = sum(1 for c in selected if c.clip.asset.is_favorite)
        final_non_favorites = len(selected) - final_favorites

        # Count months covered
        months_covered = set()
        for clip in selected:
            months_covered.add(clip.clip.asset.file_created_at.strftime("%Y-%m"))

        logger.info(
            f"Final selection: {len(selected)} clips "
            f"({final_favorites} favorites, {final_non_favorites} non-favorites) "
            f"across {len(months_covered)} months"
        )

        return selected

    def _scale_to_target_duration(
        self,
        clips: list[ClipWithSegment],
        target_duration: float,
    ) -> list[ClipWithSegment]:
        """Scale down selection to fit target duration.

        Process:
        1. Calculate actual total duration from segments
        2. If under target + 10%, return as-is
        3. If over: remove lowest-scored clips (non-favorites first)

        Note: Does NOT trim individual segments - that's handled in unified_analyzer.

        Args:
            clips: Selected clips with segments.
            target_duration: Target total duration in seconds.

        Returns:
            Clips that fit within target duration.
        """
        from collections import defaultdict

        if not clips:
            return clips

        # Calculate current total duration
        total = sum(c.end_time - c.start_time for c in clips)
        max_allowed = target_duration * 1.10  # Allow 10% over

        if total <= max_allowed:
            logger.info(f"Duration {total:.0f}s within target {target_duration:.0f}s (+10%)")
            return clips

        logger.info(f"Duration {total:.0f}s exceeds target {target_duration:.0f}s, removing clips...")

        # Protect high-density weeks and first/last weeks
        favorites_by_week: dict[str, list[ClipWithSegment]] = defaultdict(list)
        for c in clips:
            if c.clip.asset.is_favorite:
                week = c.clip.asset.file_created_at.strftime("%Y-W%W")
                favorites_by_week[week].append(c)

        num_favorite_weeks = len(favorites_by_week)
        if num_favorite_weeks > 0:
            avg_per_week = len([c for c in clips if c.clip.asset.is_favorite]) / num_favorite_weeks
            protected_weeks = {
                w for w, clips_list in favorites_by_week.items()
                if len(clips_list) >= avg_per_week * 1.5
            }
        else:
            protected_weeks = set()

        # Also protect first/last weeks
        sorted_weeks = sorted(favorites_by_week.keys())
        if sorted_weeks:
            protected_weeks.add(sorted_weeks[0])
            protected_weeks.add(sorted_weeks[-1])

        logger.debug(f"Protected weeks: {sorted(protected_weeks)}")

        # Sort by removability: non-favorites first, then low-score favorites from non-protected weeks
        def removability_score(c: ClipWithSegment) -> tuple:
            is_fav = c.clip.asset.is_favorite
            week = c.clip.asset.file_created_at.strftime("%Y-W%W")
            is_protected = week in protected_weeks
            return (is_fav, is_protected, c.score)

        clips_sorted = sorted(clips, key=removability_score)

        # Remove from most removable until under budget
        result = []
        running_total = 0.0
        removed_count = 0

        for c in reversed(clips_sorted):  # Start from least removable
            clip_duration = c.end_time - c.start_time
            if running_total + clip_duration <= max_allowed:
                result.append(c)
                running_total += clip_duration
            else:
                removed_count += 1
                logger.debug(
                    f"Removing {c.clip.asset.original_file_name or c.clip.asset.id[:8]} "
                    f"({clip_duration:.1f}s, score={c.score:.2f}, fav={c.clip.asset.is_favorite})"
                )

        # Sort back by date for chronological order
        result.sort(key=lambda c: c.clip.asset.file_created_at or datetime.min)

        logger.info(f"Removed {removed_count} clips, final duration: {running_total:.0f}s ({len(result)} clips)")
        return result

    def _deduplicate_temporal_clusters(
        self,
        clips: list[ClipWithSegment],
        time_window_minutes: float = 10.0,
    ) -> list[ClipWithSegment]:
        """Remove near-duplicate clips from the same time period.

        When multiple favorites are taken within the same time window (e.g., 10 minutes),
        they're likely capturing the same moment from slightly different angles or times.
        Keep only the best-scored one to allow more diverse content.

        Args:
            clips: Selected clips with segments and scores.
            time_window_minutes: Time window in minutes to consider as "same moment".

        Returns:
            Deduplicated clips with best representative per time cluster.
        """
        from collections import defaultdict

        if not clips:
            return clips

        # Group clips by time buckets
        time_clusters: dict[str, list[ClipWithSegment]] = defaultdict(list)

        for clip in clips:
            timestamp = clip.clip.asset.file_created_at
            # Create time bucket key (round to nearest time_window)
            bucket_minutes = int(timestamp.timestamp() / 60 / time_window_minutes)
            time_key = f"{timestamp.date()}_{bucket_minutes}"
            time_clusters[time_key].append(clip)

        result = []
        removed_count = 0
        clusters_with_duplicates = 0

        for time_key, cluster_clips in time_clusters.items():
            if len(cluster_clips) == 1:
                result.append(cluster_clips[0])
                continue

            # Multiple clips in same time window
            favorites_in_cluster = [c for c in cluster_clips if c.clip.asset.is_favorite]
            non_favorites_in_cluster = [c for c in cluster_clips if not c.clip.asset.is_favorite]

            if len(favorites_in_cluster) >= 1:
                # At least one favorite in cluster - keep only the best favorite
                # Remove ALL other clips (extra favorites AND non-favorites from same moment)
                favorites_in_cluster.sort(key=lambda c: c.score, reverse=True)
                best = favorites_in_cluster[0]

                result.append(best)

                # Count what we're removing
                removed_favorites = len(favorites_in_cluster) - 1
                removed_non_favorites = len(non_favorites_in_cluster)
                total_removed = removed_favorites + removed_non_favorites

                if total_removed > 0:
                    removed_count += total_removed
                    clusters_with_duplicates += 1

                    logger.debug(
                        f"Temporal cluster {time_key}: keeping favorite {best.clip.asset.original_file_name} "
                        f"(score={best.score:.2f}), removing {removed_favorites} favorites + "
                        f"{removed_non_favorites} non-favorites"
                    )
            else:
                # No favorites in cluster - keep only the best non-favorite
                if len(non_favorites_in_cluster) > 1:
                    non_favorites_in_cluster.sort(key=lambda c: c.score, reverse=True)
                    best = non_favorites_in_cluster[0]
                    result.append(best)
                    removed_count += len(non_favorites_in_cluster) - 1
                    clusters_with_duplicates += 1

                    logger.debug(
                        f"Temporal cluster {time_key}: keeping non-favorite {best.clip.asset.original_file_name} "
                        f"(score={best.score:.2f}), removing {len(non_favorites_in_cluster) - 1} duplicates"
                    )
                else:
                    # Only 1 non-favorite, keep it
                    result.extend(non_favorites_in_cluster)

        if removed_count > 0:
            logger.info(
                f"Temporal deduplication: removed {removed_count} clips from same-moment clusters "
                f"from {clusters_with_duplicates} time clusters (window={time_window_minutes:.0f}min)"
            )

        # Sort by date
        result.sort(key=lambda c: c.clip.asset.file_created_at or datetime.min)
        return result

    def _phase_refine(self, analyzed: list[ClipWithSegment]) -> PipelineResult:
        """Phase 4: Refine final selection.

        Args:
            analyzed: Analyzed clips with segments.

        Returns:
            Final pipeline result.
        """
        self.tracker.start_phase(PipelinePhase.REFINING, 1)
        self.tracker.start_item("Refining selection")

        # Use date-aware selection to distribute clips across the full date range
        # instead of just picking top N by score (which can cluster in certain months)
        # Add 20% buffer so user has room to deselect clips they don't want
        target_with_buffer = int(self.config.target_clips * 1.2)
        logger.info(f"Selecting with buffer: target={self.config.target_clips}, with_buffer={target_with_buffer}")
        selected = self._select_clips_distributed_by_date(
            analyzed, target_with_buffer
        )

        # Scale down to target duration (removes clips to fit target)
        # Calculate target from config: target_clips * avg_clip_duration
        target_duration = self.config.target_clips * self.config.avg_clip_duration
        selected = self._scale_to_target_duration(selected, target_duration)

        # Temporal deduplication: when multiple favorites are from same moment,
        # keep only the best-scored one (configurable window, 0 to disable)
        if self.config.temporal_dedup_window_minutes > 0:
            selected = self._deduplicate_temporal_clusters(
                selected, time_window_minutes=self.config.temporal_dedup_window_minutes
            )

        # Apply max_non_favorite_ratio (limiting non-favorites in final selection)
        # But never drop below target_clips — fill with best non-favorites if needed
        if (
            self.config.max_non_favorite_ratio < 1.0
            and self.config.prioritize_favorites
        ):
            favorites = [c for c in selected if c.clip.asset.is_favorite]
            non_favorites = [c for c in selected if not c.clip.asset.is_favorite]

            max_non_favorites = int(len(selected) * self.config.max_non_favorite_ratio)

            # Ensure we don't go below target clip count
            min_non_favorites = max(0, self.config.target_clips - len(favorites))
            max_non_favorites = max(max_non_favorites, min_non_favorites)

            if len(non_favorites) > max_non_favorites:
                # Sort non-favorites by score and keep only the best
                non_favorites.sort(key=lambda c: c.score, reverse=True)
                non_favorites = non_favorites[:max_non_favorites]

                logger.info(
                    f"Final selection: limiting non-favorites to {len(non_favorites)} "
                    f"({self.config.max_non_favorite_ratio:.0%} of {len(selected)})"
                )

                selected = favorites + non_favorites

        # Sort selected by date for chronological order
        selected.sort(key=lambda c: c.clip.asset.file_created_at or datetime.min)

        # Build result
        clip_segments: dict[str, tuple[float, float]] = {}
        selected_clips: list[VideoClipInfo] = []

        for item in selected:
            selected_clips.append(item.clip)
            clip_segments[item.clip.asset.id] = (item.start_time, item.end_time)

        # Collect errors from tracker
        errors = [{"clip_id": e.item_id, "error": e.error} for e in self.tracker.progress.errors]

        self.tracker.complete_item("selection")
        self.tracker.complete_phase()

        logger.info(f"Phase 4: Final selection of {len(selected_clips)} clips")

        return PipelineResult(
            selected_clips=selected_clips,
            clip_segments=clip_segments,
            errors=errors,
            stats={
                "total_analyzed": len(analyzed),
                "selected_count": len(selected_clips),
                "error_count": len(errors),
                "elapsed_seconds": self.tracker.progress.elapsed_seconds,
            },
        )


def smart_select_clips(
    clips: list[VideoClipInfo],
    clips_needed: int,
    hdr_only: bool = False,
    prioritize_favorites: bool = True,
    max_non_favorite_ratio: float = 1.0,
) -> list[VideoClipInfo]:
    """Smart clip selection algorithm.

    Distributes clips across days, prioritizing favorites and special days.

    Args:
        clips: Available clips.
        clips_needed: Target number of clips.
        hdr_only: Only select HDR clips.
        prioritize_favorites: Prioritize favorite clips.
        max_non_favorite_ratio: Maximum ratio of non-favorites (e.g., 0.25 = at most 25%).
            Set to 1.0 to disable this limit.

    Returns:
        Selected clips.
    """
    from collections import defaultdict

    # Filter HDR if requested
    if hdr_only:
        clips = [c for c in clips if c.is_hdr]

    if not clips:
        return []

    # Group by day
    clips_by_day: dict[str, list[VideoClipInfo]] = defaultdict(list)
    for clip in clips:
        day_key = clip.asset.file_created_at.strftime("%Y-%m-%d")
        clips_by_day[day_key].append(clip)

    # Calculate clips per day (distribute evenly, with extra for special days)
    num_days = len(clips_by_day)
    base_per_day = max(1, clips_needed // num_days) if num_days > 0 else clips_needed

    # Identify special days (more clips = more interesting)
    avg_clips_per_day = len(clips) / num_days if num_days > 0 else 0
    special_days = {
        day for day, day_clips in clips_by_day.items() if len(day_clips) > avg_clips_per_day * 1.5
    }

    # Allocate slots
    slots_per_day: dict[str, int] = {}
    remaining_slots = clips_needed

    for day in clips_by_day:
        if day in special_days:
            slots = min(base_per_day * 2, len(clips_by_day[day]), remaining_slots)
        else:
            slots = min(base_per_day, len(clips_by_day[day]), remaining_slots)
        slots_per_day[day] = slots
        remaining_slots -= slots

    # Distribute remaining slots to days with more clips
    if remaining_slots > 0:
        sorted_days = sorted(
            clips_by_day.keys(),
            key=lambda d: len(clips_by_day[d]),
            reverse=True,
        )
        for day in sorted_days:
            if remaining_slots <= 0:
                break
            available = len(clips_by_day[day]) - slots_per_day[day]
            if available > 0:
                add = min(available, remaining_slots)
                slots_per_day[day] += add
                remaining_slots -= add

    # Select clips from each day
    selected: list[VideoClipInfo] = []

    for day, slots in slots_per_day.items():
        day_clips = clips_by_day[day]

        # Separate favorites and others
        if prioritize_favorites:
            favorites = [c for c in day_clips if c.asset.is_favorite]
            others = [c for c in day_clips if not c.asset.is_favorite]

            # Sort by quality
            def quality_key(c: VideoClipInfo) -> tuple:
                res = c.width * c.height if c.width and c.height else 0
                return (res, c.bitrate or 0, c.duration_seconds or 0)

            favorites.sort(key=quality_key, reverse=True)
            others.sort(key=quality_key, reverse=True)

            # Take favorites first, then fill with others
            day_selected = favorites[:slots]
            remaining = slots - len(day_selected)
            if remaining > 0:
                day_selected.extend(others[:remaining])
        else:
            # Just sort by quality
            def quality_key(c: VideoClipInfo) -> tuple:
                res = c.width * c.height if c.width and c.height else 0
                return (res, c.bitrate or 0, c.duration_seconds or 0)

            day_clips_sorted = sorted(day_clips, key=quality_key, reverse=True)
            day_selected = day_clips_sorted[:slots]

        selected.extend(day_selected)

    # Enforce max_non_favorite_ratio
    if max_non_favorite_ratio < 1.0 and prioritize_favorites:
        favorites_selected = [c for c in selected if c.asset.is_favorite]
        non_favorites_selected = [c for c in selected if not c.asset.is_favorite]

        total_selected = len(selected)
        max_non_favorites = int(total_selected * max_non_favorite_ratio)

        if len(non_favorites_selected) > max_non_favorites:
            # Too many non-favorites, trim them
            # Sort non-favorites by quality and keep only the best ones
            def quality_key(c: VideoClipInfo) -> tuple:
                res = c.width * c.height if c.width and c.height else 0
                return (res, c.bitrate or 0, c.duration_seconds or 0)

            non_favorites_selected.sort(key=quality_key, reverse=True)
            non_favorites_to_keep = non_favorites_selected[:max_non_favorites]

            removed_count = len(non_favorites_selected) - len(non_favorites_to_keep)
            logger.info(
                f"Non-favorite ratio limit: keeping {len(non_favorites_to_keep)}/{len(non_favorites_selected)} "
                f"non-favorites ({max_non_favorite_ratio:.0%} max), removed {removed_count}"
            )

            selected = favorites_selected + non_favorites_to_keep

    # Sort by date
    selected.sort(key=lambda c: c.asset.file_created_at or datetime.min)

    return selected


def analyze_clip_for_highlight(
    video_path: Path,
    min_duration: float = 3.0,
    max_duration: float = 15.0,
    target_duration: float = 5.0,
) -> tuple[float, float, float]:
    """Analyze a single clip to find the best highlight segment.

    This is a standalone function for analyzing individual clips.

    Args:
        video_path: Path to the video file.
        min_duration: Minimum segment duration.
        max_duration: Maximum segment duration.
        target_duration: Target segment duration.

    Returns:
        Tuple of (start_time, end_time, score).
    """
    from immich_memories.analysis.scoring import SceneScorer

    scorer = SceneScorer()
    moments = scorer.sample_and_score_video(
        video_path,
        segment_duration=3.0,
        overlap=0.5,
        sample_frames=5,
    )

    if not moments:
        return 0.0, target_duration, 0.0

    # Find best moment
    best = max(moments, key=lambda m: m.total_score)

    # Clamp duration
    duration = min(max(best.duration, min_duration), max_duration)
    if duration > target_duration:
        duration = target_duration

    start = best.start_time
    end = start + duration

    return start, end, best.total_score
