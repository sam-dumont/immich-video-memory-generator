"""Batch assembly methods for VideoAssembler.

This mixin provides methods for merging intermediate batch files
and direct batch assembly without chunking recursion.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from immich_memories.processing.assembly_config import AssemblyClip

logger = logging.getLogger(__name__)


class AssemblerBatchMixin:
    """Mixin providing batch merge and direct assembly methods."""

    def _merge_intermediate_batches(
        self,
        batches: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Merge intermediate batch files using probed durations for audio sync.

        This method probes the ACTUAL duration from each intermediate file rather
        than trusting the declared duration, which fixes audio desync issues caused
        by AAC frame alignment and encoding artifacts.

        Args:
            batches: List of intermediate batch clips.
            output_path: Output video path.
            progress_callback: Progress callback.

        Returns:
            Path to assembled video.
        """
        if len(batches) < 2:
            if len(batches) == 1:
                import shutil

                shutil.copy2(batches[0].path, output_path)
                return output_path
            raise ValueError("No batches to merge")

        audio_durations, video_durations = self._probe_batch_durations(batches)
        logger.info(
            f"Merging {len(batches)} batches - "
            f"audio: {[f'{d:.2f}s' for d in audio_durations]}, "
            f"video: {[f'{d:.2f}s' for d in video_durations]}"
        )

        target_w, target_h = self._resolve_target_resolution(batches)
        ctx = self._create_assembly_context(batches, target_w, target_h)

        inputs: list[str] = []
        for batch in batches:
            inputs.extend(["-i", str(batch.path)])

        filter_parts: list[str] = [
            self._build_clip_video_filter(i, batch, ctx, use_aspect_ratio_handling=False)
            for i, batch in enumerate(batches)
        ]

        audio_filter_parts, audio_labels = self._build_probed_audio_filters(
            batches,
            audio_durations,
        )
        filter_parts.extend(audio_filter_parts)

        xfade_parts, final_video, final_audio, _ = self._build_probed_xfade_chain(
            batches,
            video_durations,
            ctx.fade_duration,
            ctx.target_fps,
            audio_labels,
        )
        filter_parts.extend(xfade_parts)

        filter_complex = ";".join(filter_parts)

        result = self._run_ffmpeg_assembly(
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
            error_msg = self._log_ffmpeg_error(result)
            raise RuntimeError(f"FFmpeg batch merge failed (code {result.returncode}): {error_msg}")

        return output_path

    def _assemble_batch_direct(
        self,
        clips: list[AssemblyClip],
        output_path: Path,
        progress_callback: Callable[[float, str], None] | None = None,
    ) -> Path:
        """Assemble a batch of clips directly without chunking check.

        This is the core xfade assembly logic, extracted to avoid infinite
        recursion when _assemble_chunked calls back into crossfade assembly.

        Args:
            clips: List of clips to assemble (should be <= CHUNK_SIZE).
            output_path: Output video path.
            progress_callback: Progress callback.

        Returns:
            Path to assembled video.
        """
        if len(clips) < 2:
            if len(clips) == 1:
                import shutil

                shutil.copy2(clips[0].path, output_path)
                return output_path
            raise ValueError("No clips to assemble")

        target_w, target_h = self._resolve_target_resolution(clips)
        ctx = self._create_assembly_context(clips, target_w, target_h)

        inputs: list[str] = []
        for clip in clips:
            inputs.extend(["-i", str(clip.path)])

        filter_parts: list[str] = [
            self._build_clip_video_filter(i, clip, ctx) for i, clip in enumerate(clips)
        ]

        audio_filter_parts, audio_labels = self._build_audio_prep_filters(clips)
        filter_parts.extend(audio_filter_parts)

        xfade_parts, final_video, final_audio, _ = self._build_xfade_chain(
            clips,
            ctx,
            audio_labels,
        )
        filter_parts.extend(xfade_parts)

        filter_complex = ";".join(filter_parts)

        result = self._run_ffmpeg_assembly(
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
            error_msg = self._log_ffmpeg_error(result)
            raise RuntimeError(
                f"FFmpeg batch assembly failed (code {result.returncode}): {error_msg}"
            )

        return output_path
