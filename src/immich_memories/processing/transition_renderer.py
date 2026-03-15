"""Transition segment extraction and rendering pipeline."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from immich_memories.processing.assembly_config import AssemblySettings
from immich_memories.processing.ffmpeg_prober import VideoProber
from immich_memories.processing.hdr_utilities import (
    _detect_hdr_type,
    _get_gpu_encoder_args,
)
from immich_memories.processing.transition_blender import TransitionBlender

logger = logging.getLogger(__name__)


class TransitionRenderer:
    """Orchestrates transition rendering between clips.

    Uses TransitionBlender for frame-level work, falls back to FFmpeg xfade
    when PyAV/GPU is unavailable. Three-tier audio fallback chain.
    """

    def __init__(
        self,
        settings: AssemblySettings,
        prober: VideoProber,
    ) -> None:
        self.settings = settings
        self.prober = prober
        self.blender = TransitionBlender(settings)

    def extract_segment_for_transition(
        self,
        src: Path,
        output: Path,
        start: float,
        duration: float,
    ) -> None:
        """Extract a segment with guaranteed audio output (anullsrc mixing)."""
        video_codec_args = _get_gpu_encoder_args(
            crf=self.settings.output_crf,
            preserve_hdr=self.settings.preserve_hdr,
        )
        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"

        # Use filter_complex with anullsrc to guarantee audio even when seeking
        # causes audio/video timestamp misalignment
        filter_complex = (
            f"[0:v]trim=start={start}:duration={duration},setpts=PTS-STARTPTS[vout];"
            f"anullsrc=r=48000:cl=stereo,atrim=0:{duration}[silence];"
            f"[0:a]atrim=start={start}:duration={duration},{audio_format},"
            f"asetpts=PTS-STARTPTS,apad=whole_dur={duration}[asrc];"
            f"[silence][asrc]amix=inputs=2:duration=longest:weights='0.001 1',"
            f"atrim=0:{duration},asetpts=PTS-STARTPTS[aout]"
        )
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            *video_codec_args,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)

        if result.returncode != 0:
            logger.warning(
                f"Extraction with audio failed, retrying with silence: {result.stderr[-200:]}"
            )
            filter_complex_silent = (
                f"[0:v]trim=start={start}:duration={duration},setpts=PTS-STARTPTS[vout];"
                f"anullsrc=r=48000:cl=stereo,atrim=0:{duration},{audio_format},asetpts=PTS-STARTPTS[aout]"
            )
            cmd_silent = [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-filter_complex",
                filter_complex_silent,
                "-map",
                "[vout]",
                "-map",
                "[aout]",
                *video_codec_args,
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(output),
            ]
            result = subprocess.run(cmd_silent, capture_output=True, text=True, timeout=1800)
            if result.returncode != 0:
                raise RuntimeError(f"Failed to extract segment: {result.stderr[-500:]}")

        has_video_out = self.prober.has_video_stream(output)
        has_audio_out = self.prober.has_audio_stream(output)
        if not has_video_out or not has_audio_out:
            logger.error(
                f"Segment extraction incomplete: {output.name} - "
                f"video={has_video_out}, audio={has_audio_out}"
            )
        else:
            out_video_dur = self.prober.probe_duration(output, "video")
            out_audio_dur = self.prober.probe_duration(output, "audio")
            logger.debug(
                f"Extracted {output.name}: requested={duration:.3f}s, "
                f"actual video={out_video_dur:.3f}s, audio={out_audio_dur:.3f}s"
            )

    def render_transition_segment(
        self,
        src_a: Path,
        a_start: float,
        a_duration: float,
        src_b: Path,
        b_start: float,
        b_duration: float,
        output_path: Path,
    ) -> None:
        """Render a crossfade transition. Tries frame-by-frame first, falls back to xfade."""
        if a_start < 0:
            raise ValueError(f"Transition source A start position is negative: {a_start}")
        if b_start < 0:
            raise ValueError(f"Transition source B start position is negative: {b_start}")
        if a_duration <= 0 or b_duration <= 0:
            raise ValueError(f"Invalid duration: a={a_duration}, b={b_duration}")

        fade_dur = a_duration

        actual_a_dur = self.prober.probe_duration(src_a, "video")
        has_audio_a = self.prober.has_audio_stream(src_a)
        has_audio_b = self.prober.has_audio_stream(src_b)

        safety_margin = 0.1
        max_a_start = max(0, actual_a_dur - fade_dur - safety_margin)
        if a_start > max_a_start:
            logger.warning(
                f"Transition seek adjusted: a_start {a_start:.2f}s -> {max_a_start:.2f}s "
                f"(src_a duration: {actual_a_dur:.2f}s, fade: {fade_dur:.2f}s)"
            )
            a_start = max_a_start

        logger.debug(
            f"Rendering transition: {src_a.name}[{a_start:.2f}s] -> {src_b.name}, "
            f"fade={fade_dur:.2f}s, audio: a={has_audio_a}, b={has_audio_b}"
        )

        hdr_type = "hlg"
        if self.settings.preserve_hdr:
            hdr_a = _detect_hdr_type(src_a)
            if hdr_a:
                hdr_type = hdr_a

        video_codec_args = _get_gpu_encoder_args(
            crf=self.settings.output_crf,
            preserve_hdr=self.settings.preserve_hdr,
            hdr_type=hdr_type,
        )

        temp_dir = output_path.parent / f".trans_{output_path.stem}"
        temp_dir.mkdir(exist_ok=True)
        audio_format = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"

        try:
            seg_a = temp_dir / "seg_a.mp4"
            self.extract_segment_for_transition(src_a, seg_a, a_start, a_duration)

            seg_b = temp_dir / "seg_b.mp4"
            self.extract_segment_for_transition(src_b, seg_b, b_start, b_duration)

            seg_a_audio = self.prober.has_audio_stream(seg_a)
            seg_b_audio = self.prober.has_audio_stream(seg_b)
            logger.debug(f"Extracted segments have audio: seg_a={seg_a_audio}, seg_b={seg_b_audio}")

            # Try frame-by-frame first (precise, no stutter)
            if self.blender.render_transition_framewise(seg_a, seg_b, output_path, fade_dur):
                logger.debug("Frame-by-frame transition successful")
                return

            # Fallback to FFmpeg xfade
            logger.debug("Falling back to FFmpeg xfade for transition")
            self._render_xfade_fallback(
                seg_a,
                seg_b,
                output_path,
                fade_dur,
                video_codec_args,
                audio_format,
                seg_a_audio,
                seg_b_audio,
            )
        finally:
            if not self.settings.debug_preserve_intermediates:
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _render_xfade_fallback(
        self,
        seg_a: Path,
        seg_b: Path,
        output_path: Path,
        fade_dur: float,
        video_codec_args: list[str],
        audio_format: str,
        seg_a_audio: bool,
        seg_b_audio: bool,
    ) -> None:
        """FFmpeg xfade fallback with three-tier audio handling."""
        res_a = self.prober.get_video_resolution(seg_a)
        res_b = self.prober.get_video_resolution(seg_b)

        if res_a and res_b:
            target_w, target_h = max(res_a[0], res_b[0]), max(res_a[1], res_b[1])
        elif res_a:
            target_w, target_h = res_a
        elif res_b:
            target_w, target_h = res_b
        else:
            target_w, target_h = 1920, 1080

        scale_filter = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
        )

        # Tier 1: acrossfade
        filter_complex = (
            f"[0:v]{scale_filter}[v0scaled];"
            f"[1:v]{scale_filter}[v1scaled];"
            f"[v0scaled][v1scaled]xfade=transition=fade:duration={fade_dur}:offset=0,"
            f"trim=0:{fade_dur},setpts=PTS-STARTPTS[vout];"
            f"[0:a][1:a]acrossfade=d={fade_dur}:c1=tri:c2=tri,"
            f"atrim=0:{fade_dur},asetpts=PTS-STARTPTS[aout]"
        )
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(seg_a),
            "-i",
            str(seg_b),
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            *video_codec_args,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode == 0:
            return

        # Tier 2: amix with fades
        logger.warning(
            f"acrossfade failed (audio: a={seg_a_audio}, b={seg_b_audio}), "
            f"trying amix: {result.stderr[-200:]}"
        )
        filter_complex_fallback = (
            f"[0:v]{scale_filter}[v0scaled];"
            f"[1:v]{scale_filter}[v1scaled];"
            f"[v0scaled][v1scaled]xfade=transition=fade:duration={fade_dur}:offset=0,"
            f"trim=0:{fade_dur},setpts=PTS-STARTPTS[vout];"
            f"anullsrc=r=48000:cl=stereo,atrim=0:{fade_dur}[silence];"
            f"[0:a]{audio_format},afade=t=out:st=0:d={fade_dur}[afade_a];"
            f"[1:a]{audio_format},afade=t=in:st=0:d={fade_dur}[afade_b];"
            f"[afade_a][afade_b]amix=inputs=2:duration=first[amixed];"
            f"[silence][amixed]amix=inputs=2:duration=first:weights='0 1',"
            f"atrim=0:{fade_dur},asetpts=PTS-STARTPTS[aout]"
        )
        cmd_fallback = [
            "ffmpeg",
            "-y",
            "-i",
            str(seg_a),
            "-i",
            str(seg_b),
            "-filter_complex",
            filter_complex_fallback,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            *video_codec_args,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        result = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=1800)
        if result.returncode == 0:
            return

        # Tier 3: silent audio
        logger.warning(f"amix fallback also failed, using silent audio: {result.stderr[-200:]}")
        filter_complex_silent = (
            f"[0:v]{scale_filter}[v0scaled];"
            f"[1:v]{scale_filter}[v1scaled];"
            f"[v0scaled][v1scaled]xfade=transition=fade:duration={fade_dur}:offset=0,"
            f"trim=0:{fade_dur},setpts=PTS-STARTPTS[vout];"
            f"anullsrc=r=48000:cl=stereo,atrim=0:{fade_dur},{audio_format},"
            f"asetpts=PTS-STARTPTS[aout]"
        )
        cmd_silent = [
            "ffmpeg",
            "-y",
            "-i",
            str(seg_a),
            "-i",
            str(seg_b),
            "-filter_complex",
            filter_complex_silent,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            *video_codec_args,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-t",
            str(fade_dur),
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        result = subprocess.run(cmd_silent, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to render transition: {result.stderr[-500:]}")
