"""Video assembler class and convenience functions.

This module provides the VideoAssembler class for combining video clips into a
final memory video with title screens, transitions, and audio.

Assembly Pipeline:
    1. Generate title screen (GPU-accelerated if available)
    2. Process video clips (normalize resolution, framerate)
    3. Apply transitions (smart, crossfade, or cuts)
    4. Generate ending screen with dominant color fade
    5. Encode final video (HEVC with HDR preservation)

Transition Types:
    - SMART: Intelligent mix of crossfades and cuts for variety
    - CROSSFADE: Smooth fade transitions between all clips
    - CUT: Hard cuts with proper re-encoding (handles codec mismatches)

All assembly methods properly handle:
    - Different input codecs (H.264, HEVC, etc.)
    - Different resolutions (auto-scaled to common resolution)
    - Different frame rates (normalized)
    - Missing audio streams (silent audio added as needed)

Example:
    ```python
    from immich_memories.processing import (
        VideoAssembler,
        AssemblySettings,
        TitleScreenSettings,
    )

    settings = AssemblySettings(
        transition=TransitionType.CROSSFADE,
        preserve_hdr=True,
        title_screens=TitleScreenSettings(year=2024),
    )

    assembler = VideoAssembler(settings)
    output = assembler.assemble(clips, Path("output.mp4"))
    ```
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

from immich_memories.config import get_config
from immich_memories.processing.assembler_audio import AssemblerAudioMixin
from immich_memories.processing.assembler_batch import AssemblerBatchMixin
from immich_memories.processing.assembler_concat import AssemblerConcatMixin
from immich_memories.processing.assembler_encoding import AssemblerEncodingMixin
from immich_memories.processing.assembler_helpers import AssemblerHelpersMixin
from immich_memories.processing.assembler_scalable import AssemblerScalableMixin
from immich_memories.processing.assembler_strategies import AssemblerStrategyMixin
from immich_memories.processing.assembler_titles import AssemblerTitleMixin
from immich_memories.processing.assembler_transition_render import AssemblerTransitionRenderMixin
from immich_memories.processing.assembler_transitions import AssemblerTransitionMixin
from immich_memories.processing.assembly_config import (
    MAX_FACE_CACHE_SIZE,
    AssemblyClip,
    AssemblySettings,
    TransitionType,
)
from immich_memories.processing.ffmpeg_prober import FFmpegProber
from immich_memories.processing.scaling_utilities import (
    _detect_face_center_in_video,
)
from immich_memories.tracking.run_database import RunDatabase

__all__ = [
    "VideoAssembler",
    "assemble_montage",
    "create_preview",
]

logger = logging.getLogger(__name__)


class VideoAssembler(
    AssemblerHelpersMixin,
    AssemblerEncodingMixin,
    AssemblerTransitionMixin,
    AssemblerTransitionRenderMixin,
    AssemblerAudioMixin,
    AssemblerConcatMixin,
    AssemblerStrategyMixin,
    AssemblerScalableMixin,
    AssemblerBatchMixin,
    AssemblerTitleMixin,
):
    """Assemble multiple clips into a final video.

    This class handles the complete video assembly pipeline including:
    - Resolution detection and normalization
    - Frame rate detection and normalization
    - Transition application (smart, crossfade, or cuts)
    - HDR metadata preservation (HEVC with BT.2020/HLG)
    - Audio mixing and normalization

    The assembly process has a robust fallback chain:
        SMART transitions -> CROSSFADE -> CUTS (with re-encoding)

    All fallbacks properly handle codec/resolution/framerate mismatches
    by re-encoding through a filter complex rather than using stream copy.

    Attributes:
        settings: AssemblySettings controlling output format and transitions.
    """

    def __init__(self, settings: AssemblySettings | None = None, run_id: str | None = None):
        """Initialize the assembler.

        Args:
            settings: Assembly settings. If None, uses defaults from config.
            run_id: Optional run ID for job tracking and cancellation support.
        """
        self.settings = settings or AssemblySettings()
        self.run_id = run_id
        self._run_db: RunDatabase | None = None
        self.prober = FFmpegProber(self.settings)

        # Face detection cache: path -> (center_x, center_y) or None
        # Using OrderedDict with size limit to prevent unbounded memory growth
        self._face_cache: OrderedDict[Path, tuple[float, float] | None] = OrderedDict()

        config = get_config()
        if self.settings.output_crf == 18:
            self.settings.output_crf = config.output.crf
        if self.settings.transition_duration == 0.5:
            self.settings.transition_duration = config.defaults.transition_duration

    def _check_cancelled(self) -> None:
        """Check if job cancellation was requested and raise if so."""
        if not self.run_id:
            return
        if self._run_db is None:
            self._run_db = RunDatabase()
        if self._run_db.is_cancel_requested(self.run_id):
            logger.info(f"Assembly job {self.run_id} cancelled by user request")
            from immich_memories.processing.assembly_config import JobCancelledException

            raise JobCancelledException(f"Job {self.run_id} cancelled")

    def _get_face_center(self, video_path: Path) -> tuple[float, float] | None:
        """Get face center for a video with caching.

        Args:
            video_path: Path to video file

        Returns:
            Tuple of (center_x, center_y) in normalized 0-1 coordinates, or None
        """
        if video_path in self._face_cache:
            # Move to end (most recently used)
            self._face_cache.move_to_end(video_path)
            return self._face_cache[video_path]

        # Evict oldest entries if cache is full
        while len(self._face_cache) >= MAX_FACE_CACHE_SIZE:
            self._face_cache.popitem(last=False)

        result = _detect_face_center_in_video(video_path)
        self._face_cache[video_path] = result
        return result

    # ------------------------------------------------------------------
    # Compatibility shims — will be removed as other mixins are refactored
    # ------------------------------------------------------------------

    def _parse_resolution_from_stream(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return self.prober.parse_resolution_from_stream(*args, **kwargs)

    def _get_video_resolution(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return self.prober.get_video_resolution(*args, **kwargs)

    def _pick_resolution_tier(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return self.prober.pick_resolution_tier(*args, **kwargs)

    def _detect_best_resolution(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return self.prober.detect_best_resolution(*args, **kwargs)

    def _probe_duration(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return self.prober.probe_duration(*args, **kwargs)

    def _probe_framerate(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return self.prober.probe_framerate(*args, **kwargs)

    def _has_audio_stream(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return self.prober.has_audio_stream(*args, **kwargs)

    def _has_video_stream(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return self.prober.has_video_stream(*args, **kwargs)

    def _probe_batch_durations(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return self.prober.probe_batch_durations(*args, **kwargs)

    @staticmethod
    def _parse_fps_str(fps_str: str) -> float | None:
        return FFmpegProber.parse_fps_str(fps_str)

    def _detect_framerate(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return self.prober.detect_framerate(*args, **kwargs)

    def _detect_max_framerate(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return self.prober.detect_max_framerate(*args, **kwargs)

    def estimate_duration(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return self.prober.estimate_duration(*args, **kwargs)

    def assemble(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble clips into a final video.

        Args:
            clips: List of clips to assemble.
            output_path: Path for output video.
            progress_callback: Progress callback (0.0 to 1.0).

        Returns:
            Path to assembled video.
        """
        if not clips:
            raise ValueError("No clips provided")

        if len(clips) == 1:
            # Single clip - just copy or add music
            return self._process_single_clip(clips[0], output_path)

        # Use new scalable assembly method for all transition types
        # This method is memory-efficient and scales to any number of clips
        result = self._assemble_scalable(clips, output_path, progress_callback)

        # Add music if specified
        if self.settings.music_path and self.settings.music_path.exists():
            result = self._add_music(result, output_path)

        return result

    def _process_single_clip(self, clip: AssemblyClip, output_path: Path) -> Path:
        """Process a single clip (add music if needed).

        Args:
            clip: The clip to process.
            output_path: Output path.

        Returns:
            Path to output video.
        """
        if self.settings.music_path and self.settings.music_path.exists():
            return self._add_music_to_clip(clip.path, output_path)
        else:
            # Just copy
            import shutil

            shutil.copy2(clip.path, output_path)
            return output_path


