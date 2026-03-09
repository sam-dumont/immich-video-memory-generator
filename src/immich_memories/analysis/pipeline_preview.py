"""Preview extraction and legacy analysis mixin for the smart pipeline.

Contains methods for extracting preview segments and the legacy single-clip analysis.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.analysis.clip_selection import (
    _get_fast_encoder_args,
)
from immich_memories.security import sanitize_filename

if TYPE_CHECKING:
    from immich_memories.api.models import VideoClipInfo

logger = logging.getLogger(__name__)


class PreviewMixin:
    """Mixin providing preview extraction and legacy analysis methods for SmartPipeline."""

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
                safe_name = sanitize_filename(clip.asset.original_file_name or "video.mp4")
                suffix = Path(safe_name).suffix or ".mp4"
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

    def _run_legacy_analysis(
        self,
        clip: VideoClipInfo,
        analysis_video: Path,
        original_video: Path | None,
        video_duration: float,
    ) -> tuple[float, float, float]:
        """Run legacy visual scoring with silence adjustment.

        Args:
            clip: Clip being analyzed.
            analysis_video: Video to analyze.
            original_video: Original video for audio analysis.
            video_duration: Duration of the video.

        Returns:
            Tuple of (start, end, score).
        """
        import gc

        from immich_memories.analysis.scoring import SceneScorer
        from immich_memories.config import get_config

        config = get_config()
        min_segment = config.analysis.min_segment_duration
        max_segment = config.analysis.max_segment_duration

        scorer = SceneScorer()
        moments = scorer.sample_and_score_video(
            analysis_video,
            segment_duration=self.config.segment_duration,
            overlap=0.5,
            sample_frames=5,
        )

        if not moments:
            duration = clip.duration_seconds or 10
            return 0.0, min(duration, self.config.avg_clip_duration), 0.0

        best_moment = max(moments, key=lambda m: m.total_score)
        segment_duration = max(min_segment, min(best_moment.duration, max_segment))

        start = best_moment.start_time
        end = start + segment_duration
        score = best_moment.total_score

        if end > video_duration:
            end = video_duration
            start = max(0, end - segment_duration)

        # Try to adjust boundaries to silence gaps
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

        self.analysis_cache.save_analysis(
            asset=clip.asset, video_info=clip, perceptual_hash=None, segments=moments
        )

        del moments
        scorer.release_capture()
        del scorer
        gc.collect()

        return start, end, score

    def _extract_and_log_preview(
        self,
        clip: VideoClipInfo,
        original_video: Path | None,
        analysis_video: Path,
        start: float,
        end: float,
    ) -> str | None:
        """Extract preview segment for UI display.

        Args:
            clip: Clip being analyzed.
            original_video: Original video path (preferred for quality).
            analysis_video: Fallback video path.
            start: Segment start time.
            end: Segment end time.

        Returns:
            Path to preview file, or None.
        """
        try:
            preview_source = original_video or analysis_video
            logger.info(f"Extracting preview for {clip.asset.id}: {start:.1f}s - {end:.1f}s")
            preview_path = self._extract_preview_segment(
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

        # Note: no explicit HDR→SDR tonemapping here. macOS browsers (Safari, Chrome)
        # handle HLG/PQ natively via the system display pipeline, so keeping the HDR
        # metadata intact produces better results than software tonemapping.

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
