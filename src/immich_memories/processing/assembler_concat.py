"""Concatenation and xfade chain methods for VideoAssembler.

This mixin provides methods for concatenating video segments with inline
trimming, stream copy concatenation, and xfade chain assembly.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from immich_memories.processing.hdr_utilities import (
    _get_gpu_encoder_args,
)

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


class AssemblerConcatMixin:
    """Mixin providing concatenation methods for VideoAssembler."""

    def _concat_with_inline_trim(
        self,
        encoded_clips: list[Path],
        clip_durations: list[float],
        transitions: list[str],
        transition_segments: dict[int, Path],
        fade: float,
        output_path: Path,
    ) -> None:
        """Concatenate clips and transitions with inline trimming.

        Instead of separately re-encoding trimmed clips (which creates frame
        mismatches at boundaries), this method trims clips during decode in
        the concat filter. Transition segments are passed through as-is.

        The concat filter decodes all inputs and re-encodes once, producing
        a single clean output with consistent HEVC headers.

        Args:
            encoded_clips: Pre-encoded clip paths.
            clip_durations: Duration of each clip.
            transitions: "fade" or "cut" for each boundary.
            transition_segments: Map of boundary index to transition path.
            fade: Fade duration in seconds.
            output_path: Output path.
        """
        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"

        # Build the list of inputs and corresponding filter operations.
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
            has_audio = self._has_audio_stream(encoded_clip)
            filter_parts.extend(
                _build_clip_trim_filters(
                    idx, clip_dur, trim_start, trim_dur, needs_trim, has_audio, audio_format
                )
            )

            concat_labels_v.append(f"[v{idx}]")
            concat_labels_a.append(f"[a{idx}]")

            # Add transition segment if present
            if i in transition_segments:
                input_files.append(transition_segments[i])
                t_idx = input_idx
                input_idx += 1

                filter_parts.append(f"[{t_idx}:v]setpts=PTS-STARTPTS[v{t_idx}]")
                filter_parts.append(f"[{t_idx}:a]{audio_format},asetpts=PTS-STARTPTS[a{t_idx}]")

                concat_labels_v.append(f"[v{t_idx}]")
                concat_labels_a.append(f"[a{t_idx}]")

        # Build concat filter
        n_segments = len(concat_labels_v)
        concat_input = "".join(
            f"{concat_labels_v[i]}{concat_labels_a[i]}" for i in range(n_segments)
        )
        filter_parts.append(f"{concat_input}concat=n={n_segments}:v=1:a=1[vout][aout]")

        filter_complex = ";".join(filter_parts)

        # Build inputs
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

    def _concat_with_copy(self, segments: list[Path], output_path: Path) -> None:
        """Concatenate video segments using the concat filter (decode + re-encode).

        Uses FFmpeg's concat filter (-filter_complex) which fully decodes all
        inputs before concatenating and re-encoding. This guarantees a single
        consistent HEVC SPS/PPS header throughout the output, eliminating the
        garbled frames that occur when the concat demuxer pastes together
        segments with different encoder parameters.

        Slower than stream copy but produces clean, artifact-free output.

        Args:
            segments: List of segment paths to concatenate.
            output_path: Output path for concatenated video.
        """
        n = len(segments)
        if n == 0:
            raise ValueError("No segments to concatenate")

        if n == 1:
            import shutil

            shutil.copy2(segments[0], output_path)
            return

        # Build inputs
        inputs: list[str] = []
        for seg in segments:
            inputs.extend(["-i", str(seg)])

        # Build concat filter: [0:v][0:a][1:v][1:a]...concat=n=N:v=1:a=1
        filter_parts = []
        for i in range(n):
            filter_parts.append(f"[{i}:v]")
            filter_parts.append(f"[{i}:a]")
        filter_parts.append(f"concat=n={n}:v=1:a=1[vout][aout]")
        filter_complex = "".join(filter_parts)

        # Get encoder args matching the rest of the pipeline
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

    def _assemble_xfade_chain(
        self,
        encoded_clips: list[Path],
        clip_durations: list[float],
        transitions: list[str],
        fade: float,
        output_path: Path,
    ) -> None:
        """Assemble pre-encoded clips using a single xfade filter chain.

        All clips are already normalized (same resolution, fps, pixel format).
        This method builds a single FFmpeg filter_complex that chains xfade
        operations, ensuring frame-perfect transitions with no decode/re-encode
        mismatches at boundaries.

        For cuts, clips are concatenated directly via the concat filter.
        For fades, xfade is used with the overlap handled inline.

        Args:
            encoded_clips: Pre-encoded clip paths.
            clip_durations: Duration of each clip in seconds.
            transitions: "fade" or "cut" for each boundary.
            fade: Fade duration in seconds.
            output_path: Output path.
        """
        n = len(encoded_clips)
        if n == 1:
            import shutil

            shutil.copy2(encoded_clips[0], output_path)
            return

        # Build inputs
        inputs: list[str] = []
        for clip in encoded_clips:
            inputs.extend(["-i", str(clip)])

        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        filter_parts = []

        # Prepare audio streams (normalize format, ensure exact duration)
        for i in range(n):
            has_audio = self._has_audio_stream(encoded_clips[i])
            if has_audio:
                filter_parts.append(f"[{i}:a]{audio_format},asetpts=PTS-STARTPTS[a{i}]")
            else:
                filter_parts.append(
                    f"anullsrc=r=48000:cl=stereo,atrim=0:{clip_durations[i]},{audio_format}[a{i}]"
                )

        # Build xfade chain for video, acrossfade chain for audio
        current_video = f"[{0}:v]"
        current_audio = f"[a{0}]"
        # Track cumulative offset for xfade timing
        cumulative_duration = clip_durations[0]

        for i in range(n - 1):
            next_idx = i + 1
            next_video = f"[{next_idx}:v]"
            next_audio = f"[a{next_idx}]"

            if transitions[i] == "fade":
                # xfade offset = cumulative duration minus fade duration
                offset = cumulative_duration - fade
                v_out = f"[vx{i}]"
                a_out = f"[ax{i}]"

                filter_parts.append(
                    f"{current_video}{next_video}xfade=transition=fade:"
                    f"duration={fade}:offset={offset}{v_out}"
                )
                filter_parts.append(
                    f"{current_audio}{next_audio}acrossfade=d={fade}:c1=tri:c2=tri{a_out}"
                )

                current_video = v_out
                current_audio = a_out
                # Next clip's content starts after the overlap
                cumulative_duration = offset + clip_durations[next_idx]
            else:
                # Cut transition: concat the two segments
                v_out = f"[vc{i}]"
                a_out = f"[ac{i}]"

                filter_parts.append(f"{current_video}{next_video}concat=n=2:v=1:a=0{v_out}")
                filter_parts.append(f"{current_audio}{next_audio}concat=n=2:v=0:a=1{a_out}")

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
