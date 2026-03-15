"""Concat, xfade chain, and batch merge helpers for AssemblyEngine.

These methods handle the FFmpeg-level concatenation operations:
inline-trim concat, stream-copy concat, xfade chain assembly,
and intermediate batch merging.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from immich_memories.processing.assembly_config import (
    AssemblyClip,
    AssemblySettings,
)
from immich_memories.processing.assembly_context_builder import (
    create_assembly_context,
    resolve_target_resolution,
)
from immich_memories.processing.clip_encoder import log_ffmpeg_error
from immich_memories.processing.hdr_utilities import (
    _get_gpu_encoder_args,
)

if TYPE_CHECKING:
    from immich_memories.processing.clip_encoder import ClipEncoder
    from immich_memories.processing.ffmpeg_prober import VideoProber
    from immich_memories.processing.filter_builder import FilterBuilder

logger = logging.getLogger(__name__)


def _build_clip_trim_filters(
    idx: int,
    clip_dur: float,
    trim_start: float,
    trim_dur: float,
    needs_trim: bool,
    has_audio: bool,
    audio_format: str,
) -> list[str]:
    """Build video and audio filter strings for a single clip (with or without trim)."""
    parts: list[str] = []
    if needs_trim:
        parts.append(
            f"[{idx}:v]trim=start={trim_start}:duration={trim_dur},setpts=PTS-STARTPTS[v{idx}]"
        )
        if has_audio:
            parts.append(
                f"[{idx}:a]atrim=start={trim_start}:duration={trim_dur},"
                f"{audio_format},asetpts=PTS-STARTPTS[a{idx}]"
            )
        else:
            parts.append(
                f"anullsrc=r=48000:cl=stereo,atrim=0:{trim_dur},{audio_format},"
                f"asetpts=PTS-STARTPTS[a{idx}]"
            )
    else:
        parts.append(f"[{idx}:v]setpts=PTS-STARTPTS[v{idx}]")
        if has_audio:
            parts.append(f"[{idx}:a]{audio_format},asetpts=PTS-STARTPTS[a{idx}]")
        else:
            parts.append(
                f"anullsrc=r=48000:cl=stereo,atrim=0:{clip_dur},{audio_format},"
                f"asetpts=PTS-STARTPTS[a{idx}]"
            )
    return parts


class ConcatService:
    """Concat, xfade chain, and batch merge operations for video assembly."""

    def __init__(
        self,
        settings: AssemblySettings,
        prober: VideoProber,
        encoder: ClipEncoder,
        filter_builder: FilterBuilder,
    ) -> None:
        self.settings = settings
        self.prober = prober
        self.encoder = encoder
        self.filter_builder = filter_builder

    def concat_with_inline_trim(
        self,
        encoded_clips: list[Path],
        clip_durations: list[float],
        transitions: list[str],
        transition_segments: dict[int, Path],
        fade: float,
        output_path: Path,
    ) -> None:
        """Concatenate clips and transitions with inline trimming."""
        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"

        input_files: list[Path] = []
        filter_parts: list[str] = []
        concat_labels_v: list[str] = []
        concat_labels_a: list[str] = []
        input_idx = 0

        for i, encoded_clip in enumerate(encoded_clips):
            clip_dur = clip_durations[i]
            is_first = i == 0
            is_last = i == len(encoded_clips) - 1

            prev_is_fade = not is_first and transitions[i - 1] == "fade"
            next_is_fade = not is_last and transitions[i] == "fade"

            trim_start = fade if prev_is_fade else 0
            trim_end = clip_dur - fade if next_is_fade else clip_dur
            trim_dur = trim_end - trim_start

            input_files.append(encoded_clip)
            idx = input_idx
            input_idx += 1

            needs_trim = trim_start > 0 or trim_end < clip_dur
            has_audio = self.prober.has_audio_stream(encoded_clip)
            filter_parts.extend(
                _build_clip_trim_filters(
                    idx, clip_dur, trim_start, trim_dur, needs_trim, has_audio, audio_format
                )
            )

            concat_labels_v.append(f"[v{idx}]")
            concat_labels_a.append(f"[a{idx}]")

            if i in transition_segments:
                input_files.append(transition_segments[i])
                t_idx = input_idx
                input_idx += 1

                filter_parts.extend(
                    (
                        f"[{t_idx}:v]setpts=PTS-STARTPTS[v{t_idx}]",
                        f"[{t_idx}:a]{audio_format},asetpts=PTS-STARTPTS[a{t_idx}]",
                    )
                )

                concat_labels_v.append(f"[v{t_idx}]")
                concat_labels_a.append(f"[a{t_idx}]")

        n_segments = len(concat_labels_v)
        concat_input = "".join(
            f"{concat_labels_v[i]}{concat_labels_a[i]}" for i in range(n_segments)
        )
        filter_parts.append(f"{concat_input}concat=n={n_segments}:v=1:a=1[vout][aout]")

        filter_complex = ";".join(filter_parts)

        inputs: list[str] = []
        for f in input_files:
            inputs.extend(["-i", str(f)])

        video_codec_args = _get_gpu_encoder_args(
            crf=self.settings.output_crf,
            preserve_hdr=self.settings.preserve_hdr,
        )

        cmd = [
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            *video_codec_args,
            "-r",
            "60",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-max_muxing_queue_size",
            "4096",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        logger.info(
            f"Concat with inline trim: {len(encoded_clips)} clips + "
            f"{len(transition_segments)} transitions = {n_segments} segments"
        )
        logger.debug(f"Filter ({len(filter_complex)} chars): {filter_complex[:300]}...")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
        if result.returncode != 0:
            raise RuntimeError(f"Concat with inline trim failed: {result.stderr[-500:]}")

    def concat_with_copy(self, segments: list[Path], output_path: Path) -> None:
        """Concatenate video segments using the concat filter (decode + re-encode)."""
        n = len(segments)
        if n == 0:
            raise ValueError("No segments to concatenate")

        if n == 1:
            shutil.copy2(segments[0], output_path)
            return

        inputs: list[str] = []
        for seg in segments:
            inputs.extend(["-i", str(seg)])

        filter_parts: list[str] = []
        for i in range(n):
            filter_parts.extend((f"[{i}:v]", f"[{i}:a]"))
        filter_parts.append(f"concat=n={n}:v=1:a=1[vout][aout]")
        filter_complex = "".join(filter_parts)

        video_codec_args = _get_gpu_encoder_args(
            crf=self.settings.output_crf,
            preserve_hdr=self.settings.preserve_hdr,
        )

        cmd = [
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            *video_codec_args,
            "-r",
            "60",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        logger.info(f"Concat filter: {n} segments -> {output_path.name}")
        logger.debug(f"Concat command: {' '.join(cmd)}")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to concatenate: {result.stderr[-500:]}")

    def assemble_xfade_chain(
        self,
        encoded_clips: list[Path],
        clip_durations: list[float],
        transitions: list[str],
        fade: float,
        output_path: Path,
    ) -> None:
        """Assemble pre-encoded clips using a single xfade filter chain."""
        n = len(encoded_clips)
        if n == 1:
            shutil.copy2(encoded_clips[0], output_path)
            return

        inputs: list[str] = []
        for clip in encoded_clips:
            inputs.extend(["-i", str(clip)])

        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        filter_parts = []

        for i in range(n):
            has_audio = self.prober.has_audio_stream(encoded_clips[i])
            if has_audio:
                filter_parts.append(f"[{i}:a]{audio_format},asetpts=PTS-STARTPTS[a{i}]")
            else:
                filter_parts.append(
                    f"anullsrc=r=48000:cl=stereo,atrim=0:{clip_durations[i]},{audio_format}[a{i}]"
                )

        current_video = f"[{0}:v]"
        current_audio = f"[a{0}]"
        cumulative_duration = clip_durations[0]

        for i in range(n - 1):
            next_idx = i + 1
            next_video = f"[{next_idx}:v]"
            next_audio = f"[a{next_idx}]"

            if transitions[i] == "fade":
                offset = cumulative_duration - fade
                v_out = f"[vx{i}]"
                a_out = f"[ax{i}]"

                filter_parts.extend(
                    (
                        f"{current_video}{next_video}xfade=transition=fade:"
                        f"duration={fade}:offset={offset}{v_out}",
                        f"{current_audio}{next_audio}acrossfade=d={fade}:c1=tri:c2=tri{a_out}",
                    )
                )

                current_video = v_out
                current_audio = a_out
                cumulative_duration = offset + clip_durations[next_idx]
            else:
                v_out = f"[vc{i}]"
                a_out = f"[ac{i}]"

                filter_parts.extend(
                    (
                        f"{current_video}{next_video}concat=n=2:v=1:a=0{v_out}",
                        f"{current_audio}{next_audio}concat=n=2:v=0:a=1{a_out}",
                    )
                )

                current_video = v_out
                current_audio = a_out
                cumulative_duration += clip_durations[next_idx]

        filter_complex = ";".join(filter_parts)

        video_codec_args = _get_gpu_encoder_args(
            crf=self.settings.output_crf,
            preserve_hdr=self.settings.preserve_hdr,
        )

        cmd = [
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            current_video,
            "-map",
            current_audio,
            *video_codec_args,
            "-r",
            "60",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        logger.info(f"Xfade assembly: {n} clips, filter length: {len(filter_complex)}")
        logger.debug(f"Filter: {filter_complex[:500]}...")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"Xfade assembly failed: {result.stderr[-500:]}")

        logger.info(f"Assembly complete: {output_path}")

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

        target_w, target_h = resolve_target_resolution(self.settings, self.prober, batches)
        ctx = create_assembly_context(self.settings, self.prober, batches, target_w, target_h)

        inputs: list[str] = []
        for batch in batches:
            inputs.extend(["-i", str(batch.path)])

        filter_parts: list[str] = [
            self.filter_builder.build_clip_video_filter(
                i, batch, ctx, use_aspect_ratio_handling=False
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
