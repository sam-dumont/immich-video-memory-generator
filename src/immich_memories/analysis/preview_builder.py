"""Preview extraction and legacy analysis service for the smart pipeline."""

from __future__ import annotations

import contextlib
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.analysis.clip_selection import _get_fast_encoder_args
from immich_memories.security import sanitize_filename

if TYPE_CHECKING:
    from immich_memories.analysis.smart_pipeline import PipelineConfig
    from immich_memories.api.immich import SyncImmichClient
    from immich_memories.api.models import VideoClipInfo
    from immich_memories.cache.database import VideoAnalysisCache
    from immich_memories.config_models import AnalysisConfig, CacheConfig, ContentAnalysisConfig

logger = logging.getLogger(__name__)


class PreviewBuilder:
    """Extracts preview segments and runs legacy analysis."""

    def __init__(
        self,
        client: SyncImmichClient,
        *,
        cache_config: CacheConfig,
        analysis_config: AnalysisConfig,
        content_analysis_config: ContentAnalysisConfig,
    ):
        self.client = client
        self._cache_config = cache_config
        self._analysis_config = analysis_config
        self._content_analysis_config = content_analysis_config

    def find_cached_preview(self, asset_id: str, start: float, end: float) -> str | None:
        """Find or build a preview for a cached clip from the video cache."""
        c_config = self._cache_config

        preview_cache_dir = c_config.cache_path / "preview-cache"
        stable_preview = preview_cache_dir / f"{asset_id}.mp4"
        if stable_preview.exists():
            return str(stable_preview)

        pipeline_preview_dir = Path.home() / ".cache" / "immich-memories" / "previews"
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
        except Exception as e:
            logger.debug(f"Could not build preview for cached {asset_id}: {e}")

        return None

    def download_clip_video(self, clip: VideoClipInfo) -> tuple[Path, Path | None]:
        """Download clip video, returning (video_path, temp_file_or_None)."""
        from immich_memories.cache.video_cache import VideoDownloadCache

        c_config = self._cache_config
        if c_config.video_cache_enabled:
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
        """Run legacy visual scoring with silence adjustment."""
        import gc

        from immich_memories.analysis.scoring import SceneScorer

        a_config = self._analysis_config
        min_segment = a_config.min_segment_duration
        max_segment = a_config.max_segment_duration

        scorer = SceneScorer(
            content_analysis_config=self._content_analysis_config,
            analysis_config=a_config,
        )
        moments = scorer.sample_and_score_video(
            analysis_video,
            segment_duration=config.segment_duration,
            overlap=0.5,
            sample_frames=5,
        )

        if not moments:
            duration = clip.duration_seconds or 10
            return 0.0, min(duration, config.avg_clip_duration), 0.0

        best_moment = max(moments, key=lambda m: m.total_score)
        segment_duration = max(min_segment, min(best_moment.duration, max_segment))

        start = best_moment.start_time
        end = start + segment_duration
        score = best_moment.total_score

        if end > video_duration:
            end = video_duration
            start = max(0, end - segment_duration)

        try:
            from immich_memories.analysis.silence_detection import (
                adjust_segment_to_silence,
                detect_silence_gaps,
            )

            silence_gaps = detect_silence_gaps(original_video or analysis_video)
            if silence_gaps:
                start, end = adjust_segment_to_silence(
                    start, end, silence_gaps, max_adjustment=1.0, min_duration=min_segment
                )
                logger.debug(f"Adjusted segment to silence: {start:.1f}s - {end:.1f}s")
        except Exception as e:
            logger.debug(f"Silence detection skipped: {e}")

        analysis_cache.save_analysis(
            asset=clip.asset, video_info=clip, perceptual_hash=None, segments=moments
        )

        del moments
        scorer.release_capture()
        del scorer
        gc.collect()

        return start, end, score

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
            preview_source = original_video or analysis_video
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
        except Exception as e:
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

        preview_dir = Path.home() / ".cache" / "immich-memories" / "previews"
        preview_dir.mkdir(parents=True, exist_ok=True)

        MAX_PREVIEWS = 20
        existing_previews = sorted(preview_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
        if len(existing_previews) > MAX_PREVIEWS:
            for old_preview in existing_previews[:-MAX_PREVIEWS]:
                with contextlib.suppress(Exception):
                    old_preview.unlink()

        timestamp = int(time.time() * 1000)
        preview_path = str(preview_dir / f"preview_{timestamp}.mp4")

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
