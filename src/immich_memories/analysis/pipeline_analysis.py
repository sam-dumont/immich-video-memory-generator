"""Analysis phase mixin: downloading, analyzing, and scoring video clips."""

from __future__ import annotations

import gc
import logging
from pathlib import Path
from typing import TYPE_CHECKING, cast

from immich_memories.processing.downscaler import cleanup_downscaled
from immich_memories.security import sanitize_filename

if TYPE_CHECKING:
    from immich_memories.analysis._content_parsing import ContentAnalyzer
    from immich_memories.analysis.smart_pipeline import ClipWithSegment
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.audio.content_analyzer import AudioContentAnalyzer

logger = logging.getLogger(__name__)


class AnalysisMixin:
    """Mixin providing analysis phase methods for SmartPipeline."""

    _cached_content_analyzer: ContentAnalyzer | None
    _cached_audio_analyzer: AudioContentAnalyzer | None

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
                    audio_categories=clip.audio_categories,
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

        # Clean up pipeline-level cached resources (PANNs model, LLM client)
        self._cleanup_pipeline_resources()

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

        Returns:
            Tuple of (start, end, score, preview_path, llm_analysis) if cached.
        """
        cached = self.analysis_cache.get_analysis(clip.asset.id)
        if not (cached and cached.segments and len(cached.segments) > 0):
            return None

        best = max(cached.segments, key=lambda s: s.total_score or 0.0)
        start, end, score = best.start_time, best.end_time, best.total_score or 0.0

        # Extract LLM analysis from persisted segment data
        cached_llm_analysis = None
        if best.llm_description or best.llm_emotion:
            cached_llm_analysis = {
                "description": best.llm_description,
                "emotion": best.llm_emotion,
                "setting": best.llm_setting,
                "activities": best.llm_activities,
                "subjects": best.llm_subjects,
                "interestingness": best.llm_interestingness,
                "quality": best.llm_quality,
            }

        # Restore audio categories from cache
        if best.audio_categories:
            clip.audio_categories = list(best.audio_categories)

        # Try to find/build a preview from the video cache
        preview_path = self._find_cached_preview(clip.asset.id, start, end)

        has_llm = "with LLM" if cached_llm_analysis else "no LLM"
        has_preview = "with preview" if preview_path else "no preview"
        logger.info(
            f"Using cached analysis for {clip.asset.id}: "
            f"{start:.1f}s - {end:.1f}s (score={score:.2f}, {has_llm}, {has_preview})"
        )
        return start, end, score, preview_path, cached_llm_analysis

    def _download_analysis_video(
        self,
        clip: VideoClipInfo,
    ) -> tuple[Path, Path, Path | None]:
        """Download video for analysis, potentially downscaled."""
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
        """Get or create cached LLM content analyzer.

        The analyzer is cached at the pipeline level to avoid recreating
        httpx.Client connections on every clip. Cleaned up in
        _cleanup_pipeline_resources().

        Returns:
            Tuple of (content_analyzer, content_weight).
        """
        from immich_memories.config import get_config

        config = get_config()
        if not config.content_analysis.enabled:
            return None, 0.0

        # Return cached analyzer if available
        if hasattr(self, "_cached_content_analyzer") and self._cached_content_analyzer:
            return self._cached_content_analyzer, config.content_analysis.weight

        try:
            from immich_memories.analysis.content_analyzer import get_content_analyzer

            analyzer = get_content_analyzer(
                provider=config.llm.provider,
                base_url=config.llm.base_url,
                model=config.llm.model,
                api_key=config.llm.api_key,
                image_detail=config.content_analysis.openai_image_detail,
                max_height=config.content_analysis.frame_max_height,
                timeout=float(config.llm.timeout_seconds),
            )
            weight = config.content_analysis.weight
            if analyzer:
                logger.info(
                    f"LLM content analysis enabled "
                    f"(provider={config.llm.provider}, weight={weight:.0%})"
                )
                self._cached_content_analyzer = analyzer
            else:
                logger.warning("Content analysis enabled but no analyzer available")
            return analyzer, weight
        except Exception as e:
            logger.warning(f"Failed to initialize content analyzer: {e}")
            return None, 0.0

    def _get_cached_audio_analyzer(self) -> object | None:
        """Get or create a pipeline-level cached AudioContentAnalyzer.

        The PANNs model is ~312MB — loading it per clip causes massive
        memory leaks. This caches it for the entire analysis phase.
        """
        from immich_memories.config import get_config

        config = get_config()
        if not config.audio_content.enabled:
            return None

        if hasattr(self, "_cached_audio_analyzer") and self._cached_audio_analyzer:
            return self._cached_audio_analyzer

        try:
            from immich_memories.audio.content_analyzer import AudioContentAnalyzer

            analyzer = AudioContentAnalyzer(
                use_panns=config.audio_content.use_panns,
                min_confidence=config.audio_content.min_confidence,
                laughter_confidence=config.audio_content.laughter_confidence,
            )
            self._cached_audio_analyzer = analyzer
            logger.info("Audio content analyzer cached at pipeline level")
            return analyzer
        except Exception as e:
            logger.warning(f"Failed to create audio analyzer: {e}")
            return None

    def _cleanup_analyzer(
        self, unified_analyzer: object | None, content_analyzer: object | None = None
    ) -> None:
        """Clean up analyzer resources to prevent OOM.

        Note: content_analyzer is NOT cleaned here — it's cached at
        pipeline level and cleaned in _cleanup_pipeline_resources().
        """
        try:
            if unified_analyzer is not None:
                unified_analyzer.clear_cache()
                unified_analyzer.scorer.release_capture()
                # Detach audio analyzer so it's not deleted with the unified analyzer
                if hasattr(unified_analyzer, "_audio_analyzer"):
                    unified_analyzer._audio_analyzer = None
                del unified_analyzer
            gc.collect()
        except Exception:
            pass

    def _cleanup_pipeline_resources(self) -> None:
        """Clean up long-lived pipeline resources after analysis phase."""
        try:
            if hasattr(self, "_cached_content_analyzer") and self._cached_content_analyzer:
                if hasattr(self._cached_content_analyzer, "close"):
                    self._cached_content_analyzer.close()
                del self._cached_content_analyzer
                self._cached_content_analyzer = None
            if hasattr(self, "_cached_audio_analyzer") and self._cached_audio_analyzer:
                if hasattr(self._cached_audio_analyzer, "cleanup"):
                    self._cached_audio_analyzer.cleanup()
                del self._cached_audio_analyzer
                self._cached_audio_analyzer = None
            gc.collect()
            logger.debug("Pipeline resources cleaned up")
        except Exception:
            pass

    def _run_unified_analysis(
        self,
        clip: VideoClipInfo,
        analysis_video: Path,
        original_video: Path,
        video_duration: float,
    ) -> tuple[float, float, float, dict[str, object] | None]:
        """Run unified audio-aware analysis."""
        from immich_memories.analysis.scoring import SceneScorer
        from immich_memories.analysis.unified_analyzer import UnifiedSegmentAnalyzer
        from immich_memories.config import get_config

        config = get_config()
        content_analyzer, content_weight = self._init_content_analyzer()

        # Cache audio analyzer at pipeline level — PANNs model is ~312MB,
        # must NOT be reloaded per clip
        audio_analyzer = self._get_cached_audio_analyzer()

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
            audio_analyzer=audio_analyzer,
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

            # Store audio categories on clip for UI display
            if best_segment.audio_categories:
                clip.audio_categories = sorted(best_segment.audio_categories)

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

            # Save SegmentAnalysis objects directly (not MomentScore)
            # so LLM fields and audio_categories are persisted to the DB
            self.analysis_cache.save_analysis(
                asset=clip.asset,
                video_info=clip,
                perceptual_hash=None,
                segments=segments,
            )

            logger.info(
                f"Unified analysis: segment {start:.1f}s - {end:.1f}s "
                f"(score={score:.2f}, cut_quality={best_segment.cut_quality:.2f})"
            )

            del segments
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
