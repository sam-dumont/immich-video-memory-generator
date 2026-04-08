"""Video assembler class and convenience functions."""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.processing.assembly_config import (
    MAX_FACE_CACHE_SIZE,
    AssemblyClip,
    AssemblySettings,
    TransitionType,
)
from immich_memories.processing.assembly_engine import AssemblyEngine
from immich_memories.processing.audio_mixer_service import AudioMixerService
from immich_memories.processing.clip_encoder import ClipEncoder
from immich_memories.processing.ffmpeg_prober import FFmpegProber
from immich_memories.processing.filter_builder import FilterBuilder
from immich_memories.processing.scaling_utilities import (
    _detect_face_center_in_video,
)
from immich_memories.processing.title_inserter import TitleInserter

if TYPE_CHECKING:
    from immich_memories.config_loader import Config

__all__ = [
    "VideoAssembler",
    "assemble_montage",
    "create_preview",
]

logger = logging.getLogger(__name__)


class VideoAssembler:
    """Assemble multiple clips into a final video.

    Composes 6 services via constructor injection:
    - FFmpegProber, FilterBuilder, ClipEncoder, AssemblyEngine
    - AudioMixerService, TitleInserter

    Attributes:
        settings: AssemblySettings controlling output format and transitions.
    """

    def __init__(
        self,
        settings: AssemblySettings | None = None,
        *,
        output_crf: int = 23,
        default_transition_duration: float = 0.5,
        default_resolution: tuple[int, int] = (1920, 1080),
    ):
        self.settings = settings or AssemblySettings()

        # Face detection cache: path -> (center_x, center_y) or None
        self._face_cache: OrderedDict[Path, tuple[float, float] | None] = OrderedDict()

        # Apply caller-provided defaults where settings left them unset
        if self.settings.output_crf is None:
            self.settings.output_crf = output_crf
        if self.settings.transition_duration is None:
            self.settings.transition_duration = default_transition_duration
        if self.settings.default_resolution is None:
            self.settings.default_resolution = default_resolution

        # Wire composed services
        self.prober = FFmpegProber(self.settings)
        self.filter_builder = FilterBuilder(self.settings, self.prober, self._get_face_center)
        self.encoder = ClipEncoder(
            self.settings,
            self.prober,
            self._get_face_center,
            default_resolution=self.settings.default_resolution,
        )
        self.engine = AssemblyEngine(
            self.settings,
            self.prober,
            self.encoder,
            self.filter_builder,
        )
        self.audio_mixer = AudioMixerService(self.settings)
        self.title_inserter = TitleInserter(self.settings, self.prober)

    def _get_face_center(self, video_path: Path) -> tuple[float, float] | None:
        if video_path in self._face_cache:
            self._face_cache.move_to_end(video_path)
            return self._face_cache[video_path]

        while len(self._face_cache) >= MAX_FACE_CACHE_SIZE:
            self._face_cache.popitem(last=False)

        result = _detect_face_center_in_video(video_path)
        self._face_cache[video_path] = result
        return result

    def assemble_with_titles(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
        frame_preview_callback: Callable[[bytes], None] | None = None,
    ) -> Path:
        def wrapped_assemble(clips, output_path, progress_callback=None):
            return self.assemble(clips, output_path, progress_callback, frame_preview_callback)

        return self.title_inserter.assemble_with_titles(
            clips, output_path, wrapped_assemble, progress_callback
        )

    def assemble(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
        frame_preview_callback: Callable[[bytes], None] | None = None,
    ) -> Path:
        """Assemble clips into a final video.

        Args:
            clips: List of clips to assemble.
            output_path: Path for output video.
            progress_callback: Progress callback (0.0 to 1.0).
            frame_preview_callback: Receives JPEG bytes for live preview.

        Returns:
            Path to assembled video.
        """
        if not clips:
            raise ValueError("No clips provided")

        if len(clips) == 1:
            return self._process_single_clip(clips[0], output_path)

        result = self.engine.assemble_scalable(
            clips, output_path, progress_callback, frame_preview_callback
        )

        if self.settings.music_path and self.settings.music_path.exists():
            result = self.audio_mixer.add_music(result, output_path)

        return result

    def _process_single_clip(self, clip: AssemblyClip, output_path: Path) -> Path:
        needs_processing = (
            self.settings.privacy_mode
            or (not self.settings.auto_resolution and self.settings.target_resolution)
            or (clip.rotation_override is not None and clip.rotation_override != 0)
        )

        if needs_processing:
            # Single clip still needs FFmpeg for filters (blur, resize, rotation)
            return self.engine.assemble_scalable([clip], output_path)

        if self.settings.music_path and self.settings.music_path.exists():
            return self.audio_mixer.add_music_to_clip(clip.path, output_path)

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
    from immich_memories.processing.clip_probing import get_video_duration

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
    *,
    config: Config,
) -> Path:
    preview_clips = []
    remaining_duration = preview_duration

    for clip in clips:
        if remaining_duration <= 0:
            break

        if clip.duration <= remaining_duration:
            preview_clips.append(clip)
            remaining_duration -= clip.duration
        else:
            from immich_memories.processing.clips import extract_clip

            truncated_path = extract_clip(
                clip.path,
                start_time=0,
                end_time=remaining_duration,
                config=config,
            )
            preview_clips.append(
                AssemblyClip(
                    path=truncated_path,
                    duration=remaining_duration,
                )
            )
            break

    settings = AssemblySettings(
        transition=TransitionType.CUT,
        output_crf=28,
    )

    return VideoAssembler(settings).assemble(preview_clips, output_path)
