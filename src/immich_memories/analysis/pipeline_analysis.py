"""Analysis phase mixin for the smart pipeline.

Contains methods for Phase 3: downloading, analyzing, and scoring video clips.
"""

from __future__ import annotations

import gc
import logging
from pathlib import Path
from typing import TYPE_CHECKING, cast

from immich_memories.processing.downscaler import cleanup_downscaled
from immich_memories.security import sanitize_filename

if TYPE_CHECKING:
    from immich_memories.analysis.smart_pipeline import ClipWithSegment
    from immich_memories.api.models import VideoClipInfo

logger = logging.getLogger(__name__)


class AnalysisMixin:
    """Mixin providing analysis phase methods for SmartPipeline."""

    def _phase_analyze(self, clips: list[VideoClipInfo]) -> list[ClipWithSegment]:
        """Phase 3: Analyze clips for best segments.

        Args:
            clips: Candidate clips.

        Returns:
            Clips with their optimal segments.
        """
        from immich_memories.analysis.smart_pipeline import ClipWithSegment

        # Filter out clips that are too short (minimum 1.5 seconds)
        MIN_DURATION = 1.5
        valid_clips = [c for c in clips if (c.duration_seconds or 0) >= MIN_DURATION]
        skipped = len(clips) - len(valid_clips)
        if skipped > 0:
            logger.info(f"Skipping {skipped} clips shorter than {MIN_DURATION}s")

        from immich_memories.analysis.progress import PipelinePhase

        self.tracker.start_phase(PipelinePhase.ANALYZING, len(valid_clips))

        results: list[ClipWithSegment] = []

        for clip in valid_clips:
            name = clip.asset.original_file_name or clip.asset.id[:8]
            self.tracker.start_item(name, asset_id=clip.asset.id)

            try:
                start, end, score, preview_path, llm_analysis = self._analyze_clip_with_preview(
                    clip
                )

                # Store LLM analysis in clip for UI display
                if llm_analysis:
                    clip.llm_description = cast(str | None, llm_analysis.get("description"))
                    clip.llm_emotion = cast(str | None, llm_analysis.get("emotion"))
                    clip.llm_setting = cast(str | None, llm_analysis.get("setting"))
                    clip.llm_activities = cast(list[str] | None, llm_analysis.get("activities"))
                    clip.llm_subjects = cast(list[str] | None, llm_analysis.get("subjects"))
                    clip.llm_interestingness = cast(
                        float | None, llm_analysis.get("interestingness")
                    )
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
                    llm_description=cast(str | None, llm_analysis.get("description"))
                    if llm_analysis
                    else None,
                    llm_emotion=cast(str | None, llm_analysis.get("emotion"))
                    if llm_analysis
                    else None,
                    llm_interestingness=cast(float | None, llm_analysis.get("interestingness"))
                    if llm_analysis
                    else None,
                    llm_quality=cast(float | None, llm_analysis.get("quality"))
                    if llm_analysis
                    else None,
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

    def _check_analysis_cache(
        self,
        clip: VideoClipInfo,
    ) -> tuple[float, float, float, str | None, dict[str, object] | None] | None:
        """Check if analysis is cached and return it.

        Args:
            clip: Clip to check cache for.

        Returns:
            Tuple of (start, end, score, None, llm_analysis) if cached, None otherwise.
        """
        cached = self.analysis_cache.get_analysis(clip.asset.id)
        if not (cached and cached.segments and len(cached.segments) > 0):
            return None

        best = max(cached.segments, key=lambda s: s.total_score or 0.0)
        start, end, score = best.start_time, best.end_time, best.total_score or 0.0

        # Extract LLM analysis from cached data if available
        cached_llm_analysis = None
        if hasattr(best, "llm_description") and (
            best.llm_description or getattr(best, "llm_emotion", None)
        ):
            cached_llm_analysis = {
                "description": getattr(best, "llm_description", None),
                "emotion": getattr(best, "llm_emotion", None),
                "setting": getattr(best, "llm_setting", None),
                "activities": getattr(best, "llm_activities", None),
                "subjects": getattr(best, "llm_subjects", None),
                "interestingness": getattr(best, "llm_interestingness", None),
                "quality": getattr(best, "llm_quality", None),
            }

        logger.info(
            f"Using cached analysis for {clip.asset.id}: "
            f"{start:.1f}s - {end:.1f}s (score={score:.2f})"
        )
        return start, end, score, None, cached_llm_analysis

    def _download_analysis_video(
        self,
        clip: VideoClipInfo,
    ) -> tuple[Path, Path, Path | None]:
        """Download video for analysis, potentially downscaled.

        Args:
            clip: Clip to download.

        Returns:
            Tuple of (analysis_video, original_video, temp_file_to_cleanup).
        """
        import tempfile

        from immich_memories.cache.video_cache import VideoDownloadCache
        from immich_memories.config import get_config

        config = get_config()
        temp_file: Path | None = None

        if config.cache.video_cache_enabled:
            video_cache = VideoDownloadCache(
                cache_dir=config.cache.video_cache_path,
                max_size_gb=config.cache.video_cache_max_size_gb,
                max_age_days=config.cache.video_cache_max_age_days,
            )
            analysis_video, original_video = video_cache.get_analysis_video(
                self.client,
                clip.asset,
                target_height=config.analysis.analysis_resolution,
                enable_downscaling=config.analysis.enable_downscaling,
            )
        else:
            safe_name = sanitize_filename(clip.asset.original_file_name or "video.mp4")
            suffix = Path(safe_name).suffix or ".mp4"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                temp_file = Path(tmp.name)
            self.client.download_asset(clip.asset.id, temp_file)
            analysis_video = temp_file
            original_video = temp_file

        if not analysis_video or not analysis_video.exists():
            raise ValueError("Failed to download video")

        if analysis_video != original_video:
            logger.info(
                f"Using downscaled video for analysis: {analysis_video.name} "
                f"(original: {original_video.name if original_video else 'N/A'})"
            )
        else:
            logger.info(f"Using original video (no downscaling): {analysis_video.name}")

        return analysis_video, original_video, temp_file

    def _init_content_analyzer(self) -> tuple[object | None, float]:
        """Initialize LLM content analyzer if enabled.

        Returns:
            Tuple of (content_analyzer, content_weight).
        """
        from immich_memories.config import get_config

        config = get_config()
        if not config.content_analysis.enabled:
            return None, 0.0

        try:
            from immich_memories.analysis.content_analyzer import get_content_analyzer

            analyzer = get_content_analyzer(
                ollama_url=config.llm.ollama_url,
                ollama_model=config.llm.ollama_model,
                openai_api_key=config.llm.openai_api_key,
                openai_model=config.llm.openai_model,
                openai_base_url=config.llm.openai_base_url,
                openai_image_detail=config.content_analysis.openai_image_detail,
                max_height=config.content_analysis.frame_max_height,
                provider=config.llm.provider,
            )
            weight = config.content_analysis.weight
            if analyzer:
                logger.info(
                    f"LLM content analysis enabled "
                    f"(provider={config.llm.provider}, weight={weight:.0%})"
                )
            else:
                logger.warning("Content analysis enabled but no analyzer available")
            return analyzer, weight
        except Exception as e:
            logger.warning(f"Failed to initialize content analyzer: {e}")
            return None, 0.0

    def _cleanup_analyzer(
        self, unified_analyzer: object | None, content_analyzer: object | None
    ) -> None:
        """Clean up analyzer resources to prevent OOM."""
        try:
            if unified_analyzer is not None:
                unified_analyzer.clear_cache()
                unified_analyzer.scorer.release_capture()
                del unified_analyzer
            if content_analyzer is not None:
                del content_analyzer
            gc.collect()
        except Exception:
            pass

    def _run_unified_analysis(
        self,
        clip: VideoClipInfo,
        analysis_video: Path,
        original_video: Path,
        video_duration: float,
    ) -> tuple[float, float, float, dict[str, object] | None]:
        """Run unified audio-aware analysis.

        Args:
            clip: Clip being analyzed.
            analysis_video: Downscaled video for visual analysis.
            original_video: Original video for audio analysis.
            video_duration: Duration of the video.

        Returns:
            Tuple of (start, end, score, llm_analysis). Returns (0, 0, 0, None) on failure.
        """
        from immich_memories.analysis.scoring import SceneScorer
        from immich_memories.analysis.unified_analyzer import UnifiedSegmentAnalyzer
        from immich_memories.config import get_config

        config = get_config()
        content_analyzer, content_weight = self._init_content_analyzer()

        unified_analyzer = UnifiedSegmentAnalyzer(
            scorer=SceneScorer(),
            content_analyzer=content_analyzer,
            min_segment_duration=config.analysis.min_segment_duration,
            max_segment_duration=config.analysis.max_segment_duration,
            silence_threshold_db=config.analysis.silence_threshold_db,
            cut_point_merge_tolerance=config.analysis.cut_point_merge_tolerance,
            content_weight=content_weight,
            audio_content_enabled=config.audio_content.enabled,
            audio_content_weight=config.audio_content.weight,
        )

        try:
            segments = unified_analyzer.analyze(
                analysis_video,
                video_duration=video_duration,
                audio_video_path=original_video,
            )

            if not segments:
                logger.warning("Unified analysis returned no segments, using legacy")
                return 0.0, 0.0, 0.0, None

            best_segment = segments[0]
            start = best_segment.start_time
            end = best_segment.end_time
            score = best_segment.total_score

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

            moments = [seg.to_moment_score() for seg in segments]
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

            del segments
            del moments
            return start, end, score, llm_analysis
        finally:
            self._cleanup_analyzer(unified_analyzer, content_analyzer)

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
        from immich_memories.config import get_config

        # Check cache first
        cached_result = self._check_analysis_cache(clip)
        if cached_result is not None:
            return cached_result

        config = get_config()
        analysis_video: Path | None = None
        original_video: Path | None = None
        temp_file: Path | None = None

        try:
            analysis_video, original_video, temp_file = self._download_analysis_video(clip)

            video_duration = clip.duration_seconds or 30
            start, end, score = 0.0, 0.0, 0.0
            llm_analysis: dict[str, object] | None = None

            # Try unified analysis first
            if config.analysis.use_unified_analysis:
                try:
                    start, end, score, llm_analysis = self._run_unified_analysis(
                        clip, analysis_video, original_video, video_duration
                    )
                except Exception as e:
                    logger.warning(f"Unified analysis failed: {e}, using legacy approach")

            # Legacy fallback
            if score == 0.0:
                start, end, score = self._run_legacy_analysis(
                    clip, analysis_video, original_video, video_duration
                )
                if start == 0.0 and end > 0.0 and score == 0.0:
                    return start, end, score, None, None

            # Extract preview
            preview_path = self._extract_and_log_preview(
                clip, original_video, analysis_video, start, end
            )

            return start, end, score, preview_path, llm_analysis

        finally:
            if temp_file:
                try:
                    temp_file.unlink(missing_ok=True)
                except Exception:
                    pass
            if analysis_video and original_video and analysis_video != original_video:
                try:
                    cleanup_downscaled(original_video)
                    logger.debug(f"Cleaned up downscaled video for: {original_video.name}")
                except Exception:
                    pass
            gc.collect()