def assemble_montage(
    clips: list[Path],
    output_path: Path,
    transition: TransitionType = TransitionType.CROSSFADE,
    transition_duration: float = 0.5,
    music_path: Path | None = None,
    music_volume: float = 0.3,
    music_vocals_path: Path | None = None,
    music_accompaniment_path: Path | None = None,
) -> Path:
    """Convenience function to assemble a video montage.

    Args:
        clips: List of clip paths.
        output_path: Output video path.
        transition: Transition type.
        transition_duration: Transition duration in seconds.
        music_path: Optional music file path.
        music_volume: Music volume (0-1).
        music_vocals_path: Optional vocals/melody stem for ducking.
        music_accompaniment_path: Optional drums+bass stem (stays full during speech).

    Returns:
        Path to assembled video.
    """
    from immich_memories.processing.clips import get_video_duration

    # Convert paths to AssemblyClips
    assembly_clips = []
    for path in clips:
        duration = get_video_duration(path)
        assembly_clips.append(
            AssemblyClip(
                path=path,
                duration=duration,
            )
        )

    settings = AssemblySettings(
        transition=transition,
        transition_duration=transition_duration,
        music_path=music_path,
        music_volume=music_volume,
        music_vocals_path=music_vocals_path,
        music_accompaniment_path=music_accompaniment_path,
    )

    return VideoAssembler(settings).assemble(assembly_clips, output_path)


def create_preview(
    clips: list[AssemblyClip],
    output_path: Path,
    preview_duration: float = 30.0,
) -> Path:
    """Create a quick preview of the assembly.

    Only includes the first N seconds.

    Args:
        clips: List of clips.
        output_path: Output path.
        preview_duration: Maximum preview duration.

    Returns:
        Path to preview video.
    """
    # Truncate clips to fit preview duration
    preview_clips = []
    remaining_duration = preview_duration

    for clip in clips:
        if remaining_duration <= 0:
            break

        if clip.duration <= remaining_duration:
            preview_clips.append(clip)
            remaining_duration -= clip.duration
        else:
            # Truncate this clip
            from immich_memories.processing.clips import extract_clip

            truncated_path = extract_clip(
                clip.path,
                start_time=0,
                end_time=remaining_duration,
            )
            preview_clips.append(
                AssemblyClip(
                    path=truncated_path,
                    duration=remaining_duration,
                )
            )
            break

    # Assemble preview with faster settings
    settings = AssemblySettings(
        transition=TransitionType.CUT,  # Faster
        output_crf=28,  # Lower quality for speed
    )

    return VideoAssembler(settings).assemble(preview_clips, output_path)
