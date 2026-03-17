"""Clip analysis service: downloading, analyzing, and scoring video clips."""

from __future__ import annotations

import contextlib
import gc
import logging
from pathlib import Path
from typing import TYPE_CHECKING, cast

from immich_memories.processing.downscaler import cleanup_downscaled
from immich_memories.security import sanitize_filename

if TYPE_CHECKING:
    from immich_memories.analysis.llm_response_parser import ContentAnalyzer
    from immich_memories.analysis.preview_builder import PreviewBuilder
    from immich_memories.analysis.progress import ProgressTracker
    from immich_memories.analysis.smart_pipeline import ClipWithSegment, PipelineConfig
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.audio.content_analyzer import AudioContentAnalyzer
    from immich_memories.cache.database import VideoAnalysisCache
    from immich_memories.config_loader import Config

logger = logging.getLogger(__name__)


class ClipAnalyzer:
    """Downloads, analyzes, and scores video clips for optimal segments."""

    def __init__(
        self,
        config: PipelineConfig,
        client: SyncImmichClient,
        analysis_cache: VideoAnalysisCache,
        preview_builder: PreviewBuilder,
        app_config: Config | None = None,
    ):
        self.config = config
        self.client = client
        self.analysis_cache = analysis_cache
        self.preview_builder = preview_builder
        self._app_config = app_config
        self._cached_content_analyzer: ContentAnalyzer | None = None
        self._cached_audio_analyzer: AudioContentAnalyzer | None = None

    def _get_app_config(self) -> Config:
        """Get the app config, falling back to get_config() if not injected."""
        if self._app_config is None:
            from immich_memories.config import get_config

            self._app_config = get_config()
        return self._app_config

    def phase_analyze(
        self,
        clips: list[VideoClipInfo],
        tracker: ProgressTracker,
        check_cancelled: object,
    ) -> list[ClipWithSegment]:
        """Phase 3: Analyze clips for best segments."""
        from immich_memories.analysis.progress import PipelinePhase
        from immich_memories.analysis.smart_pipeline import ClipWithSegment

        MIN_DURATION = 1.5
        valid_clips = [c for c in clips if (c.duration_seconds or 0) >= MIN_DURATION]
        skipped = len(clips) - len(valid_clips)
        if skipped > 0:
            logger.info(f"Skipping {skipped} clips shorter than {MIN_DURATION}s")

        tracker.start_phase(PipelinePhase.ANALYZING, len(valid_clips))

        results: list[ClipWithSegment] = []

        for clip in valid_clips:
            name = clip.asset.original_file_name or clip.asset.id[:8]
            tracker.start_item(name, asset_id=clip.asset.id)

            try:
                start, end, score, preview_path, llm_analysis = self._analyze_clip_with_preview(
                    clip
                )

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
                tracker.complete_item(
                    clip.asset.id,
                    video_duration=clip.duration_seconds,
                    segment=(start, end),
                    score=score,
                    preview_path=preview_path,
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

            except Exception as e:
                error_msg = str(e)
                logger.error(f"Failed to analyze {clip.asset.id}: {error_msg}")
                tracker.complete_item(clip.asset.id, success=False, error=error_msg)

                duration = clip.duration_seconds or 10
                results.append(
                    ClipWithSegment(
                        clip=clip,
                        start_time=0.0,
                        end_time=min(duration, self.config.avg_clip_duration),
                        score=0.0,
                    )
                )

            gc.collect()
            check_cancelled()

        tracker.complete_phase()
        self._cleanup_pipeline_resources()

        logger.info(f"Phase 3: Analyzed {len(results)} clips")

        from immich_memories.analysis.content_analyzer import ContentAnalyzer

        ContentAnalyzer.log_session_summary()

        return results

    def _check_analysis_cache(
        self,
        clip: VideoClipInfo,
    ) -> tuple[float, float, float, str | None, dict[str, object] | None] | None:
        """Check if analysis is cached and return it."""
        cached = self.analysis_cache.get_analysis(clip.asset.id)
        if not (cached and cached.segments and len(cached.segments) > 0):
            return None

        best = max(cached.segments, key=lambda s: s.total_score or 0.0)
        start, end, score = best.start_time, best.end_time, best.total_score or 0.0

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

        if best.audio_categories:
            clip.audio_categories = list(best.audio_categories)

        preview_path = self.preview_builder.find_cached_preview(clip.asset.id, start, end)

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

        config = self._get_app_config()
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
            download_id = clip.asset.live_photo_video_id or clip.asset.id
            self.client.download_asset(download_id, temp_file)
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
        """Get or create cached LLM content analyzer."""
        config = self._get_app_config()
        if not config.content_analysis.enabled:
            return None, 0.0

        if self._cached_content_analyzer:
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
        """Get or create a pipeline-level cached AudioContentAnalyzer."""
        config = self._get_app_config()
        if not config.audio_content.enabled:
            return None

        if self._cached_audio_analyzer:
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
        """Clean up analyzer resources to prevent OOM."""
        with contextlib.suppress(Exception):
            if unified_analyzer is not None:
                unified_analyzer.clear_cache()
                unified_analyzer.scorer.release_capture()
                if hasattr(unified_analyzer, "_audio_analyzer"):
                    unified_analyzer._audio_analyzer = None
                del unified_analyzer
            gc.collect()

    def _cleanup_pipeline_resources(self) -> None:
        """Clean up long-lived pipeline resources after analysis phase."""
        with contextlib.suppress(Exception):
            if self._cached_content_analyzer:
                if hasattr(self._cached_content_analyzer, "close"):
                    self._cached_content_analyzer.close()
                del self._cached_content_analyzer
                self._cached_content_analyzer = None
            if self._cached_audio_analyzer:
                if hasattr(self._cached_audio_analyzer, "cleanup"):
                    self._cached_audio_analyzer.cleanup()
                del self._cached_audio_analyzer
                self._cached_audio_analyzer = None
            gc.collect()
            logger.debug("Pipeline resources cleaned up")

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

        config = self._get_app_config()
        content_analyzer, content_weight = self._init_content_analyzer()
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

    def _run_analysis_with_fallback(
        self,
        clip: VideoClipInfo,
        analysis_video: Path,
        original_video: Path,
        video_duration: float,
        use_unified: bool,
    ) -> tuple[float, float, float, dict[str, object] | None]:
        """Run unified analysis with legacy fallback, returning (start, end, score, llm)."""
        start, end, score = 0.0, 0.0, 0.0
        llm_analysis: dict[str, object] | None = None

        if use_unified:
            try:
                start, end, score, llm_analysis = self._run_unified_analysis(
                    clip, analysis_video, original_video, video_duration
                )
            except Exception as e:
                logger.warning(f"Unified analysis failed: {e}, using legacy approach")

        if score == 0.0:
            start, end, score = self.preview_builder.run_legacy_analysis(
                clip,
                analysis_video,
                original_video,
                video_duration,
                self.config,
                self.analysis_cache,
            )

        return start, end, score, llm_analysis

    def _analyze_clip_with_preview(
        self,
        clip: VideoClipInfo,
    ) -> tuple[float, float, float, str | None, dict[str, object] | None]:
        """Analyze a clip and extract a preview segment."""
        cached_result = self._check_analysis_cache(clip)
        if cached_result is not None:
            return cached_result

        config = self._get_app_config()
        analysis_video: Path | None = None
        original_video: Path | None = None
        temp_file: Path | None = None

        try:
            analysis_video, original_video, temp_file = self._download_analysis_video(clip)
            video_duration = clip.duration_seconds or 30

            start, end, score, llm_analysis = self._run_analysis_with_fallback(
                clip,
                analysis_video,
                original_video,
                video_duration,
                use_unified=config.analysis.use_unified_analysis,
            )

            if start == 0.0 and end > 0.0 and score == 0.0:
                return start, end, score, None, None

            preview_path = self.preview_builder.extract_and_log_preview(
                clip, original_video, analysis_video, start, end
            )
            return start, end, score, preview_path, llm_analysis

        finally:
            if temp_file:
                with contextlib.suppress(Exception):
                    temp_file.unlink(missing_ok=True)
            if analysis_video and original_video and analysis_video != original_video:
                with contextlib.suppress(Exception):
                    cleanup_downscaled(original_video)
                    logger.debug(f"Cleaned up downscaled video for: {original_video.name}")
            gc.collect()
