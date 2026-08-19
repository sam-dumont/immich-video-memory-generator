"""Clip analysis service: downloading, analyzing, and scoring video clips."""

from __future__ import annotations

import contextlib
import gc
import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, cast

from immich_memories.analysis.cache_projection import (
    apply_cached_segment,
    apply_semantic_payload,
    is_compatible_analysis_cache,
)
from immich_memories.processing.downscaler import cleanup_downscaled
from immich_memories.security import sanitize_filename

if TYPE_CHECKING:
    from immich_memories.analysis.llm_response_parser import ContentAnalyzer
    from immich_memories.analysis.preview_builder import PreviewBuilder
    from immich_memories.analysis.progress import ProgressTracker
    from immich_memories.analysis.scoring import SceneScorer
    from immich_memories.analysis.smart_pipeline import ClipWithSegment, PipelineConfig
    from immich_memories.analysis.unified_analyzer import UnifiedSegmentAnalyzer
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.audio.content_analyzer import AudioContentAnalyzer
    from immich_memories.cache.database import VideoAnalysisCache
    from immich_memories.cache.video_cache import CacheBatch, VideoDownloadCache
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
        *,
        app_config: Config,
        video_cache: VideoDownloadCache | None = None,
        provider_circuit=None,
    ):
        self.config = config
        self.client = client
        self.analysis_cache = analysis_cache
        self.preview_builder = preview_builder
        self._app_config = app_config
        self._provider_circuit = provider_circuit
        self._video_cache = video_cache
        self._cache_batch: CacheBatch | None = None
        cache_config = app_config.cache
        if (
            self._video_cache is None
            and cache_config.video_cache_enabled
            and isinstance(cache_config.video_cache_path, Path)
        ):
            from immich_memories.cache.video_cache import VideoDownloadCache

            self._video_cache = VideoDownloadCache(
                cache_dir=cache_config.video_cache_path,
                max_size_gb=cache_config.video_cache_max_size_gb,
                max_age_days=cache_config.video_cache_max_age_days,
            )
        self._cached_content_analyzer: ContentAnalyzer | None = None
        self._cached_audio_analyzer: AudioContentAnalyzer | None = None
        self._cached_scene_scorer: SceneScorer | None = None
        self._cached_unified_analyzer: UnifiedSegmentAnalyzer | None = None
        bind_legacy_provider = getattr(self.preview_builder, "bind_legacy_analyzer_provider", None)
        if bind_legacy_provider is not None:
            bind_legacy_provider(self._get_unified_analyzer)

    def bind_cache_batch(self, batch: CacheBatch | None) -> None:
        """Use the SmartPipeline-owned batch for every cache download in this run."""
        self._cache_batch = batch

    def phase_analyze(
        self,
        clips: list[VideoClipInfo],
        tracker: ProgressTracker,
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

                apply_semantic_payload(clip, llm_analysis)

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

            except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as e:
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

        tracker.complete_phase()

        logger.info(f"Phase 3: Analyzed {len(results)} clips")

        from immich_memories.analysis.content_analyzer import ContentAnalyzer

        ContentAnalyzer.log_session_summary()

        return results

    def phase_plan_cached(
        self,
        clips: list[VideoClipInfo],
        tracker: ProgressTracker,
    ) -> list[ClipWithSegment]:
        """Use cached analysis and metadata fallbacks without downloading source videos."""
        from immich_memories.analysis.progress import PipelinePhase

        valid_clips = [c for c in clips if (c.duration_seconds or 0) >= 1.5]
        tracker.start_phase(PipelinePhase.ANALYZING, len(valid_clips))
        results: list[ClipWithSegment] = []

        # Probe once so a dry-run still validates the configured optional model/route.
        self._init_content_analyzer()
        for clip in valid_clips:
            tracker.start_item(
                clip.asset.original_file_name or clip.asset.id[:8], asset_id=clip.asset.id
            )
            planned = self._plan_cached_or_metadata_clip(clip)
            results.append(planned)
            tracker.complete_item(
                clip.asset.id,
                video_duration=clip.duration_seconds,
                segment=(planned.start_time, planned.end_time),
                score=planned.score,
            )

        tracker.complete_phase()
        logger.info("Planning analysis: %d cached/metadata clips", len(results))
        return results

    def plan_cached_or_metadata(
        self,
        clips: list[VideoClipInfo],
    ) -> list[ClipWithSegment]:
        """Plan local fallback segments without downloads or provider health probes."""
        valid_clips = [c for c in clips if (c.duration_seconds or 0) >= 1.5]
        results = [self._plan_cached_or_metadata_clip(clip) for clip in valid_clips]
        logger.info("Preserved %d cached/metadata fallback clips", len(results))
        return results

    def _plan_cached_or_metadata_clip(self, clip: VideoClipInfo) -> ClipWithSegment:
        """Build one segment from cache, falling back to local metadata."""
        from immich_memories.analysis.smart_pipeline import ClipWithSegment

        cached = self._check_analysis_cache(clip)
        if cached is None:
            start = 0.0
            end = min(clip.duration_seconds, self.config.avg_clip_duration)
            score = clip.quality_score
        else:
            start, end, score, _preview_path, llm_analysis = cached
            apply_semantic_payload(clip, llm_analysis)
        return ClipWithSegment(clip=clip, start_time=start, end_time=end, score=score)

    def _check_analysis_cache(
        self,
        clip: VideoClipInfo,
    ) -> tuple[float, float, float, str | None, dict[str, object] | None] | None:
        """Check if analysis is cached and return it."""
        cached = self.analysis_cache.get_analysis(clip.asset.id)
        if not (cached and cached.segments and len(cached.segments) > 0):
            return None

        if not is_compatible_analysis_cache(cached, self._app_config):
            logger.info(
                "Ignoring semantic cache for %s: cached model=%s, configured model=%s",
                clip.asset.id,
                cached.model_version or "none",
                self._app_config.llm.model,
            )
            return None

        best = max(cached.segments, key=lambda s: s.total_score or 0.0)
        start, end, score = best.start_time, best.end_time, best.total_score or 0.0

        cached_llm_analysis = apply_cached_segment(clip, best)

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

        config = self._app_config
        temp_file: Path | None = None

        if config.cache.video_cache_enabled:
            video_cache = self._cache_batch or self._video_cache
            if video_cache is None:
                raise RuntimeError("Video cache is enabled but unavailable")
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
        config = self._app_config
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
                circuit=self._provider_circuit,
            )
            weight = config.content_analysis.weight
            if analyzer:
                health = analyzer.check_health()
                if not health.available:
                    logger.warning("Content analysis disabled for this run: %s", health.message)
                    analyzer.close()
                    return None, 0.0
                logger.info(
                    f"LLM content analysis enabled "
                    f"(provider={config.llm.provider}, weight={weight:.0%})"
                )
                self._cached_content_analyzer = analyzer
            else:
                logger.warning("Content analysis enabled but no analyzer available")
            return analyzer, weight
        except (ImportError, RuntimeError, ValueError) as e:
            logger.warning(f"Failed to initialize content analyzer: {e}")
            return None, 0.0

    def _get_cached_audio_analyzer(self) -> object | None:
        """Get or create a pipeline-level cached AudioContentAnalyzer."""
        config = self._app_config
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
        except (ImportError, RuntimeError, OSError) as e:
            logger.warning(f"Failed to create audio analyzer: {e}")
            return None

    def _cleanup_analyzer(
        self, unified_analyzer: object | None, content_analyzer: object | None = None
    ) -> None:
        """Reset per-video analyzer state while retaining reusable services."""
        with contextlib.suppress(Exception):
            if unified_analyzer is not None:
                unified_analyzer.reset_for_video()

    def close(self) -> None:
        """Release reusable native/model resources once after an analysis batch."""
        unified_analyzer = self._cached_unified_analyzer
        self._cached_unified_analyzer = None
        self._cached_scene_scorer = None
        content_analyzer = self._cached_content_analyzer
        self._cached_content_analyzer = None
        audio_analyzer = self._cached_audio_analyzer
        self._cached_audio_analyzer = None

        with contextlib.suppress(Exception):
            if unified_analyzer is not None:
                unified_analyzer.reset_for_video()
        with contextlib.suppress(Exception):
            if unified_analyzer is not None:
                unified_analyzer.clear_cache()
        with contextlib.suppress(Exception):
            if content_analyzer is not None and hasattr(content_analyzer, "close"):
                content_analyzer.close()
        with contextlib.suppress(Exception):
            if audio_analyzer is not None and hasattr(audio_analyzer, "cleanup"):
                audio_analyzer.cleanup()
        try:
            gc.collect()
        finally:
            logger.debug("Pipeline resources cleaned up")

    def _run_unified_analysis(
        self,
        clip: VideoClipInfo,
        analysis_video: Path,
        original_video: Path,
        video_duration: float,
    ) -> tuple[float, float, float, dict[str, object] | None]:
        """Run unified audio-aware analysis."""
        unified_analyzer = self._get_unified_analyzer()

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
                    "category": best_segment.llm_category,
                    "emotion": best_segment.llm_emotion,
                    "setting": best_segment.llm_setting,
                    "subjects": best_segment.llm_subjects,
                    "interestingness": best_segment.llm_interestingness,
                    "quality": best_segment.llm_quality,
                }

            has_semantic_analysis = any(
                isinstance(confidence := getattr(segment, "llm_confidence", None), (int, float))
                and float(confidence) >= self._app_config.content_analysis.min_confidence
                for segment in segments
            )

            self.analysis_cache.save_analysis(
                asset=clip.asset,
                video_info=clip,
                perceptual_hash=None,
                segments=segments,
                model_version=(
                    self._app_config.llm.model or None if has_semantic_analysis else None
                ),
            )

            logger.info(
                f"Unified analysis: segment {start:.1f}s - {end:.1f}s "
                f"(score={score:.2f}, cut_quality={best_segment.cut_quality:.2f})"
            )

            del segments
            return start, end, score, llm_analysis
        finally:
            self._cleanup_analyzer(unified_analyzer)

    def _get_unified_analyzer(self) -> UnifiedSegmentAnalyzer:
        """Create the immutable-config analysis services once for this batch."""
        if self._cached_unified_analyzer is not None:
            return self._cached_unified_analyzer

        from immich_memories.analysis.analyzer_factory import analyzer_kwargs_from_config
        from immich_memories.analysis.scoring import SceneScorer
        from immich_memories.analysis.unified_analyzer import UnifiedSegmentAnalyzer

        config = self._app_config
        content_analyzer, content_weight = self._init_content_analyzer()
        audio_analyzer = self._get_cached_audio_analyzer()
        self._cached_scene_scorer = SceneScorer(
            content_analysis_config=config.content_analysis,
            analysis_config=config.analysis,
        )
        self._cached_unified_analyzer = UnifiedSegmentAnalyzer(
            scorer=self._cached_scene_scorer,
            content_analyzer=content_analyzer,
            content_weight=content_weight,
            audio_analyzer=audio_analyzer,
            **analyzer_kwargs_from_config(config),
        )
        return self._cached_unified_analyzer

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
            except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as e:
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

        config = self._app_config
        analysis_video: Path | None = None
        original_video: Path | None = None
        temp_file: Path | None = None

        try:
            analysis_video, original_video, temp_file = self._download_analysis_video(clip)
            video_duration = clip.duration_seconds or 30

            # Fast mode: only favorites get LLM analysis, gap-fillers use legacy scoring
            use_llm = config.analysis.use_unified_analysis
            if self.config.analysis_depth == "fast" and not clip.asset.is_favorite:
                use_llm = False
                logger.debug(f"Fast mode: skipping LLM for non-favorite {clip.asset.id[:8]}")

            start, end, score, llm_analysis = self._run_analysis_with_fallback(
                clip,
                analysis_video,
                original_video,
                video_duration,
                use_unified=use_llm,
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
