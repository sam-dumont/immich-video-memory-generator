"""Preview extraction and legacy analysis service for the smart pipeline."""

from __future__ import annotations

import contextlib
import logging
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.processing.hardware import fast_encoder_args
from immich_memories.security import sanitize_filename

if TYPE_CHECKING:
    from immich_memories.analysis.smart_pipeline import PipelineConfig
    from immich_memories.analysis.unified_analyzer import UnifiedSegmentAnalyzer
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.cache.database import VideoAnalysisCache
    from immich_memories.cache.video_cache import CacheBatch, VideoDownloadCache
    from immich_memories.config_models import AnalysisConfig, CacheConfig, ContentAnalysisConfig

logger = logging.getLogger(__name__)


# Previews are now ~10 MB rather than ~72 MB, and they are reused across runs
# instead of being rebuilt. A cap of 20 would evict a library's previews on
# every pass and put the re-encode straight back. Real cache eviction is a
# separate concern (see the cache page).
_MAX_PREVIEWS = 200


def _evict_oldest_previews(preview_dir: Path) -> None:
    existing = sorted(preview_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    for stale in existing[:-_MAX_PREVIEWS]:
        with contextlib.suppress(OSError):
            stale.unlink()


class PreviewBuilder:
    """Extracts preview segments and runs legacy analysis."""

    def __init__(
        self,
        client: SyncImmichClient,
        *,
        cache_config: CacheConfig,
        analysis_config: AnalysisConfig,
        content_analysis_config: ContentAnalysisConfig,
        video_cache: VideoDownloadCache | None = None,
        hardware_enabled: bool = True,
    ):
        self.client = client
        self._cache_config = cache_config
        self._analysis_config = analysis_config
        self._content_analysis_config = content_analysis_config
        self._video_cache = video_cache
        self._hardware_enabled = hardware_enabled
        self._cache_batch: CacheBatch | None = None
        self._legacy_analyzer: UnifiedSegmentAnalyzer | None = None
        self._owns_legacy_analyzer = False
        self._legacy_analyzer_provider: Callable[[], UnifiedSegmentAnalyzer] | None = None

    def bind_cache_batch(self, batch: CacheBatch | None) -> None:
        """Use the SmartPipeline-owned batch for download requests in this run."""
        self._cache_batch = batch

    def bind_legacy_analyzer(self, analyzer: UnifiedSegmentAnalyzer | None) -> None:
        """Bind ClipAnalyzer's reusable service for legacy fallback analysis."""
        self._release_owned_legacy_analyzer()
        self._legacy_analyzer = analyzer
        self._owns_legacy_analyzer = False
        self._legacy_analyzer_provider = None

    def bind_legacy_analyzer_provider(
        self, provider: Callable[[], UnifiedSegmentAnalyzer] | None
    ) -> None:
        """Use the ClipAnalyzer-owned service even when legacy analysis runs first."""
        self._release_owned_legacy_analyzer()
        self._legacy_analyzer = None
        self._legacy_analyzer_provider = provider

    def _release_owned_legacy_analyzer(self) -> None:
        if self._owns_legacy_analyzer and self._legacy_analyzer is not None:
            with contextlib.suppress(Exception):
                self._legacy_analyzer.reset_for_video()
            with contextlib.suppress(Exception):
                self._legacy_analyzer.clear_cache(release_audio_analyzer=True)
        self._owns_legacy_analyzer = False

    def find_cached_preview(self, asset_id: str, start: float, end: float) -> str | None:
        """Find or build a preview for a cached clip from the video cache."""
        c_config = self._cache_config

        preview_cache_dir = c_config.cache_path / "preview-cache"
        stable_preview = preview_cache_dir / f"{asset_id}.mp4"
        if stable_preview.exists():
            return str(stable_preview)

        pipeline_preview_dir = c_config.cache_path / "previews"
        for p in pipeline_preview_dir.glob(f"*{asset_id[:8]}*"):
            if p.exists():
                return str(p)

        video_cache_dir = c_config.video_cache_path
        if not video_cache_dir.exists():
            return None

        subdir = asset_id[:2] if len(asset_id) >= 2 else "00"
        sub_path = video_cache_dir / subdir
        if not sub_path.exists():
            return None

        source = None
        for pattern in (f"{asset_id}_480p.*", f"{asset_id}.*"):
            matches = list(sub_path.glob(pattern))
            if matches:
                source = matches[0]
                break

        if source is None:
            return None

        try:
            preview_path = self.extract_preview_segment(source, start, end, asset_id=asset_id)
            if preview_path and Path(preview_path).exists():
                logger.debug(f"Built preview for cached clip {asset_id}")
                return preview_path
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            logger.debug(f"Could not build preview for cached {asset_id}: {e}")

        return None

    def download_clip_video(self, clip: VideoClipInfo) -> tuple[Path, Path | None]:
        """Download clip video, returning (video_path, temp_file_or_None)."""
        c_config = self._cache_config
        if c_config.video_cache_enabled:
            video_cache = self._cache_batch or self._video_cache
            if video_cache is None:
                from immich_memories.cache.video_cache import VideoDownloadCache

                # Standalone PreviewBuilder callers retain bounded one-off cache
                # semantics; SmartPipeline always injects its shared instance.
                video_cache = VideoDownloadCache(
                    cache_dir=c_config.video_cache_path,
                    max_size_gb=c_config.video_cache_max_size_gb,
                    max_age_days=c_config.video_cache_max_age_days,
                )
            video_path = video_cache.download_or_get(self.client, clip.asset)
            return video_path, None

        safe_name = sanitize_filename(clip.asset.original_file_name or "video.mp4")
        suffix = Path(safe_name).suffix or ".mp4"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            temp_file = Path(tmp.name)
        self.client.download_asset(clip.asset.id, temp_file)
        return temp_file, temp_file

    def run_legacy_analysis(
        self,
        clip: VideoClipInfo,
        analysis_video: Path,
        original_video: Path | None,
        video_duration: float,
        config: PipelineConfig,
        analysis_cache: VideoAnalysisCache,
    ) -> tuple[float, float, float]:
        """Run legacy analysis using a bound or standalone reusable analyzer."""
        from immich_memories.analysis.scoring import SceneScorer
        from immich_memories.analysis.unified_analyzer import UnifiedSegmentAnalyzer
        from immich_memories.config_models import AudioContentConfig

        a_config = self._analysis_config
        min_segment = a_config.min_segment_duration
        max_segment = a_config.max_segment_duration

        analyzer = self._legacy_analyzer
        if analyzer is None:
            if self._legacy_analyzer_provider is not None:
                analyzer = self._legacy_analyzer_provider()
            else:
                scorer = SceneScorer(
                    content_analysis_config=self._content_analysis_config,
                    analysis_config=a_config,
                )
                analyzer = UnifiedSegmentAnalyzer(
                    scorer=scorer,
                    min_segment_duration=min_segment,
                    max_segment_duration=max_segment,
                    audio_content_config=AudioContentConfig(),
                    analysis_config=a_config,
                )
                self._owns_legacy_analyzer = True
            self._legacy_analyzer = analyzer

        try:
            segments = analyzer.analyze(
                analysis_video,
                video_duration=video_duration,
                enable_content_analysis=False,
                enable_audio_content_analysis=False,
            )

            if not segments:
                duration = clip.duration_seconds or 10
                return 0.0, min(duration, config.avg_clip_duration), 0.0

            best = segments[0]  # Sorted by score, best first
            segment_duration = max(min_segment, min(best.end_time - best.start_time, max_segment))

            start = best.start_time
            end = start + segment_duration
            score = best.total_score

            if end > video_duration:
                end = video_duration
                start = max(0, end - segment_duration)

            moments = [seg.to_moment_score() for seg in segments]
            analysis_cache.save_analysis(
                asset=clip.asset, video_info=clip, perceptual_hash=None, segments=moments
            )

            return start, end, score
        finally:
            analyzer.reset_for_video()

    def close(self) -> None:
        """Release only standalone legacy resources; bound services belong to ClipAnalyzer."""
        self._release_owned_legacy_analyzer()
        self._legacy_analyzer = None
        self._legacy_analyzer_provider = None

    def extract_and_log_preview(
        self,
        clip: VideoClipInfo,
        original_video: Path | None,
        analysis_video: Path,
        start: float,
        end: float,
    ) -> str | None:
        """Extract preview segment for UI display."""
        try:
            # The preview is a UI thumbnail and the proxy is already built:
            # encoding it from the 4K original cost 3.4s and 72MB per clip
            # against 0.4s and 10MB from the 480p proxy.
            preview_source = analysis_video or original_video
            logger.info(f"Extracting preview for {clip.asset.id}: {start:.1f}s - {end:.1f}s")
            preview_path = self.extract_preview_segment(
                preview_source, start, end, asset_id=clip.asset.id
            )
            if preview_path and Path(preview_path).exists():
                file_size = Path(preview_path).stat().st_size
                logger.info(f"Preview extracted: {preview_path} ({file_size / 1024:.1f} KB)")
                return preview_path
            logger.warning(f"Preview file not created for {clip.asset.id}")
            return None
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            logger.warning(f"Failed to extract preview for {clip.asset.id}: {e}")
            return None

    def extract_preview_segment(
        self,
        video_path: Path,
        start: float,
        end: float,
        min_duration: float = 2.0,
        max_duration: float = 15.0,
        asset_id: str | None = None,
    ) -> str:
        """Extract a preview segment from a video using ffmpeg."""
        import subprocess
        import time

        preview_dir = self._cache_config.cache_path / "previews"
        preview_dir.mkdir(parents=True, exist_ok=True)
        _evict_oldest_previews(preview_dir)

        # Named after the asset because find_cached_preview looks previews up by
        # it. The previous `preview_<ms-timestamp>.mp4` could never be matched,
        # so every clip re-encoded a preview it already had. Re-analysing an
        # asset overwrites its preview, which is what the reader wants: one
        # current preview per asset.
        stem = asset_id or f"preview_{int(time.time() * 1000)}"
        preview_path = str(preview_dir / f"{stem}.mp4")

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
        except (OSError, subprocess.SubprocessError, ValueError):
            video_duration = 60.0

        segment_duration = end - start

        if segment_duration < min_duration:
            extension = (min_duration - segment_duration) / 2
            start = start - extension
            end = end + extension
            segment_duration = min_duration

        if segment_duration > max_duration:
            end = start + max_duration
            segment_duration = max_duration

        if start < 0:
            end = end - start
            start = 0
        if end > video_duration:
            start = max(0, start - (end - video_duration))
            end = video_duration

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

        encoder_args = fast_encoder_args(hardware_enabled=self._hardware_enabled)

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
            "0:v:0",
            "-map",
            "0:a:0?",
            *encoder_args,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-threads",
            "2",
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
