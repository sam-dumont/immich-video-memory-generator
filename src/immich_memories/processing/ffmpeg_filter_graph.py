"""Batch merge helpers for AssemblyEngine.

These methods handle the FFmpeg-level concatenation operations:
intermediate batch merging and direct batch assembly.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
)
from immich_memories.processing.clip_encoder import log_ffmpeg_error

if TYPE_CHECKING:
    from immich_memories.processing.clip_encoder import ClipEncoder
    from immich_memories.processing.ffmpeg_prober import FFmpegProber
    from immich_memories.processing.filter_builder import FilterBuilder

logger = logging.getLogger(__name__)


class ConcatService:
    """Batch merge and direct assembly operations for video assembly."""

    def __init__(
        self,
        settings: AssemblySettings,
        prober: FFmpegProber,
        encoder: ClipEncoder,
        filter_builder: FilterBuilder,
    ) -> None:
        self.settings = settings
        self.prober = prober
        self.encoder = encoder
        self.filter_builder = filter_builder

    def merge_intermediate_batches(
        self,
        batches: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Merge intermediate batch files using probed durations for audio sync."""
        if len(batches) < 2:
            if len(batches) == 1:
                shutil.copy2(batches[0].path, output_path)
                return output_path
            raise ValueError("No batches to merge")

        audio_durations, video_durations = self.prober.probe_batch_durations(batches)
        logger.info(
            f"Merging {len(batches)} batches - "
            f"audio: {[f'{d:.2f}s' for d in audio_durations]}, "
            f"video: {[f'{d:.2f}s' for d in video_durations]}"
        )

        from immich_memories.processing.assembly_engine import (
            create_assembly_context,
            resolve_target_resolution,
        )

        target_w, target_h = resolve_target_resolution(self.settings, self.prober, batches)
        ctx = create_assembly_context(self.settings, self.prober, batches, target_w, target_h)

        inputs: list[str] = []
        for batch in batches:
            inputs.extend(["-i", str(batch.path)])

        # WHY: skip_privacy_blur=True — batch intermediates already have blur baked in
        filter_parts: list[str] = [
            self.filter_builder.build_clip_video_filter(
                i, batch, ctx, use_aspect_ratio_handling=False, skip_privacy_blur=True
            )
            for i, batch in enumerate(batches)
        ]

        audio_filter_parts, audio_labels = self.filter_builder.build_probed_audio_filters(
            batches,
            audio_durations,
        )
        filter_parts.extend(audio_filter_parts)

        xfade_parts, final_video, final_audio, _ = self.filter_builder.build_probed_xfade_chain(
            batches,
            video_durations,
            ctx.fade_duration,
            ctx.target_fps,
            audio_labels,
        )
        filter_parts.extend(xfade_parts)

        filter_complex = ";".join(filter_parts)

        result = self.encoder.run_ffmpeg_assembly(
            inputs,
            filter_complex,
            final_video,
            final_audio,
            output_path,
            batches,
            ctx,
            progress_callback,
        )

        if result.returncode != 0:
            error_msg = log_ffmpeg_error(result)
            raise RuntimeError(f"FFmpeg batch merge failed (code {result.returncode}): {error_msg}")

        return output_path

    def assemble_batch_direct(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble a batch of clips directly without chunking check."""
        if len(clips) < 2:
            if len(clips) == 1:
                shutil.copy2(clips[0].path, output_path)
                return output_path
            raise ValueError("No clips to assemble")

        from immich_memories.processing.assembly_engine import (
            create_assembly_context,
            resolve_target_resolution,
        )

        target_w, target_h = resolve_target_resolution(self.settings, self.prober, clips)
        ctx = create_assembly_context(self.settings, self.prober, clips, target_w, target_h)

        inputs: list[str] = []
        for clip in clips:
            inputs.extend(["-i", str(clip.path)])

        filter_parts: list[str] = [
            self.filter_builder.build_clip_video_filter(i, clip, ctx)
            for i, clip in enumerate(clips)
        ]

        audio_filter_parts, audio_labels = self.filter_builder.build_audio_prep_filters(clips)
        filter_parts.extend(audio_filter_parts)

        xfade_parts, final_video, final_audio, _ = self.filter_builder.build_xfade_chain(
            clips,
            ctx,
            audio_labels,
        )
        filter_parts.extend(xfade_parts)

        filter_complex = ";".join(filter_parts)

        result = self.encoder.run_ffmpeg_assembly(
            inputs,
            filter_complex,
            final_video,
            final_audio,
            output_path,
            clips,
            ctx,
            progress_callback,
        )

        if result.returncode != 0:
            error_msg = log_ffmpeg_error(result)
            raise RuntimeError(
                f"FFmpeg batch assembly failed (code {result.returncode}): {error_msg}"
            )

        return output_path
