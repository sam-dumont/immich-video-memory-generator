"""Video assembler class and convenience functions."""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

from immich_memories.config import get_config
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
from immich_memories.processing.transition_renderer import TransitionRenderer
from immich_memories.tracking.run_database import RunDatabase

__all__ = [
    "VideoAssembler",
    "assemble_montage",
    "create_preview",
]

logger = logging.getLogger(__name__)


class VideoAssembler:
    """Assemble multiple clips into a final video.

    Composes 7 services via constructor injection:
    - FFmpegProber, FilterBuilder, ClipEncoder, AssemblyEngine
    - TransitionRenderer, AudioMixerService, TitleInserter

    Attributes:
        settings: AssemblySettings controlling output format and transitions.
    """

    def __init__(self, settings: AssemblySettings | None = None, run_id: str | None = None):
        self.settings = settings or AssemblySettings()
        self.run_id = run_id
        self._run_db: RunDatabase | None = None

        # Face detection cache: path -> (center_x, center_y) or None
        self._face_cache: OrderedDict[Path, tuple[float, float] | None] = OrderedDict()

        config = get_config()
        if self.settings.output_crf == 18:
            self.settings.output_crf = config.output.crf
        if self.settings.transition_duration == 0.5:
            self.settings.transition_duration = config.defaults.transition_duration

        # Wire composed services
        self.prober = FFmpegProber(self.settings)
        self.filter_builder = FilterBuilder(self.settings, self.prober, self._get_face_center)
        self.encoder = ClipEncoder(
            self.settings, self.prober, self.filter_builder, self._get_face_center
        )
        self.engine = AssemblyEngine(
            self.settings,
            self.prober,
            self.encoder,
            self.filter_builder,
            self._check_cancelled,
        )
        self.transitions = TransitionRenderer(self.settings, self.prober)
        self.audio_mixer = AudioMixerService(self.settings)
        self.title_inserter = TitleInserter(self.settings, self.prober)

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
    ) -> Path:
        return self.title_inserter.assemble_with_titles(
            clips, output_path, self.assemble, progress_callback
        )

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
            return self._process_single_clip(clips[0], output_path)

        result = self.engine.assemble_scalable(clips, output_path, progress_callback)

        if self.settings.music_path and self.settings.music_path.exists():
            result = self.audio_mixer.add_music(result, output_path)

        return result

    def _process_single_clip(self, clip: AssemblyClip, output_path: Path) -> Path:
        if self.settings.music_path and self.settings.music_path.exists():
            return self.audio_mixer.add_music_to_clip(clip.path, output_path)
        else:
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
    from immich_memories.processing.clips import get_video_duration

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
